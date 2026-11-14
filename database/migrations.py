"""Versioned SQLite migrations with pre-migration backups.

The application historically created its schema with ``CREATE TABLE IF NOT
EXISTS`` from ``schema.sql``.  That works for fresh databases but cannot add
columns to an existing one.  This module adds idempotent, versioned migrations
and takes a backup of the database before the first structural change.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable, List

from config.settings import (
    BACKUP_DIR,
    DATABASE_PATH,
    DEFAULT_CHAPTER_TARGET_WORDS,
    DEFAULT_MIN_CHAPTER_WORDS,
    DEFAULT_REQUIRED_CHAPTER_COUNT,
)

logger = logging.getLogger(__name__)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if not _table_exists(conn, table):
        return
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migration_001_evidence_linking(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunity_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER NOT NULL,
            source_id INTEGER,
            claim_field TEXT NOT NULL,
            claim_text TEXT,
            excerpt TEXT,
            relevance_score REAL DEFAULT 0.0,
            verified INTEGER DEFAULT 0,
            verification_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES research_sources(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_opportunity_evidence_opp "
        "ON opportunity_evidence(opportunity_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_opportunity_evidence_source "
        "ON opportunity_evidence(source_id)"
    )
    _add_column(conn, "opportunities", "evidence_status", "TEXT DEFAULT 'HYPOTHESIS'")
    _add_column(conn, "opportunities", "evidence_summary", "TEXT")
    _add_column(conn, "opportunities", "citation_ids_json", "TEXT DEFAULT '[]'")
    # Legacy opportunities were scored by source count, not by linked evidence.
    # Reclassify them honestly instead of deleting user data.
    conn.execute(
        """
        UPDATE opportunities
        SET evidence_status = 'LEGACY_UNVERIFIED',
            evidence_summary = COALESCE(evidence_summary, 'Created before evidence linking; no verified claim links.')
        WHERE id NOT IN (SELECT DISTINCT opportunity_id FROM opportunity_evidence)
        """
    )


def _migration_002_generation_contracts(conn: sqlite3.Connection) -> None:
    _add_column(conn, "product_charters", "product_format", "TEXT DEFAULT 'Ebook'")
    _add_column(conn, "transformation_maps", "product_format", "TEXT DEFAULT 'Ebook'")
    _add_column(conn, "product_outlines", "product_format", "TEXT DEFAULT 'Ebook'")
    _add_column(conn, "chapters", "target_word_count", "INTEGER DEFAULT 2200")
    _add_column(conn, "chapters", "validation_status", "TEXT DEFAULT 'PENDING'")
    _add_column(conn, "chapters", "validation_json", "TEXT")


def _migration_003_audit_and_exports(conn: sqlite3.Connection) -> None:
    _add_column(conn, "quality_reports", "deterministic_checks_json", "TEXT")
    _add_column(conn, "quality_reports", "issues_json", "TEXT")
    _add_column(conn, "quality_reports", "human_review_required", "INTEGER DEFAULT 0")
    _add_column(conn, "exports", "completeness_status", "TEXT DEFAULT 'WORKING_DRAFT'")
    _add_column(conn, "exports", "completeness_json", "TEXT")


def _migration_004_p1_trust_backup_autosave(conn: sqlite3.Connection) -> None:
    """P1 schema: source trust, score/evidence separation, chunks, autosave, backups."""
    # --- Source trust -------------------------------------------------
    _add_column(conn, "research_sources", "declared_reliability", "TEXT DEFAULT 'UNASSESSED'")
    _add_column(conn, "research_sources", "assessed_reliability", "TEXT DEFAULT 'UNASSESSED'")
    _add_column(conn, "research_sources", "assessment_score", "REAL DEFAULT 0.0")
    _add_column(conn, "research_sources", "assessment_json", "TEXT")
    _add_column(conn, "research_sources", "assessed_at", "TIMESTAMP")
    if _table_exists(conn, "research_sources"):
        # Preserve a legacy reliability label as the user's *declaration* only.
        # The column was just added with a default, so it cannot be trusted as a
        # signal; prefer the legacy reliability_assessment when it holds a real
        # label.  It is still never promoted to an independent assessment.
        conn.execute(
            """
            UPDATE research_sources
            SET declared_reliability = UPPER(TRIM(reliability_assessment))
            WHERE reliability_assessment IS NOT NULL
              AND UPPER(TRIM(reliability_assessment)) IN ('HIGH', 'MEDIUM', 'LOW')
            """
        )
        conn.execute(
            "UPDATE research_sources SET assessed_reliability = 'UNASSESSED' "
            "WHERE assessed_reliability IS NULL OR assessed_reliability = ''"
        )
        # Never let the legacy declaration masquerade as an independent
        # assessment in older code paths.
        conn.execute(
            "UPDATE research_sources SET reliability_assessment = assessed_reliability"
        )

    # --- Score vs evidence confidence ---------------------------------
    _add_column(conn, "opportunity_scores", "evidence_confidence", "TEXT")
    _add_column(conn, "opportunity_scores", "model_score_label", "TEXT DEFAULT 'MODEL_ESTIMATE'")
    _add_column(conn, "opportunity_scores", "source_coverage_json", "TEXT")
    _add_column(conn, "opportunity_scores", "verified_source_count", "INTEGER DEFAULT 0")
    _add_column(conn, "opportunity_scores", "verified_field_count", "INTEGER DEFAULT 0")
    if _table_exists(conn, "opportunity_scores"):
        conn.execute(
            "UPDATE opportunity_scores SET evidence_confidence = "
            "COALESCE(NULLIF(evidence_confidence, ''), NULLIF(confidence_level, ''), 'HYPOTHESIS')"
        )
    _add_column(conn, "opportunities", "source_coverage_json", "TEXT")
    _add_column(conn, "opportunities", "human_review_required", "INTEGER DEFAULT 0")
    _add_column(conn, "projects", "requested_opportunity_count", "INTEGER DEFAULT 5")

    # --- Chunked research index ---------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            token_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES research_sources(id) ON DELETE CASCADE,
            UNIQUE(source_id, chunk_index)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_chunks_project ON research_chunks(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_chunks_source ON research_chunks(source_id)")

    # --- Editor autosave / recovery -----------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_autosaves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL UNIQUE,
            project_id INTEGER NOT NULL,
            draft_content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'SAVED',
            error_message TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )

    # --- Backup audit trail -------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backup_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            reason TEXT NOT NULL,
            size_bytes INTEGER DEFAULT 0,
            integrity_ok INTEGER DEFAULT 0,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _migration_005_project_manuscript_settings(conn: sqlite3.Connection) -> None:
    """Per-project manuscript length and required-chapter settings.

    Word-count targets were previously global. Each project now owns its own
    values so changing one project never changes another. Existing projects are
    backfilled from the global application settings that were in effect.
    """
    _add_column(
        conn,
        "projects",
        "required_chapter_count",
        f"INTEGER DEFAULT {int(DEFAULT_REQUIRED_CHAPTER_COUNT)}",
    )
    _add_column(
        conn,
        "projects",
        "target_chapter_words",
        f"INTEGER DEFAULT {int(DEFAULT_CHAPTER_TARGET_WORDS)}",
    )
    _add_column(
        conn,
        "projects",
        "min_chapter_words",
        f"INTEGER DEFAULT {int(DEFAULT_MIN_CHAPTER_WORDS)}",
    )

    target_default = int(DEFAULT_CHAPTER_TARGET_WORDS)
    min_default = int(DEFAULT_MIN_CHAPTER_WORDS)
    if not _table_exists(conn, "projects"):
        return
    if _table_exists(conn, "application_settings"):
        for key, fallback in (
            ("chapter_target_words", target_default),
            ("min_chapter_words", min_default),
        ):
            row = conn.execute(
                "SELECT value FROM application_settings WHERE key = ?", (key,)
            ).fetchone()
            try:
                value = int(row[0]) if row else fallback
            except (TypeError, ValueError):
                value = fallback
            column = "target_chapter_words" if key == "chapter_target_words" else "min_chapter_words"
            # Only fill values that are still at their newly-added default.
            conn.execute(
                f"UPDATE projects SET {column} = ? WHERE {column} IS NULL OR {column} <= 0",
                (value,),
            )

    conn.execute(
        "UPDATE projects SET required_chapter_count = ? "
        "WHERE required_chapter_count IS NULL OR required_chapter_count <= 0",
        (int(DEFAULT_REQUIRED_CHAPTER_COUNT),),
    )
    conn.execute(
        "UPDATE projects SET target_chapter_words = ? "
        "WHERE target_chapter_words IS NULL OR target_chapter_words <= 0",
        (target_default,),
    )
    conn.execute(
        "UPDATE projects SET min_chapter_words = ? "
        "WHERE min_chapter_words IS NULL OR min_chapter_words <= 0",
        (min_default,),
    )
    # A minimum cannot exceed the target.
    conn.execute(
        "UPDATE projects SET min_chapter_words = target_chapter_words "
        "WHERE min_chapter_words > target_chapter_words"
    )


def _migration_006_p2_intelligence(conn: sqlite3.Connection) -> None:
    """P2 structured generation, retrieval traceability and editorial review.

    All tables are additive.  Existing chapters keep their content; the new
    columns describe structure, evidence and editorial state without changing
    any publication gate by themselves.
    """
    # --- Chapter completeness detail ----------------------------------
    _add_column(conn, "chapters", "planned_word_count", "INTEGER DEFAULT 0")
    _add_column(conn, "chapters", "actual_word_count", "INTEGER DEFAULT 0")
    _add_column(conn, "chapters", "structural_complete", "INTEGER DEFAULT 0")
    _add_column(conn, "chapters", "evidence_complete", "INTEGER DEFAULT 0")
    _add_column(conn, "chapters", "editorial_status", "TEXT DEFAULT 'NOT_REVIEWED'")
    _add_column(conn, "chapters", "approval_status", "TEXT DEFAULT 'UNREVIEWED'")
    _add_column(conn, "chapters", "editorial_json", "TEXT")

    _add_column(conn, "quality_reports", "editorial_rubric_json", "TEXT")
    _add_column(conn, "quality_reports", "editorial_advisory", "INTEGER DEFAULT 1")
    _add_column(conn, "quality_reports", "recommendations_json", "TEXT")

    # --- Structured chapter plans and sections ------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL UNIQUE,
            plan_json TEXT NOT NULL,
            planned_words INTEGER DEFAULT 0,
            target_words INTEGER DEFAULT 0,
            min_words INTEGER DEFAULT 0,
            status TEXT DEFAULT 'PLANNED',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapter_plans_project ON chapter_plans(project_id)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            section_key TEXT NOT NULL,
            heading TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            plan_notes TEXT,
            content TEXT DEFAULT '',
            word_budget INTEGER DEFAULT 0,
            word_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'PLANNED',
            model TEXT,
            generation_time_ms REAL,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            UNIQUE(chapter_id, section_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapter_sections_chapter ON chapter_sections(chapter_id)"
    )

    # --- Evidence used by a chapter (citation traceability) -----------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            source_id INTEGER,
            chunk_id INTEGER,
            chunk_index INTEGER,
            relevance_score REAL DEFAULT 0.0,
            quality_score REAL DEFAULT 0.0,
            citation_label TEXT,
            excerpt TEXT,
            used INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES research_sources(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapter_sources_chapter ON chapter_sources(chapter_id)"
    )

    # --- Batch generation sessions ------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS batch_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'RUNNING',
            mode TEXT DEFAULT 'ALL_REMAINING',
            total_chapters INTEGER DEFAULT 0,
            processed_chapters INTEGER DEFAULT 0,
            failed_chapters INTEGER DEFAULT 0,
            current_chapter INTEGER,
            state_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_batch_sessions_project ON batch_sessions(project_id)"
    )

    # --- Model comparison evaluations ---------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            model TEXT NOT NULL,
            task TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            generation_ms REAL DEFAULT 0.0,
            metrics_json TEXT,
            rubric_json TEXT,
            json_valid INTEGER DEFAULT 0,
            citation_compliance REAL DEFAULT 0.0,
            structure_score REAL DEFAULT 0.0,
            word_count INTEGER DEFAULT 0,
            topic_relevance REAL DEFAULT 0.0,
            instruction_compliance REAL DEFAULT 0.0,
            human_score REAL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
        )
        """
    )

    # --- Editorial recommendations requiring user review --------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS editorial_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            chapter_id INTEGER,
            report_id INTEGER,
            code TEXT NOT NULL,
            category TEXT NOT NULL,
            severity TEXT DEFAULT 'MEDIUM',
            message TEXT NOT NULL,
            source TEXT DEFAULT 'DETERMINISTIC',
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(report_id) REFERENCES quality_reports(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_editorial_recommendations_project "
        "ON editorial_recommendations(project_id)"
    )

    # Backfill actual word counts for existing chapters so the UI never shows
    # a stale value.  SQLite has no word-count function, so count spaces + 1
    # for non-empty content as an approximation used only for display.
    if _table_exists(conn, "chapters") and _column_exists(conn, "chapters", "content"):
        conn.execute(
            """
            UPDATE chapters
            SET actual_word_count = CASE
                WHEN content IS NULL OR TRIM(content) = '' THEN 0
                ELSE LENGTH(TRIM(content)) - LENGTH(REPLACE(TRIM(content), ' ', '')) + 1
            END
            WHERE actual_word_count IS NULL OR actual_word_count = 0
            """
        )


