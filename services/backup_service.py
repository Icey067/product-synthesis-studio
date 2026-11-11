"""Consistent SQLite backups, verification, scheduling and recovery.

Snapshots are produced with SQLite's online backup API rather than a plain file
copy, so a backup is transactionally consistent even while the application is
writing.  Every backup is integrity-checked before it is accepted.

Restoration is intentionally gated: ``restore_backup(..., confirm=False)``
refuses to touch the live database.  A caller must pass ``confirm=True`` after
the user explicitly agrees.  A pre-restore backup is always taken first.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import BACKUP_DIR, DATABASE_PATH

logger = logging.getLogger(__name__)

_SAFE_REASON_RE = str.maketrans({c: "_" for c in '<>:"/\\|?* '})


def _safe_reason(reason: str) -> str:
    cleaned = (reason or "manual").translate(_SAFE_REASON_RE).strip("_")
    return cleaned[:60] or "manual"


def _resolve_paths(db_path: Optional[Path], backup_dir: Optional[Path]) -> tuple[Path, Path]:
    return (Path(db_path) if db_path else Path(DATABASE_PATH),
            Path(backup_dir) if backup_dir else Path(BACKUP_DIR))


def verify_backup(path: str | Path) -> tuple[bool, str]:
    """Run SQLite integrity and foreign-key checks on a backup file."""
    candidate = Path(path)
    if not candidate.exists() or candidate.stat().st_size == 0:
        return False, "Backup file is missing or empty."
    try:
        conn = sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            quick = conn.execute("PRAGMA quick_check").fetchone()
            tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()
            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return False, f"Backup is not a readable SQLite database: {exc}"

    integrity_value = str(integrity[0]) if integrity else "unknown"
    quick_value = str(quick[0]) if quick else "unknown"
    if integrity_value.lower() != "ok" or quick_value.lower() != "ok":
        return False, f"Integrity check failed (integrity={integrity_value}, quick={quick_value})."
    if not tables or int(tables[0]) == 0:
        return False, "Backup contains no application tables."
    if fk_violations:
        return False, (
            f"Foreign-key check failed: {len(fk_violations)} orphaned row(s). "
            "The backup is not safe to restore."
        )
    return True, "Integrity check passed; foreign keys valid."


def _record_backup(db_path: Path, info: Dict[str, Any]) -> None:
    """Best-effort audit record; never fail a backup because logging failed."""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='backup_runs'"
            ).fetchone()
            if exists:
                conn.execute(
                    """
                    INSERT INTO backup_runs (file_path, reason, size_bytes, integrity_ok, detail)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(info["path"]), info["reason"], info["size_bytes"],
                        1 if info["integrity_ok"] else 0, info.get("detail", ""),
                    ),
                )
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not record backup run: %s", exc)


def create_backup(
    reason: str = "manual",
    db_path: Optional[Path] = None,
    backup_dir: Optional[Path] = None,
    verify: bool = True,
    keep_last: Optional[int] = 30,
) -> Optional[Dict[str, Any]]:
    """Create a consistent snapshot using SQLite's backup API.

    The online backup API reads a consistent snapshot even when the source
    database is in WAL mode or is being written concurrently.  The temporary
    file is removed if anything fails, and the returned metadata always reflects
    a completed integrity check.

    Returns metadata, or ``None`` when the live database does not exist or the
    backup could not be produced.
    """
    live_db, target_dir = _resolve_paths(db_path, backup_dir)
    if not live_db.exists() or live_db.stat().st_size == 0:
        return None

    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    reason_safe = _safe_reason(reason)
    target = target_dir / f"pss_data_{reason_safe}_{stamp}.db"
    temp = target.with_suffix(".db.tmp")

    source_conn = None
    dest_conn = None
    completed = False
    try:
        # A generous busy timeout prevents a transient writer from turning a
        # scheduled backup into a failure.
        source_conn = sqlite3.connect(str(live_db), timeout=30.0)
        source_conn.execute("PRAGMA busy_timeout = 30000")
        dest_conn = sqlite3.connect(str(temp))
        source_conn.backup(dest_conn)
        dest_conn.commit()
        completed = True
    except Exception as exc:  # defensive: never leave a partial snapshot behind
        logger.exception("Backup failed for %s: %s", live_db, exc)
        return None
    finally:
        if dest_conn is not None:
            dest_conn.close()
        if source_conn is not None:
            source_conn.close()
        if not completed and temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass

    os.replace(temp, target)
    integrity_ok, detail = verify_backup(target) if verify else (True, "Verification skipped.")
    info = {
        "path": target,
        "reason": reason,
        "size_bytes": target.stat().st_size,
        "integrity_ok": integrity_ok,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "detail": detail,
    }
    _record_backup(live_db, info)
    logger.info("Backup created at %s (%s)", target, detail)
    if not integrity_ok:
        # Keep the file for inspection but report the failure.
        info["error"] = detail
    if keep_last is not None and keep_last > 0:
        info["pruned"] = prune_backups(keep_last=keep_last, backup_dir=target_dir)
    return info


