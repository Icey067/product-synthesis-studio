"""Migration and backup regression tests.

Ensures existing databases receive the new columns/tables, legacy opportunities
are honestly reclassified, migrations are idempotent, and a backup is taken
before structural changes.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from database import migrations


def _make_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP);
        CREATE TABLE opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            name TEXT,
            evidence_confidence TEXT DEFAULT 'LOW'
        );
        CREATE TABLE product_charters (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER);
        CREATE TABLE transformation_maps (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER);
        CREATE TABLE product_outlines (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER);
        CREATE TABLE chapters (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER);
        CREATE TABLE quality_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER);
        CREATE TABLE exports (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER);
        CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
        CREATE TABLE research_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            title TEXT,
            reliability_assessment TEXT DEFAULT 'MEDIUM'
        );
        CREATE TABLE opportunity_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER,
            weighted_score REAL,
            confidence_level TEXT
        );
        INSERT INTO opportunities (project_id, name) VALUES (1, 'Legacy Opportunity');
        INSERT INTO research_sources (project_id, title, reliability_assessment)
        VALUES (1, 'Legacy source', 'HIGH');
        """
    )
    conn.commit()
    conn.close()


def test_migrations_add_columns_and_reclassify_legacy(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(db_path)

    conn = sqlite3.connect(str(db_path))
    applied = migrations.apply_migrations(conn)
    conn.commit()
    assert applied == [1, 2, 3, 4, 5, 6]

    def columns(table):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

    assert "opportunity_evidence" in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"evidence_status", "evidence_summary", "citation_ids_json"} <= columns("opportunities")
    assert "product_format" in columns("product_charters")
    assert "product_format" in columns("transformation_maps")
    assert "product_format" in columns("product_outlines")
    assert {"target_word_count", "validation_status", "validation_json"} <= columns("chapters")
    assert {"deterministic_checks_json", "issues_json", "human_review_required"} <= columns("quality_reports")
    assert {"completeness_status", "completeness_json"} <= columns("exports")
    # P1 schema additions
    assert {"declared_reliability", "assessed_reliability", "assessment_score"} <= columns("research_sources")
    assert {"evidence_confidence", "model_score_label", "source_coverage_json"} <= columns("opportunity_scores")
    assert {
        "requested_opportunity_count",
        "required_chapter_count",
        "target_chapter_words",
        "min_chapter_words",
    } <= columns("projects")
    assert {"research_chunks", "chapter_autosaves", "backup_runs"} <= {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    row = conn.execute(
        "SELECT evidence_status FROM opportunities WHERE name = 'Legacy Opportunity'"
    ).fetchone()
    assert row[0] == "LEGACY_UNVERIFIED"

    # Legacy reliability is kept only as the user's declaration; it is not
    # promoted to an independent assessment (no credibility is created).
    legacy_source = conn.execute(
        "SELECT declared_reliability, assessed_reliability, reliability_assessment "
        "FROM research_sources WHERE title = 'Legacy source'"
    ).fetchone()
    assert legacy_source[0] == "HIGH"
    assert legacy_source[1] == "UNASSESSED"
    assert legacy_source[2] == "UNASSESSED"

    # Idempotent: running again applies nothing and does not error.
    assert migrations.apply_migrations(conn) == []
    conn.close()


def test_backup_database_copies_file(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    _make_legacy_db(db_path)
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(migrations, "DATABASE_PATH", db_path)
    monkeypatch.setattr(migrations, "BACKUP_DIR", backup_dir)

    target = migrations.backup_database("unit_test")
    assert target is not None
    assert target.exists()
    assert target.stat().st_size > 0
    assert target.parent == backup_dir
    # The snapshot must be a readable database, not a partial file copy.
    snapshot = sqlite3.connect(str(target))
    try:
        assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert snapshot.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0] > 0
    finally:
        snapshot.close()


def test_init_db_backs_up_existing_database_before_migration(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "legacy_live.db"
    backup_dir = tmp_path / "backups"
    output_dir = tmp_path / "output"
    _make_legacy_db(db_path)

    env = os.environ.copy()
    env["PSS_DATABASE_PATH"] = str(db_path)
    env["PSS_BACKUP_DIR"] = str(backup_dir)
    env["PSS_OUTPUT_DIR"] = str(output_dir)

    script = (
        "import sqlite3, os\n"
        "from database.db import init_db\n"
        "init_db()\n"
        "conn = sqlite3.connect(os.environ['PSS_DATABASE_PATH'])\n"
        "status = conn.execute(\"SELECT evidence_status FROM opportunities WHERE id=1\").fetchone()[0]\n"
        "has_table = conn.execute(\"SELECT COUNT(*) FROM sqlite_master WHERE name='opportunity_evidence'\").fetchone()[0]\n"
        "print('STATUS=' + status)\n"
        "print('TABLE=' + str(has_table))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(project_root),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "STATUS=LEGACY_UNVERIFIED" in result.stdout
    assert "TABLE=1" in result.stdout

    backups = list(backup_dir.glob("*.db"))
    assert backups, "No pre-migration backup was created"
    assert any("pre_migration" in b.name for b in backups)