def _migration_007_publication_quality(conn: sqlite3.Connection) -> None:
    """P2.2 publication-quality engine.

    Adds structured claim records, cross-chapter repetition records, a persisted
    book architecture/coverage matrix, section-level validation state and audit
    columns.  Everything is additive so existing projects keep working.
    """
    # Chapter / section / plan lifecycle and validation state.
    _add_column(conn, "chapters", "lifecycle_status", "TEXT DEFAULT 'INCOMPLETE'")
    _add_column(conn, "chapter_plans", "architecture_json", "TEXT")
    _add_column(conn, "chapter_plans", "evidence_plan_json", "TEXT")
    _add_column(conn, "chapter_sections", "validation_json", "TEXT")
    _add_column(conn, "chapter_sections", "validation_status", "TEXT DEFAULT 'PENDING'")
    _add_column(conn, "chapter_sections", "claim_summary_json", "TEXT")
    _add_column(conn, "chapter_sections", "repetition_summary_json", "TEXT")
    _add_column(conn, "chapter_sections", "revision", "INTEGER DEFAULT 0")
    _add_column(conn, "chapter_sections", "approved", "INTEGER DEFAULT 0")
    _add_column(conn, "quality_reports", "claim_summary_json", "TEXT")
    _add_column(conn, "quality_reports", "repetition_summary_json", "TEXT")
    _add_column(conn, "quality_reports", "architecture_summary_json", "TEXT")
    _add_column(conn, "quality_reports", "gate_status", "TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            section_id INTEGER,
            section_key TEXT,
            claim_key TEXT NOT NULL,
            claim_text TEXT NOT NULL,
            claim_type TEXT NOT NULL,
            citation_ids_json TEXT,
            source_ids_json TEXT,
            excerpts_json TEXT,
            verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
            confidence REAL DEFAULT 0.0,
            human_review_status TEXT DEFAULT 'NOT_REQUIRED',
            review_note TEXT,
            severity TEXT DEFAULT 'MEDIUM',
            number_check_json TEXT,
            unit_check_json TEXT,
            population_check_json TEXT,
            geography_check_json TEXT,
            time_check_json TEXT,
            meaning_check_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(section_id) REFERENCES chapter_sections(id) ON DELETE SET NULL,
            UNIQUE(chapter_id, claim_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapter_claims_chapter ON chapter_claims(chapter_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapter_claims_project ON chapter_claims(project_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapter_claims_status ON chapter_claims(verification_status)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS claim_verification_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            previous_status TEXT,
            verification_status TEXT NOT NULL,
            human_review_status TEXT NOT NULL,
            explanation TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(claim_id) REFERENCES chapter_claims(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_claim_verification_events_claim "
        "ON claim_verification_events(claim_id)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repetition_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            chapter_id INTEGER,
            scope TEXT NOT NULL DEFAULT 'PROJECT',
            similarity_threshold REAL DEFAULT 0.82,
            exact_count INTEGER DEFAULT 0,
            near_count INTEGER DEFAULT 0,
            finding_count INTEGER DEFAULT 0,
            critical_count INTEGER DEFAULT 0,
            summary_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_repetition_runs_project ON repetition_runs(project_id)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repetition_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            chapter_id INTEGER,
            original_chapter_id INTEGER,
            original_chapter_number INTEGER,
            duplicate_chapter_number INTEGER,
            original_section TEXT,
            duplicate_section TEXT,
            match_type TEXT NOT NULL,
            similarity REAL DEFAULT 0.0,
            original_text TEXT NOT NULL,
            duplicate_text TEXT NOT NULL,
            recommendation TEXT,
            severity TEXT DEFAULT 'MEDIUM',
            resolved INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(run_id) REFERENCES repetition_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE SET NULL,
            FOREIGN KEY(original_chapter_id) REFERENCES chapters(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_repetition_findings_run ON repetition_findings(run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_repetition_findings_chapter "
        "ON repetition_findings(chapter_id)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_architectures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL UNIQUE,
            architecture_json TEXT NOT NULL,
            coverage_matrix_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PLANNED',
            overlap_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )

    # --- Measurable transformation targets ---------------------------
    # These remain separate from factual claims.  A reader-selected target is
    # useful, but must never silently become a guaranteed product outcome.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transformation_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            target_text TEXT NOT NULL,
            target_value TEXT,
            metric TEXT,
            measurement_method TEXT,
            population TEXT,
            time_period TEXT,
            target_category TEXT NOT NULL,
            source_ids_json TEXT DEFAULT '[]',
            evidence_status TEXT NOT NULL DEFAULT 'NOT_INDEPENDENTLY_ESTABLISHED',
            display_label TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transformation_targets_project "
        "ON transformation_targets(project_id)"
    )


def _migration_008_claim_provenance(conn: sqlite3.Connection) -> None:
    """P2.3.4: store deterministic provenance/attribution checks per claim."""
    _add_column(conn, "chapter_claims", "provenance_check_json", "TEXT DEFAULT '{}'")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapter_claims_project_chapter "
        "ON chapter_claims(project_id, chapter_id)"
    )


MIGRATIONS: List[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "evidence_linking", _migration_001_evidence_linking),
    (2, "generation_contracts", _migration_002_generation_contracts),
    (3, "audit_and_export_gates", _migration_003_audit_and_exports),
    (4, "p1_trust_backup_autosave", _migration_004_p1_trust_backup_autosave),
    (5, "project_manuscript_settings", _migration_005_project_manuscript_settings),
    (6, "p2_intelligence", _migration_006_p2_intelligence),
    (7, "publication_quality", _migration_007_publication_quality),
    (8, "claim_provenance", _migration_008_claim_provenance),
]


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    if not _table_exists(conn, "schema_migrations"):
        return set()
    return {int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations")}


def pending_migrations(conn: sqlite3.Connection) -> list[tuple[int, str, Callable]]:
    applied = _applied_versions(conn)
    return [m for m in MIGRATIONS if m[0] not in applied]


def backup_database(reason: str = "pre-migration") -> Path | None:
    """Take a consistent SQLite snapshot using the backup service.

    Kept as a compatibility wrapper because existing callers expect a path.
    """
    from services.backup_service import create_backup

    info = create_backup(reason, db_path=DATABASE_PATH, backup_dir=BACKUP_DIR)
    if not info:
        return None
    target = Path(info["path"])
    if not info.get("integrity_ok"):
        logger.error("Backup integrity check failed for %s: %s", target, info.get("detail"))
    logger.info("Database backed up to %s", target)
    return target


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply all pending migrations. Each migration is idempotent."""
    pending = pending_migrations(conn)
    applied: list[int] = []
    for version, name, fn in pending:
        logger.info("Applying migration %s: %s", version, name)
        fn(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_migrations (version) VALUES (?)", (version,)
        )
        applied.append(version)
    return applied
