"""Final consistency and data-safety audit regressions.

Covers the canonical manuscript-completeness service, per-project word-count
settings, real SQLite backup/restore (including WAL and foreign keys), complete
project duplication with foreign-key remapping, test isolation, autosave failure
recovery/conflict handling, and approved-chapter overwrite protection.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from config.settings import BACKUP_DIR, DATABASE_PATH, OUTPUT_DIR
from database.db import get_connection, transaction
from models.schemas import (
    OpportunityEvidenceItem,
    OpportunityItem,
    OpportunityResponse,
    ProjectCreate,
)
from services.autosave_service import AutosaveService, FAILED, SAVED
from services.backup_service import (
    create_backup,
    prune_backups,
    restore_backup,
    verify_backup,
)
from services.generation_service import GenerationService
from services.manuscript_service import ManuscriptService, STATUS_DRAFT
from services.market_service import MarketService
from services.outline_service import OutlineService
from services.product_format_service import validate_outline
from services.project_service import ProjectService
from services.research_service import ResearchService

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "database" / "schema.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _words(text: str) -> int:
    return len((text or "").split())


def _content(word_count: int) -> str:
    return "word " * word_count


def _add_chapter(
    project_id: int,
    number: int,
    word_count: int,
    *,
    validation_status: str = "PENDING",
    title: str | None = None,
) -> int:
    with transaction() as conn:
        cur = conn.cursor()
        content = _content(word_count) if word_count else ""
        cur.execute(
            """
            INSERT INTO chapters (
                project_id, chapter_number, title, purpose, content,
                word_count, validation_status
            ) VALUES (?, ?, ?, 'Purpose', ?, ?, ?)
            """,
            (project_id, number, title or f"Chapter {number}", content, word_count, validation_status),
        )
        return cur.lastrowid


def _approve(chapter_id: int, project_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO quality_reports (
                project_id, chapter_id, status, score, deterministic_checks_json,
                issues_json, human_review_required
            ) VALUES (?, ?, 'PASS', 94, ?, '[]', 0)
            """,
            (project_id, chapter_id, '{"counts": {"critical": 0, "high": 0}}'),
        )


def _new_project(name: str, **kwargs) -> int:
    return ProjectService.create_project(ProjectCreate(name=name, **kwargs))


# ---------------------------------------------------------------------------
# 1. Canonical manuscript completeness
# ---------------------------------------------------------------------------
def test_canonical_counts_are_identical_across_service_entry_points():
    project_id = _new_project("Canonical Counts Test")
    ProjectService.update_chapter_settings(
        project_id, target_words=2200, min_words=1000, required_chapters=4
    )

    approved_id = _add_chapter(project_id, 1, 1200)
    _approve(approved_id, project_id)
    _add_chapter(project_id, 2, 500)          # written but below minimum
    _add_chapter(project_id, 3, 2200)         # complete, not approved
    _add_chapter(project_id, 4, 0)            # outline only

    chapters = OutlineService.get_chapters(project_id)
    fresh = ManuscriptService.assess_project(project_id)
    supplied = ManuscriptService.assess(project_id, chapters, sources=[])

    expected = {
        "required_chapters": 4,
        "existing_chapters": 4,
        "written_chapters": 3,
        "min_word_compliant_chapters": 2,
        "audit_approved_chapters": 1,
        "human_review_required_chapters": 0,
        "publication_ready_chapters": 1,
    }
    assert fresh["counts"] == expected
    assert supplied["counts"] == expected
    assert fresh["counts"] == supplied["counts"]
    assert fresh["label"] == supplied["label"] == "DRAFT — 2/4 CHAPTERS COMPLETE"
    assert fresh["status"] == STATUS_DRAFT
    assert fresh["can_publish"] is False

    # Per-chapter rows expose target, minimum, actual words and validation state.
    details = {d["chapter"]: d for d in fresh["chapter_details"]}
    assert details[1]["target_words"] == 2200
    assert details[1]["min_words"] == 1000
    assert details[1]["words"] == 1200
    assert details[1]["min_word_compliant"] is True
    assert details[1]["approved"] is True
    assert details[2]["words"] == 500
    assert details[2]["min_word_compliant"] is False
    assert details[3]["words"] == 2200
    assert details[3]["approved"] is False
    assert details[4]["words"] == 0 and details[4]["written"] is False

    ProjectService.delete_project(project_id)