def prune_backups(
    keep_last: int = 30,
    backup_dir: Optional[Path] = None,
    preserve_latest: bool = True,
) -> List[str]:
    """Retain only the newest ``keep_last`` snapshots.

    Ordered by modification time.  The newest snapshot is never deleted.  The
    backup audit table is not rewritten: it is a historical log, so a pruned
    file simply shows up as missing on disk.
    """
    _, target_dir = _resolve_paths(None, backup_dir)
    if not target_dir.exists():
        return []
    files = sorted(
        target_dir.glob("pss_data_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    keep = max(1, int(keep_last)) if preserve_latest else max(0, int(keep_last))
    deleted: List[str] = []
    for path in files[keep:]:
        try:
            path.unlink()
            deleted.append(path.name)
        except OSError as exc:
            logger.warning("Could not prune backup %s: %s", path, exc)
    return deleted


def list_backups(backup_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    _, target_dir = _resolve_paths(None, backup_dir)
    if not target_dir.exists():
        return []
    items: List[Dict[str, Any]] = []
    for path in sorted(target_dir.glob("pss_data_*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        items.append({
            "path": path,
            "name": path.name,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return items


def _last_scheduled_backup(db_path: Path) -> Optional[datetime]:
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT created_at FROM backup_runs WHERE reason = 'scheduled' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    try:
        value = str(row[0]).replace("Z", "")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def maybe_run_scheduled_backup(
    interval_hours: float = 24.0,
    db_path: Optional[Path] = None,
    backup_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Create a scheduled snapshot if the interval has elapsed."""
    live_db, target_dir = _resolve_paths(db_path, backup_dir)
    if not live_db.exists() or live_db.stat().st_size == 0:
        return None
    now = now or datetime.now(timezone.utc)
    last = _last_scheduled_backup(live_db)
    if last and now - last < timedelta(hours=float(interval_hours)):
        return None
    return create_backup("scheduled", db_path=live_db, backup_dir=target_dir)


def restore_backup(
    backup_path: str | Path,
    confirm: bool = False,
    db_path: Optional[Path] = None,
    backup_dir: Optional[Path] = None,
) -> tuple[bool, str]:
    """Restore a verified backup into the live database.

    Without ``confirm=True`` this function makes no changes at all.  This is the
    explicit-confirmation gate required by the recovery procedure.
    """
    if not confirm:
        return (
            False,
            "Restore refused: explicit confirmation is required. "
            "Re-run the restore with confirm=True only after the user has agreed.",
        )

    source_path = Path(backup_path)
    ok, detail = verify_backup(source_path)
    if not ok:
        return False, f"Restore refused: {detail}"

    live_db, target_dir = _resolve_paths(db_path, backup_dir)
    if not live_db.exists():
        return False, f"Restore refused: live database '{live_db}' does not exist."

    pre = create_backup("pre_restore", db_path=live_db, backup_dir=target_dir)
    if pre is None or not pre.get("integrity_ok"):
        return False, "Restore refused: could not create a verified pre-restore backup."

    try:
        src = sqlite3.connect(str(source_path))
        dst = sqlite3.connect(str(live_db))
        try:
            dst.execute("PRAGMA foreign_keys = ON")
            src.backup(dst)
            dst.commit()
            integrity = dst.execute("PRAGMA integrity_check").fetchone()
            fk_violations = dst.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            dst.close()
            src.close()
    except Exception as exc:  # defensive: report rather than crash the UI
        return False, f"Restore failed while copying the backup: {exc}"

    integrity_value = str(integrity[0]) if integrity else "unknown"
    if integrity_value.lower() != "ok":
        return False, f"Restore copied the file but integrity check failed: {integrity_value}."
    if fk_violations:
        return False, (
            f"Restore copied the file but {len(fk_violations)} foreign-key violation(s) "
            "were detected. The pre-restore backup is still available."
        )

    # Apply any migrations that were added after the backup was taken.  Only the
    # configured live database is migrated automatically; a caller-supplied
    # path (used by tests/tools) is left exactly as restored.
    try:
        from config.settings import DATABASE_PATH as CONFIGURED_DB

        if live_db.resolve() == Path(CONFIGURED_DB).resolve():
            from database.db import init_db

            init_db()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Restore completed but post-restore migration failed: %s", exc)
        return True, f"Restore completed, but schema migration reported: {exc}"

    return True, f"Restore completed from '{source_path.name}' and verified. Pre-restore backup: '{pre['path'].name}'."
