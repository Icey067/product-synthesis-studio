"""Debounced, recoverable chapter-editor autosave.

The editor keeps a per-chapter draft in ``chapter_autosaves``.  A draft is saved
after the user stops typing (debounce), never on every keystroke, and its state
is always one of:

* ``SAVING``  - a save is in progress,
* ``SAVED``   - the draft is safely stored,
* ``FAILED``  - the save failed and the error is retained for the UI.

The draft is separate from the published chapter content, so an accidental
navigation or crash can be recovered without overwriting a validated chapter.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, Optional

from database.db import get_connection, transaction

SAVING = "SAVING"
SAVED = "SAVED"
FAILED = "FAILED"

DEFAULT_DEBOUNCE_SECONDS = 1.5


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class AutosaveService:
    @staticmethod
    def mark_saving(chapter_id: int, project_id: int, content: str) -> None:
        """Record an in-progress save so the UI can show 'Saving…'."""
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO chapter_autosaves
                    (chapter_id, project_id, draft_content, status, error_message, updated_at)
                VALUES (?, ?, ?, ?, NULL, CURRENT_TIMESTAMP)
                ON CONFLICT(chapter_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    draft_content = excluded.draft_content,
                    status = excluded.status,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chapter_id, project_id, content, SAVING),
            )

    @staticmethod
    def save_draft(chapter_id: int, project_id: int, content: str) -> tuple[bool, str]:
        """Persist the draft.  Returns ``(success, message)``."""
        if content is None:
            content = ""
        try:
            with transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO chapter_autosaves
                        (chapter_id, project_id, draft_content, status, error_message, updated_at)
                    VALUES (?, ?, ?, ?, NULL, CURRENT_TIMESTAMP)
                    ON CONFLICT(chapter_id) DO UPDATE SET
                        project_id = excluded.project_id,
                        draft_content = excluded.draft_content,
                        status = excluded.status,
                        error_message = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (chapter_id, project_id, content, SAVED),
                )
            return True, "Draft autosaved."
        except Exception as exc:  # pragma: no cover - defensive
            AutosaveService.record_failure(chapter_id, project_id, content, str(exc))
            return False, f"Autosave failed: {exc}"

    @staticmethod
    def record_failure(chapter_id: int, project_id: int, content: str, error: str) -> None:
        """Best-effort FAILED marker.  Never raises into the UI."""
        try:
            with transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO chapter_autosaves
                        (chapter_id, project_id, draft_content, status, error_message, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(chapter_id) DO UPDATE SET
                        project_id = excluded.project_id,
                        draft_content = excluded.draft_content,
                        status = excluded.status,
                        error_message = excluded.error_message,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (chapter_id, project_id, content or "", FAILED, error or "Unknown error"),
                )
        except Exception:
            pass

    @staticmethod
    def get_draft(chapter_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM chapter_autosaves WHERE chapter_id = ?", (chapter_id,)
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_project_drafts(project_id: int) -> list[Dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chapter_autosaves WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def clear_draft(chapter_id: int) -> bool:
        with transaction() as conn:
            cur = conn.execute("DELETE FROM chapter_autosaves WHERE chapter_id = ?", (chapter_id,))
            return cur.rowcount > 0

    @staticmethod
    def recoverable_draft(chapter_id: int, current_content: str = "") -> Optional[Dict[str, Any]]:
        """Return a draft only when it differs from the published content."""
        draft = AutosaveService.get_draft(chapter_id)
        if not draft:
            return None
        if (draft.get("draft_content") or "") == (current_content or ""):
            return None
        if not (draft.get("draft_content") or "").strip():
            return None
        return draft

    @staticmethod
    def draft_status(chapter_id: int) -> str:
        draft = AutosaveService.get_draft(chapter_id)
        return (draft or {}).get("status") or "IDLE"

    @staticmethod
    def should_save(
        last_saved_content: str,
        current_content: str,
        last_attempt_at: Optional[float] = None,
        now: Optional[float] = None,
        min_interval_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        force: bool = False,
    ) -> bool:
        """Debounce helper used by the editor.

        A save is due only when the content changed, and (unless forced) not
        sooner than ``min_interval_seconds`` after the previous attempt.
        """
        if (current_content or "") == (last_saved_content or ""):
            return False
        if force:
            return True
        if last_attempt_at is not None:
            now = now if now is not None else time.monotonic()
            if now - last_attempt_at < min_interval_seconds:
                return False
        return True

    @staticmethod
    def autosave_if_changed(
        chapter_id: int,
        project_id: int,
        content: str,
        last_saved_content: str,
        last_attempt_at: Optional[float] = None,
        now: Optional[float] = None,
        min_interval_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Debounced save with an explicit result for UI status rendering."""
        result: Dict[str, Any] = {
            "attempted": False,
            "success": False,
            "status": AutosaveService.draft_status(chapter_id),
            "message": "No changes to autosave.",
            "content_hash": content_hash(content),
            "next_attempt_at": last_attempt_at,
        }
        if not AutosaveService.should_save(
            last_saved_content,
            content,
            last_attempt_at=last_attempt_at,
            now=now,
            min_interval_seconds=min_interval_seconds,
            force=force,
        ):
            return result

        now_value = now if now is not None else time.monotonic()
        result["attempted"] = True
        result["next_attempt_at"] = now_value
        AutosaveService.mark_saving(chapter_id, project_id, content)
        try:
            ok, message = AutosaveService.save_draft(chapter_id, project_id, content)
        except Exception as exc:  # defensive: never let a save error break the editor
            AutosaveService.record_failure(chapter_id, project_id, content, str(exc))
            ok, message = False, f"Autosave failed: {exc}"
        result.update(
            {
                "success": ok,
                "status": SAVED if ok else FAILED,
                "message": message,
                "saved_content": content if ok else last_saved_content,
            }
        )
        return result

    @staticmethod
    def has_conflict(
        base_content: Optional[str],
        published_content: Optional[str],
        draft_content: Optional[str],
    ) -> bool:
        """Detect an external edit while a local draft is in progress.

        A conflict exists when the published chapter changed since the draft was
        started and the local draft differs from the new published text.  The
        caller must resolve it explicitly; autosave never overwrites published
        content.
        """
        if base_content is None:
            return False
        published = published_content or ""
        base = base_content or ""
        draft = draft_content or ""
        return published != base and draft.strip() != "" and draft != published

    @staticmethod
    def failed_recovery(chapter_id: int) -> Optional[Dict[str, Any]]:
        """Return a FAILED draft together with its error for UI recovery."""
        draft = AutosaveService.get_draft(chapter_id)
        if draft and str(draft.get("status", "")).upper() == FAILED:
            return draft
        return None

    @staticmethod
    def status_label(status: str) -> str:
        return {
            SAVING: "⏳ Saving…",
            SAVED: "✅ Draft autosaved",
            FAILED: "⚠️ Autosave failed",
        }.get(str(status or "").upper(), "• Not yet autosaved")