def test_canonical_counts_are_project_scoped_not_global():
    project_a = _new_project("Scoped Project A")
    project_b = _new_project("Scoped Project B")
    ProjectService.update_chapter_settings(project_a, required_chapters=3)
    ProjectService.update_chapter_settings(project_b, required_chapters=1)

    _add_chapter(project_a, 1, 1200)
    _add_chapter(project_a, 2, 1200)
    _add_chapter(project_b, 1, 1200)

    a = ManuscriptService.assess_project(project_a)
    b = ManuscriptService.assess_project(project_b)

    assert a["existing_chapters"] == 2
    assert b["existing_chapters"] == 1
    assert a["written_count"] == 2
    assert b["written_count"] == 1
    # Required counts come from each project's own settings.
    assert a["required_chapters"] == 3
    assert b["required_chapters"] == 1
    # Global chapter totals must never leak into a project report.
    assert a["existing_chapters"] != b["existing_chapters"]

    ProjectService.delete_project(project_a)
    ProjectService.delete_project(project_b)


# ---------------------------------------------------------------------------
# 2. Configurable chapter word counts
# ---------------------------------------------------------------------------
def test_per_project_word_settings_persist_and_are_isolated():
    project_a = _new_project("Word Settings A")
    project_b = _new_project("Word Settings B")

    saved = ProjectService.update_chapter_settings(
        project_a, target_words=1500, min_words=800, required_chapters=6
    )
    assert saved == {"target_words": 1500, "min_words": 800, "required_chapters": 6}
    assert ProjectService.get_chapter_settings(project_a) == saved

    # Project B is untouched by project A's change.
    b_defaults = ProjectService.get_chapter_settings(project_b)
    assert b_defaults["target_words"] != 1500
    assert b_defaults["min_words"] != 800
    assert b_defaults["required_chapters"] != 6

    # Values persist across a fresh database read.
    assert ProjectService.get_project(project_a)["target_chapter_words"] == 1500
    assert ProjectService.get_project(project_a)["min_chapter_words"] == 800

    with pytest.raises(ValueError):
        ProjectService.update_chapter_settings(project_a, target_words=800, min_words=1200)

    # Invalid create payload is rejected before touching the database.
    with pytest.raises(ValueError):
        ProjectCreate(name="Bad Settings", target_chapter_words=500, min_chapter_words=900)

    ProjectService.delete_project(project_a)
    ProjectService.delete_project(project_b)


def test_short_complete_and_over_target_chapters_use_project_thresholds():
    project_id = _new_project("Threshold Test")
    ProjectService.update_chapter_settings(
        project_id, target_words=2200, min_words=1000, required_chapters=3
    )
    _add_chapter(project_id, 1, 400)    # short
    _add_chapter(project_id, 2, 2200)   # exactly target
    _add_chapter(project_id, 3, 3500)   # over target

    report = ManuscriptService.assess_project(project_id)
    details = {d["chapter"]: d for d in report["chapter_details"]}

    assert details[1]["words"] == 400 and details[1]["min_word_compliant"] is False
    assert details[2]["words"] == 2200 and details[2]["min_word_compliant"] is True
    assert details[3]["words"] == 3500 and details[3]["min_word_compliant"] is True
    assert report["min_word_compliant_count"] == 2
    assert report["written_count"] == 3
    assert report["target_words"] == 2200
    assert report["min_chapter_words"] == 1000

    # Lowering the minimum changes only this project's compliance result.
    ProjectService.update_chapter_settings(project_id, min_words=300)
    lowered = ManuscriptService.assess_project(project_id)
    assert lowered["min_word_compliant_count"] == 3

    ProjectService.delete_project(project_id)


