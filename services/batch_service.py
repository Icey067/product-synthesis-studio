"""Batch chapter generation with explicit pause/resume and approved protection.

A batch session is persisted in ``batch_sessions`` so an interrupted run can be
resumed.  Section drafts are already persisted by the structured planner, so a
crash never loses completed work.  Approved or locked chapters are never
overwritten without an explicit ``force``/confirmation flag.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from database.db import get_connection, transaction
from services.chapter_planning_service import ChapterPlanningService
from services.generation_service import GenerationService
from services.manuscript_service import ManuscriptService
from services.ollama_client import OllamaClient
from services.outline_service import OutlineService
from services.research_service import ResearchService

PAUSED = "PAUSED"
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

ACTIVE_STATUSES = (RUNNING,)
RESUMABLE_STATUSES = (PAUSED, FAILED, RUNNING)


class BatchService:
    def __init__(
        self,
        ollama_client: Optional[OllamaClient] = None,
        planner: Optional[ChapterPlanningService] = None,
        generator: Optional[GenerationService] = None,
    ):
        self.ai = ollama_client or OllamaClient()
        self.planner = planner or ChapterPlanningService(ollama_client=self.ai)
        self.generator = generator or GenerationService(ollama_client=self.ai)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    @staticmethod
    def create_session(project_id: int, mode: str = "ALL_REMAINING", total: int = 0) -> int:
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO batch_sessions (project_id, status, mode, total_chapters)
                VALUES (?, ?, ?, ?)
                """,
                (project_id, RUNNING, mode, int(total)),
            )
            return int(cur.lastrowid)

    @staticmethod
    def get_session(session_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM batch_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def latest_session(project_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM batch_sessions WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def set_status(session_id: int, status: str) -> bool:
        with transaction() as conn:
            cur = conn.execute(
                "UPDATE batch_sessions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, session_id),
            )
            return cur.rowcount > 0

    def pause(self, session_id: int) -> bool:
        return self.set_status(session_id, PAUSED)

    def cancel(self, session_id: int) -> bool:
        return self.set_status(session_id, CANCELLED)

    @staticmethod
    def _update_progress(
        session_id: int,
        processed: int,
        failed: int,
        current: Optional[int],
        results: List[Dict[str, Any]],
    ) -> None:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE batch_sessions
                SET processed_chapters = ?, failed_chapters = ?, current_chapter = ?,
                    state_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (processed, failed, current, json.dumps(results), session_id),
            )

    @staticmethod
    def _status(session_id: int) -> str:
        session = BatchService.get_session(session_id)
        return (session or {}).get("status", "")

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------
    @staticmethod
    def recover_interrupted(project_id: int) -> List[Dict[str, Any]]:
        """Mark stale RUNNING sessions as PAUSED so they can be resumed.

        Returns the sessions that were recovered.
        """
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM batch_sessions WHERE project_id = ? AND status = ?",
                (project_id, RUNNING),
            ).fetchall()
            sessions = [dict(row) for row in rows]
            for session in sessions:
                conn.execute(
                    "UPDATE batch_sessions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (PAUSED, session["id"]),
                )
        for session in sessions:
            session["status"] = PAUSED
        return sessions

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------
    @staticmethod
    def progress(project_id: int, session_id: Optional[int] = None) -> Dict[str, Any]:
        chapters = OutlineService.get_chapters(project_id)
        sources = ResearchService.get_sources(project_id)
        with get_connection() as conn:
            traces = conn.execute(
                "SELECT chapter_id, COUNT(DISTINCT source_id) AS sources, "
                "COUNT(*) AS excerpts FROM chapter_sources WHERE project_id = ? "
                "AND source_id IS NOT NULL GROUP BY chapter_id",
                (project_id,),
            ).fetchall()
        trace_map = {int(r["chapter_id"]): dict(r) for r in traces}

        total = len(chapters)
        detail: List[Dict[str, Any]] = []
        written = 0
        complete = 0
        covered = 0
        total_words = 0
        for chapter in chapters:
            words = int(chapter.get("actual_word_count") or 0)
            if not words:
                words = len((chapter.get("content") or "").split())
            total_words += words
            is_written = words > 0
            is_complete = (chapter.get("validation_status") or "") == "COMPLETE"
            trace = trace_map.get(int(chapter["id"]), {})
            has_evidence = int(trace.get("sources") or 0) > 0
            written += 1 if is_written else 0
            complete += 1 if is_complete else 0
            covered += 1 if has_evidence else 0
            sections = ChapterPlanningService.section_progress(chapter["id"])
            detail.append(
                {
                    "chapter": chapter.get("chapter_number"),
                    "chapter_id": chapter.get("id"),
                    "title": chapter.get("title", ""),
                    "words": words,
                    "target_words": int(chapter.get("target_word_count") or 0),
                    "status": chapter.get("validation_status") or "PENDING",
                    "locked": bool(chapter.get("is_locked")),
                    "approved": ManuscriptService.chapter_is_approved(chapter["id"]),
                    "source_count": int(trace.get("sources") or 0),
                    "excerpt_count": int(trace.get("excerpts") or 0),
                    "sections": sections,
                }
            )

        session = BatchService.get_session(session_id) if session_id else BatchService.latest_session(project_id)
        return {
            "project_id": project_id,
            "session": session,
            "total_chapters": total,
            "written_chapters": written,
            "complete_chapters": complete,
            "remaining_chapters": max(0, total - complete),
            "research_covered_chapters": covered,
            "research_coverage_pct": round((covered / total) * 100, 1) if total else 0.0,
            "available_sources": len(sources),
            "total_words": total_words,
            "chapters": detail,
        }

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def _generate_one(
        self,
        project_id: int,
        chapter: Dict[str, Any],
        charter: Dict[str, Any],
        sources: List[Dict[str, Any]],
        structured: bool,
        force: bool,
        custom_instructions: str = "",
    ) -> Dict[str, Any]:
        if structured:
            result = self.planner.generate_chapter_structured(
                project_id,
                chapter,
                ai=self.ai,
                allow_approved_overwrite=force,
            )
            return {
                "chapter": chapter.get("chapter_number"),
                "chapter_id": chapter.get("id"),
                "status": "COMPLETE" if result.get("saved") else "FAILED",
                "saved": bool(result.get("saved")),
                "words": int((result.get("report") or {}).get("word_count") or 0),
                "target": int((result.get("report") or {}).get("target_words") or 0),
                "message": result.get("message", ""),
                "evidence": result.get("evidence", {}),
                "consistency": result.get("consistency", {}),
            }

        ok, content, report, message = self.generator.generate_chapter_validated(
            chapter, charter, sources, custom_instructions=custom_instructions
        )
        saved = False
        if ok and report is not None:
            saved = GenerationService.save_chapter_content(
                chapter["id"],
                content,
                note="Batch generation (validated)",
                validation=report,
                allow_approved_overwrite=force,
            )
        return {
            "chapter": chapter.get("chapter_number"),
            "chapter_id": chapter.get("id"),
            "status": (report or {}).get("status") if saved else "FAILED",
            "saved": saved,
            "words": int((report or {}).get("word_count") or 0),
            "target": int((report or {}).get("target_words") or 0),
            "message": message,
        }

    def run(
        self,
        project_id: int,
        session_id: Optional[int] = None,
        mode: str = "ALL_REMAINING",
        structured: bool = True,
        force: bool = False,
        max_chapters: Optional[int] = None,
        progress_cb: Optional[Callable[[int, int, Dict[str, Any], str], None]] = None,
        custom_instructions: str = "",
        chapter_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Generate remaining chapters, checking the persisted pause flag."""
        from services.charter_service import CharterService

        chapters = OutlineService.get_chapters(project_id)
        if not chapters:
            return {"ok": False, "message": "No outline exists. Generate an outline first.", "results": []}

        charter = CharterService.get_charter(project_id) or {}
        sources = ResearchService.get_sources(project_id)
        if session_id is None:
            session_id = self.create_session(project_id, mode=mode, total=len(chapters))
        else:
            self.set_status(session_id, RUNNING)

        results: List[Dict[str, Any]] = []
        processed = 0
        failed = 0
        produced = 0
        selected = set(chapter_ids or [])
        stop_reason = "COMPLETED"

        for index, chapter in enumerate(chapters):
            if selected and int(chapter["id"]) not in selected:
                continue
            if self._status(session_id) in (PAUSED, CANCELLED):
                stop_reason = self._status(session_id)
                break
            if max_chapters is not None and produced >= max_chapters:
                stop_reason = "LIMIT_REACHED"
                break

            if chapter.get("is_locked"):
                results.append({"chapter": chapter["chapter_number"], "status": "SKIPPED_LOCKED"})
                continue
            if not force and ManuscriptService.chapter_is_approved(chapter["id"]):
                results.append({"chapter": chapter["chapter_number"], "status": "SKIPPED_APPROVED"})
                continue
            if not force and (chapter.get("validation_status") or "") == "COMPLETE" and (
                chapter.get("content") or ""
            ).strip():
                results.append({"chapter": chapter["chapter_number"], "status": "ALREADY_COMPLETE"})
                continue

            try:
                result = self._generate_one(
                    project_id,
                    chapter,
                    charter,
                    sources,
                    structured=structured,
                    force=force,
                    custom_instructions=custom_instructions,
                )
            except Exception as exc:  # one bad chapter must not abort a batch
                result = {
                    "chapter": chapter.get("chapter_number"),
                    "chapter_id": chapter.get("id"),
                    "status": "FAILED",
                    "message": str(exc),
                }
            results.append(result)
            processed += 1
            if result.get("saved"):
                produced += 1
            else:
                failed += 1
            self._update_progress(session_id, processed, failed, chapter.get("chapter_number"), results)
            if progress_cb:
                progress_cb(index + 1, len(chapters), chapter, result.get("message", result.get("status", "")))

        if stop_reason == "COMPLETED":
            # Only mark completed if no PAUSED/CANCELLED request arrived at the end.
            final_status = COMPLETED if failed == 0 else FAILED
            self.set_status(session_id, final_status)
        elif stop_reason == "LIMIT_REACHED":
            self.set_status(session_id, PAUSED)
        else:
            self.set_status(session_id, stop_reason)

        progress = self.progress(project_id, session_id)
        return {
            "ok": True,
            "session_id": session_id,
            "status": self._status(session_id),
            "results": results,
            "produced": produced,
            "failed": failed,
            "stop_reason": stop_reason,
            "progress": progress,
            "message": (
                f"Batch {stop_reason.lower()}: {produced} chapter(s) written, "
                f"{failed} failed, {progress['remaining_chapters']} still incomplete."
            ),
        }

    def resume(
        self,
        project_id: int,
        session_id: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        session = self.get_session(session_id) if session_id else self.latest_session(project_id)
        if not session:
            return self.run(project_id, session_id=None, **kwargs)
        if session.get("status") not in RESUMABLE_STATUSES:
            return {
                "ok": False,
                "message": f"Session {session['id']} is {session.get('status')} and cannot be resumed.",
                "results": [],
            }
        return self.run(project_id, session_id=int(session["id"]), **kwargs)

    def retry_chapter(
        self,
        project_id: int,
        chapter_id: int,
        session_id: Optional[int] = None,
        structured: bool = True,
        force: bool = False,
    ) -> Dict[str, Any]:
        chapter = next(
            (c for c in OutlineService.get_chapters(project_id) if int(c["id"]) == int(chapter_id)),
            None,
        )
        if not chapter:
            return {"ok": False, "message": "Chapter not found.", "results": []}
        return self.run(
            project_id,
            session_id=session_id,
            mode="RETRY",
            structured=structured,
            force=force,
            chapter_ids=[int(chapter_id)],
        )

    def regenerate_section(
        self,
        project_id: int,
        chapter_id: int,
        section_key: str,
        force: bool = False,
    ) -> Dict[str, Any]:
        return self.planner.generate_section(
            project_id,
            chapter_id,
            section_key,
            force=True,
            allow_approved_overwrite=force,
        )

    @staticmethod
    def lock_chapter(chapter_id: int, locked: bool = True) -> bool:
        return GenerationService.set_lock_status(chapter_id, locked)
