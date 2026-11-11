"""P1 regressions: verified backups, gated restore, debounced autosave."""
import sqlite3
from pathlib import Path

from database.db import transaction
from models.schemas import ProjectCreate
from services.autosave_service import AutosaveService, FAILED, SAVED, SAVING
from services.backup_service import (
    create_backup,
    list_backups,
    maybe_run_scheduled_backup,
    restore_backup,
    verify_backup,
)
from services.project_service import ProjectService


def _make_database(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL);
        CREATE TABLE backup_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            reason TEXT NOT NULL,
            size_bytes INTEGER DEFAULT 0,
            integrity_ok INTEGER DEFAULT 0,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO items (name) VALUES ('before');
        """
    )
    conn.commit()
    conn.close()


def _names(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {row[0] for row in conn.execute("SELECT name FROM items")}
    finally:
        conn.close()


def test_backup_verify_and_restore_requires_confirmation(tmp_path):
    live_db = tmp_path / "live.db"
    backup_dir = tmp_path / "backups"
    _make_database(live_db)

    info = create_backup("unit_test", db_path=live_db, backup_dir=backup_dir)
    assert info is not None
    assert info["integrity_ok"] is True
    assert Path(info["path"]).exists()
    assert verify_backup(info["path"])[0] is True

    conn = sqlite3.connect(str(live_db))
    conn.execute("INSERT INTO items (name) VALUES ('after')")
    conn.commit()
    conn.close()
    assert _names(live_db) == {"before", "after"}

    refused, message = restore_backup(
        info["path"], confirm=False, db_path=live_db, backup_dir=backup_dir
    )
    assert refused is False
    assert "confirmation" in message.lower()
    # Nothing changed without explicit confirmation.
    assert _names(live_db) == {"before", "after"}

    restored, message = restore_backup(
        info["path"], confirm=True, db_path=live_db, backup_dir=backup_dir
    )
    assert restored is True, message
    assert _names(live_db) == {"before"}

    backups = list_backups(backup_dir)
    assert any("pre_restore" in item["name"] for item in backups)


def test_verify_rejects_non_database_file(tmp_path):
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a sqlite database")
    ok, detail = verify_backup(broken)
    assert ok is False
    assert "not a readable SQLite database" in detail


def test_scheduled_backup_runs_at_most_once_per_interval(tmp_path):
    live_db = tmp_path / "live.db"
    backup_dir = tmp_path / "scheduled"
    _make_database(live_db)

    first = maybe_run_scheduled_backup(24.0, db_path=live_db, backup_dir=backup_dir)
    assert first is not None and first["integrity_ok"] is True

    second = maybe_run_scheduled_backup(24.0, db_path=live_db, backup_dir=backup_dir)
    assert second is None

    forced = maybe_run_scheduled_backup(0.0, db_path=live_db, backup_dir=backup_dir)
    assert forced is not None


def _chapter(project_id: int) -> int:
    with transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO chapters (project_id, chapter_number, title, content, word_count)
            VALUES (?, 1, 'Chapter One', 'Published content.', 2)
            """,
            (project_id,),
        )
        return cur.lastrowid


def test_autosave_roundtrip_recovery_and_clear():
    project_id = ProjectService.create_project(ProjectCreate(name="Autosave Test"))
    chapter_id = _chapter(project_id)

    assert AutosaveService.get_draft(chapter_id) is None
    assert AutosaveService.draft_status(chapter_id) == "IDLE"

    ok, message = AutosaveService.save_draft(chapter_id, project_id, "Draft content")
    assert ok, message
    draft = AutosaveService.get_draft(chapter_id)
    assert draft["draft_content"] == "Draft content"
    assert draft["status"] == SAVED

    assert AutosaveService.recoverable_draft(chapter_id, "Published content.") is not None
    assert AutosaveService.recoverable_draft(chapter_id, "Draft content") is None

    assert AutosaveService.clear_draft(chapter_id) is True
    assert AutosaveService.get_draft(chapter_id) is None
    ProjectService.delete_project(project_id)


def test_autosave_debounce_helper():
    assert AutosaveService.should_save("old", "old") is False
    assert AutosaveService.should_save("old", "new", force=True) is True
    assert (
        AutosaveService.should_save(
            "old", "new", last_attempt_at=100.0, now=100.5, min_interval_seconds=1.5
        )
        is False
    )
    assert (
        AutosaveService.should_save(
            "old", "new", last_attempt_at=100.0, now=102.0, min_interval_seconds=1.5
        )
        is True
    )


def test_autosave_if_changed_reports_saved_status():
    project_id = ProjectService.create_project(ProjectCreate(name="Autosave Status Test"))
    chapter_id = _chapter(project_id)

    result = AutosaveService.autosave_if_changed(
        chapter_id, project_id, "Fresh draft", "Published content."
    )
    assert result["attempted"] is True
    assert result["success"] is True
    assert result["status"] == SAVED
    assert AutosaveService.draft_status(chapter_id) == SAVED

    unchanged = AutosaveService.autosave_if_changed(
        chapter_id, project_id, "Fresh draft", "Fresh draft"
    )
    assert unchanged["attempted"] is False

    ProjectService.delete_project(project_id)


def test_autosave_status_labels():
    assert "Saving" in AutosaveService.status_label(SAVING)
    assert "autosaved" in AutosaveService.status_label(SAVED)
    assert "failed" in AutosaveService.status_label(FAILED)


def test_delete_project_creates_pre_delete_backup():
    project_id = ProjectService.create_project(ProjectCreate(name="Pre Delete Backup Test"))
    before = {item["name"] for item in list_backups()}
    assert ProjectService.delete_project(project_id) is True
    after = {item["name"] for item in list_backups()}
    new_backups = after - before
    assert any("pre_delete" in name for name in new_backups), new_backups