# ---------------------------------------------------------------------------
# 3. Backup and restore
# ---------------------------------------------------------------------------
def _make_schema_db(path: Path, *, wal: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    if wal:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    conn.execute(
        "INSERT INTO projects (id, name, status) VALUES (1, 'Backup Project', 'ACTIVE')"
    )
    conn.execute(
        """
        INSERT INTO research_sources (
            project_id, title, content_hash, source_category, raw_content
        ) VALUES (1, 'Backup Source', 'hash-1', 'REPORT', 'Source body')
        """
    )
    conn.execute(
        """
        INSERT INTO chapters (
            project_id, chapter_number, title, content, word_count
        ) VALUES (1, 1, 'Backup Chapter', 'chapter body', 2)
        """
    )
    conn.commit()
    return conn


def test_online_backup_handles_wal_and_restores_project_graph(tmp_path):
    live_db = tmp_path / "live_wal.db"
    backup_dir = tmp_path / "backups"
    writer = _make_schema_db(live_db, wal=True)

    # A concurrent writer remains open while the online backup runs.
    info = create_backup("audit_wal", db_path=live_db, backup_dir=backup_dir)
    assert info is not None
    assert info["integrity_ok"] is True
    assert verify_backup(info["path"])[0] is True

    snapshot = sqlite3.connect(str(info["path"]))
    try:
        assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert snapshot.execute("PRAGMA foreign_key_check").fetchall() == []
        assert snapshot.execute("SELECT name FROM projects WHERE id=1").fetchone()[0] == "Backup Project"
        assert snapshot.execute("SELECT COUNT(*) FROM research_sources WHERE project_id=1").fetchone()[0] == 1
        assert snapshot.execute("SELECT COUNT(*) FROM chapters WHERE project_id=1").fetchone()[0] == 1
    finally:
        snapshot.close()

    # Mutate live after the snapshot, then restore.
    writer.execute(
        "INSERT INTO chapters (project_id, chapter_number, title, content) VALUES (1, 2, 'Later', 'later')"
    )
    writer.commit()
    assert writer.execute("SELECT COUNT(*) FROM chapters WHERE project_id=1").fetchone()[0] == 2

    ok, message = restore_backup(
        info["path"], confirm=True, db_path=live_db, backup_dir=backup_dir
    )
    assert ok is True, message

    check = sqlite3.connect(str(live_db))
    try:
        assert check.execute("SELECT COUNT(*) FROM chapters WHERE project_id=1").fetchone()[0] == 1
        assert check.execute("SELECT name FROM projects WHERE id=1").fetchone()[0] == "Backup Project"
        assert check.execute("SELECT title FROM research_sources WHERE project_id=1").fetchone()[0] == "Backup Source"
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert check.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        check.close()
        writer.close()

    # A verified pre-restore backup exists.
    names = {p.name for p in backup_dir.glob("*.db")}
    assert any("pre_restore" in name for name in names)


def test_verify_backup_rejects_foreign_key_orphans(tmp_path):
    db_path = tmp_path / "orphans.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO chapters (project_id, chapter_number, title) VALUES (999, 1, 'Orphan')"
    )
    conn.commit()
    conn.close()

    ok, detail = verify_backup(db_path)
    assert ok is False
    assert "foreign-key" in detail.lower()


def test_restore_refuses_corrupt_backup_and_leaves_live_database_untouched(tmp_path):
    live_db = tmp_path / "live_guard.db"
    backup_dir = tmp_path / "backups"
    conn = _make_schema_db(live_db)
    conn.close()

    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a sqlite database")

    ok, message = restore_backup(bad, confirm=True, db_path=live_db, backup_dir=backup_dir)
    assert ok is False
    assert "refused" in message.lower()

    check = sqlite3.connect(str(live_db))
    try:
        assert check.execute("SELECT name FROM projects WHERE id=1").fetchone()[0] == "Backup Project"
    finally:
        check.close()


def test_failed_backup_leaves_no_partial_temp_file(tmp_path):
    live_db = tmp_path / "garbage.db"
    live_db.write_bytes(b"this is not a database")
    backup_dir = tmp_path / "backups"

    assert create_backup("broken", db_path=live_db, backup_dir=backup_dir) is None
    assert list(backup_dir.glob("*.tmp")) == []
    assert list(backup_dir.glob("pss_data_*.db")) == []


def test_backup_retention_prunes_to_newest_files(tmp_path):
    backup_dir = tmp_path / "retention"
    backup_dir.mkdir()
    now = time.time()
    for index in range(6):
        path = backup_dir / f"pss_data_old_{index}.db"
        path.write_bytes(b"snapshot")
        os.utime(path, (now + index, now + index))

    deleted = prune_backups(keep_last=2, backup_dir=backup_dir)
    remaining = sorted(p.name for p in backup_dir.glob("pss_data_*.db"))
    assert len(remaining) == 2
    assert remaining == ["pss_data_old_4.db", "pss_data_old_5.db"]
    assert len(deleted) == 4


# ---------------------------------------------------------------------------
# 4. Duplication foreign-key integrity
# ---------------------------------------------------------------------------
def test_duplication_remaps_chunks_findings_reports_and_autosaves():
    project_id = _new_project(
        "Duplication Integrity",
        required_chapter_count=3,
        target_chapter_words=1800,
        min_chapter_words=900,
    )
    ok, source_id, message = ResearchService.add_source(
        project_id,
        "Duplication source",
        "REPORT",
        "A detailed research source about clinic operations and no-show costs. " * 30,
    )
    assert ok, message

    with transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO opportunities (
                project_id, name, exact_customer, customer_problem, problem_urgency,
                desired_outcome, potential_solution
            ) VALUES (?, 'Dup Opp', 'Owners', 'Problem', 'High', 'Outcome', 'Solution')
            """,
            (project_id,),
        )
        opportunity_id = cur.lastrowid
        cur.execute(
            """
            INSERT INTO opportunity_evidence (
                opportunity_id, source_id, claim_field, claim_text
            ) VALUES (?, ?, 'demand_evidence', 'Claim')
            """,
            (opportunity_id, source_id),
        )
        cur.execute(
            """
            INSERT INTO research_findings (project_id, source_id, finding_text)
            VALUES (?, ?, 'Finding text')
            """,
            (project_id, source_id),
        )
        cur.execute(
            "UPDATE projects SET selected_opportunity_id = ? WHERE id = ?",
            (opportunity_id, project_id),
        )
        cur.execute(
            """
            INSERT INTO chapters (
                project_id, chapter_number, title, content, word_count
            ) VALUES (?, 1, 'Dup Chapter', 'Published text', 2)
            """,
            (project_id,),
        )
        chapter_id = cur.lastrowid
        cur.execute(
            """
            INSERT INTO quality_reports (project_id, chapter_id, status, score)
            VALUES (?, ?, 'PASS', 90)
            """,
            (project_id, chapter_id),
        )
        cur.execute(
            """
            INSERT INTO chapter_revisions (chapter_id, revision_number, content, word_count)
            VALUES (?, 1, 'Old text', 2)
            """,
            (chapter_id,),
        )

    AutosaveService.save_draft(chapter_id, project_id, "Draft text")
    duplicate_id = ProjectService.duplicate_project(project_id)
    assert duplicate_id is not None

    with get_connection() as conn:
        dup_project = dict(conn.execute("SELECT * FROM projects WHERE id=?", (duplicate_id,)).fetchone())
        dup_source = conn.execute("SELECT * FROM research_sources WHERE project_id=?", (duplicate_id,)).fetchone()
        dup_chunk = conn.execute("SELECT * FROM research_chunks WHERE project_id=?", (duplicate_id,)).fetchone()
        dup_finding = conn.execute("SELECT * FROM research_findings WHERE project_id=?", (duplicate_id,)).fetchone()
        dup_evidence = conn.execute(
            "SELECT * FROM opportunity_evidence WHERE opportunity_id=?", (dup_project["selected_opportunity_id"],)
        ).fetchone()
        dup_chapter = conn.execute("SELECT * FROM chapters WHERE project_id=?", (duplicate_id,)).fetchone()
        dup_report = conn.execute("SELECT * FROM quality_reports WHERE project_id=?", (duplicate_id,)).fetchone()
        dup_autosave = conn.execute("SELECT * FROM chapter_autosaves WHERE project_id=?", (duplicate_id,)).fetchone()
        original = conn.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
        original_chunk = conn.execute("SELECT * FROM research_chunks WHERE project_id=?", (project_id,)).fetchone()

    # Per-project settings survive duplication.
    assert dup_project["required_chapter_count"] == 3
    assert dup_project["target_chapter_words"] == 1800
    assert dup_project["min_chapter_words"] == 900

    # Foreign keys point at the duplicate's own rows, never the originals.
    assert dup_source["id"] != source_id
    assert dup_chunk["source_id"] == dup_source["id"]
    assert dup_chunk["id"] != original_chunk["id"]
    assert dup_finding["source_id"] == dup_source["id"]
    assert dup_evidence["source_id"] == dup_source["id"]
    assert dup_report["chapter_id"] == dup_chapter["id"]
    assert dup_autosave["chapter_id"] == dup_chapter["id"]

    # Editing the duplicate does not touch the original.
    with transaction() as conn:
        conn.execute("UPDATE chapters SET content='Duplicate edited' WHERE id=?", (dup_chapter["id"],))
        conn.execute("UPDATE research_sources SET title='Duplicate source edited' WHERE id=?", (dup_source["id"],))
        conn.execute("UPDATE projects SET name='Duplicate renamed' WHERE id=?", (duplicate_id,))
    with get_connection() as conn:
        assert conn.execute("SELECT content FROM chapters WHERE id=?", (chapter_id,)).fetchone()[0] == "Published text"
        assert conn.execute("SELECT title FROM research_sources WHERE id=?", (source_id,)).fetchone()[0] == "Duplication source"
        assert conn.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()[0] == "Duplication Integrity"

    ProjectService.delete_project(project_id)
    ProjectService.delete_project(duplicate_id)


def test_outline_validation_respects_configured_required_chapter_count():
    short_outline = {
        "chapters": [
            {
                "chapter_number": 1,
                "title": "Mapping WhatsApp Order Leakage in a 200-SKU Pharmacy",
            },
            {
                "chapter_number": 2,
                "title": "Building a Weekly Refill Reminder Routine That Staff Actually Use",
            },
        ]
    }
    # Default full-book contract still requires 8-14 chapters.
    valid, issues = validate_outline("Ebook", short_outline)
    assert valid is False
    assert any(i["code"] == "outline_count" for i in issues)

    # A project explicitly configured for two required chapters is accepted.
    valid, issues = validate_outline("Ebook", short_outline, required_chapters=2)
    assert valid is True, issues

    # Large configured projects may exceed the default fourteen-chapter ceiling.
    large = {
        "chapters": [
            {"chapter_number": n, "title": f"Pharmacy workflow module {n} for repeat orders"}
            for n in range(1, 21)
        ]
    }
    valid, issues = validate_outline("Ebook", large, required_chapters=20)
    assert valid is True, issues


# ---------------------------------------------------------------------------
# 5. Opportunity discovery evidence integrity
# ---------------------------------------------------------------------------
def test_discovery_stores_hallucinated_source_ids_as_null_without_fk_failure():
    project_id = _new_project("Evidence FK Test")
    ok, source_id, message = ResearchService.add_source(
        project_id,
        "Evidence FK source",
        "REPORT",
        "A detailed survey of independent pharmacy ordering behaviour and refill reminders. " * 20,
    )
    assert ok, message

    class FakeAI:
        def generate_structured(self, prompt, schema, **kwargs):
            return (
                True,
                OpportunityResponse(
                    opportunities=[
                        OpportunityItem(
                            name="Hallucinated citation",
                            exact_customer="Pharmacy owners",
                            customer_problem="Missed refill orders",
                            problem_urgency="High",
                            desired_outcome="Reliable refills",
                            potential_solution="A reminder workflow",
                            evidence=[
                                OpportunityEvidenceItem(
                                    field="demand_evidence",
                                    source_id=999999,
                                    excerpt="An excerpt that does not exist",
                                    claim="A claim that does not exist",
                                )
                            ],
                        )
                    ]
                ),
                "Success",
            )

    project = ProjectService.get_project(project_id)
    saved, opportunities, detail = MarketService(FakeAI()).discover_opportunities(
        project, requested_count=1
    )
    assert saved is True, detail
    assert len(opportunities) == 1

    with get_connection() as conn:
        evidence = conn.execute(
            "SELECT * FROM opportunity_evidence WHERE opportunity_id=?",
            (opportunities[0]["id"],),
        ).fetchone()
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert evidence["source_id"] is None
    assert "does not exist" in (evidence["verification_notes"] or "")
    assert fk == []

    ProjectService.delete_project(project_id)


# ---------------------------------------------------------------------------
# 6. Test isolation
# ---------------------------------------------------------------------------
def test_test_run_uses_temporary_database_and_output():
    temp_root = Path(DATABASE_PATH).resolve().parent
    assert "pss_test_" in str(temp_root)
    assert "pss_test_" in str(Path(OUTPUT_DIR).resolve())
    assert "pss_test_" in str(Path(BACKUP_DIR).resolve())
    production_db = Path(__file__).resolve().parents[1] / "database" / "pss_data.db"
    assert Path(DATABASE_PATH).resolve() != production_db.resolve()


# ---------------------------------------------------------------------------
# 7. Autosave and approved-chapter protection
# ---------------------------------------------------------------------------
def test_failed_autosave_is_recorded_and_recoverable(monkeypatch):
    project_id = _new_project("Autosave Failure Test")
    chapter_id = _add_chapter(project_id, 1, 10)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(AutosaveService, "save_draft", staticmethod(boom))
    result = AutosaveService.autosave_if_changed(
        chapter_id, project_id, "Unsaved local text", "Original"
    )
    assert result["attempted"] is True
    assert result["success"] is False
    assert result["status"] == FAILED

    draft = AutosaveService.failed_recovery(chapter_id)
    assert draft is not None
    assert draft["status"] == FAILED
    assert "locked" in (draft.get("error_message") or "")
    assert draft["draft_content"] == "Unsaved local text"

    # The recoverable draft survives a simulated restart (fresh read).
    assert AutosaveService.recoverable_draft(chapter_id, "Original")["draft_content"] == "Unsaved local text"

    ProjectService.delete_project(project_id)


def test_conflict_detection_and_resolution_does_not_overwrite_published_content():
    assert AutosaveService.has_conflict("base", "changed elsewhere", "local draft") is True
    assert AutosaveService.has_conflict("base", "changed elsewhere", "changed elsewhere") is False
    assert AutosaveService.has_conflict(None, "changed", "draft") is False

    project_id = _new_project("Conflict Test")
    chapter_id = _add_chapter(project_id, 1, 20)
    # An external edit changed the published content.
    with transaction() as conn:
        conn.execute("UPDATE chapters SET content='external text' WHERE id=?", (chapter_id,))
    AutosaveService.save_draft(chapter_id, project_id, "local draft")

    with get_connection() as conn:
        published = conn.execute("SELECT content FROM chapters WHERE id=?", (chapter_id,)).fetchone()[0]
    assert published == "external text"
    assert AutosaveService.has_conflict("word " * 20, published, AutosaveService.get_draft(chapter_id)["draft_content"])

    ProjectService.delete_project(project_id)


def test_approved_chapter_cannot_be_silently_overwritten():
    project_id = _new_project("Approved Overwrite Test")
    chapter_id = _add_chapter(project_id, 1, 50)
    _approve(chapter_id, project_id)
    assert ManuscriptService.chapter_is_approved(chapter_id) is True

    assert GenerationService.save_chapter_content(chapter_id, "replacement") is False
    with get_connection() as conn:
        assert conn.execute("SELECT content FROM chapters WHERE id=?", (chapter_id,)).fetchone()[0] == _content(50)

    # Explicit confirmation allows the replacement and archives a revision.
    assert (
        GenerationService.save_chapter_content(
            chapter_id, "approved replacement", allow_approved_overwrite=True
        )
        is True
    )
    with get_connection() as conn:
        assert conn.execute("SELECT content FROM chapters WHERE id=?", (chapter_id,)).fetchone()[0] == "approved replacement"
        assert conn.execute("SELECT COUNT(*) FROM chapter_revisions WHERE chapter_id=?", (chapter_id,)).fetchone()[0] >= 1

    ProjectService.delete_project(project_id)


def test_batch_generation_skips_approved_chapters(monkeypatch):
    project_id = _new_project("Batch Skip Test")
    ProjectService.update_chapter_settings(project_id, required_chapters=2)
    approved_id = _add_chapter(project_id, 1, 1200)
    _approve(approved_id, project_id)
    _add_chapter(project_id, 2, 0)

    service = GenerationService()
    calls: list[int] = []

    def fake_generate(chapter, charter, evidence_sources, custom_instructions=""):
        calls.append(chapter["chapter_number"])
        return False, "", None, "simulated model failure"

    monkeypatch.setattr(service, "generate_chapter_validated", fake_generate)
    ok, results, _message = service.generate_book_sequentially(
        project_id, {"product_format": "Ebook"}
    )

    statuses = {r["chapter"]: r["status"] for r in results}
    assert statuses[1] == "SKIPPED_APPROVED"
    assert calls == [2]
    assert ok is True

    ProjectService.delete_project(project_id)
