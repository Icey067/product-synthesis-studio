"""Canonical manuscript completeness and publication gating.

This module is the **single source of truth** for every manuscript statistic
shown in the application.  Dashboards, the chapter builder, the quality auditor
and the exporters all call :meth:`ManuscriptService.assess_project` (or the
lower-level :meth:`assess`) so they can never disagree about how many chapters
are required, written, long enough, audited, or publication-ready.

All counts are **project-scoped**.  A global chapter total (for example
"12 chapters with content across the database") must never be used as the
denominator for a project report.

Definitions
-----------
required chapter count
    The project's configured ``required_chapter_count``.
existing chapter count
    Chapter rows that belong to the project (the outline).
written chapter count
    Existing chapters whose stored content is non-empty (actual word count > 0).
minimum-word-compliant count
    Written chapters whose actual word count meets the project's configured
    ``min_chapter_words``.
audit-approved count
    Chapters whose most recent audit is an independent, deterministic PASS and
    does not require human review.  Legacy model-only reports never count.
human-review-required count
    Chapters whose most recent audit flagged ``human_review_required``.
publication-ready count
    Chapters that are minimum-word compliant, structurally valid, citation-clean
    and audit-approved.  Only these may appear in a full publication.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config.settings import (
    DEFAULT_CHAPTER_TARGET_WORDS,
    DEFAULT_MIN_CHAPTER_WORDS,
    DEFAULT_REQUIRED_CHAPTER_COUNT,
)
from database.db import get_connection
from services.chapter_contract import count_words
from services.citation_service import CitationService

STATUS_DRAFT = "WORKING_DRAFT"
STATUS_COMPLETED = "COMPLETED_MANUSCRIPT"
STATUS_READY = "PUBLICATION_READY"

# Validation states that do not disqualify a chapter structurally.
_STRUCTURE_OK_STATES = (None, "", "PENDING", "COMPLETE")


class ManuscriptService:
    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _latest_audit(chapter_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT status, score, human_review_required, deterministic_checks_json
                FROM quality_reports
                WHERE chapter_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (chapter_id,),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _audit_is_verified(audit: Optional[Dict[str, Any]]) -> bool:
        """A PASS only counts if the independent deterministic auditor produced it.

        Reports created before the auditor existed contain only a model opinion and
        an empty ``deterministic_checks_json``. They cannot approve a chapter.
        """
        if not audit:
            return False
        if str(audit.get("status", "")).upper() != "PASS":
            return False
        if audit.get("human_review_required"):
            return False
        raw = audit.get("deterministic_checks_json")
        if not raw:
            return False
        try:
            checks = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return False
        return isinstance(checks, dict) and bool(checks.get("counts"))

    @staticmethod
    def _audit_requires_human(audit: Optional[Dict[str, Any]]) -> bool:
        if not audit:
            return False
        if bool(audit.get("human_review_required")):
            return True
        return str(audit.get("status", "")).upper() == "REQUIRES_HUMAN_VERIFICATION"

    @classmethod
    def chapter_is_approved(cls, chapter_id: int) -> bool:
        """Public helper used by the editor before overwriting a chapter."""
        return cls._audit_is_verified(cls._latest_audit(chapter_id))

    @classmethod
    def chapter_requires_human_review(cls, chapter_id: int) -> bool:
        return cls._audit_requires_human(cls._latest_audit(chapter_id))

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @staticmethod
    def _settings(project_id: Optional[int], overrides: Optional[Dict[str, int]] = None) -> Dict[str, int]:
        settings = {
            "target_words": int(DEFAULT_CHAPTER_TARGET_WORDS),
            "min_words": int(DEFAULT_MIN_CHAPTER_WORDS),
            "required_chapters": int(DEFAULT_REQUIRED_CHAPTER_COUNT),
        }
        if project_id is not None:
            try:
                from services.project_service import ProjectService

                settings.update(ProjectService.get_chapter_settings(project_id))
            except Exception:
                pass
        if overrides:
            for key in ("target_words", "min_words", "required_chapters"):
                if overrides.get(key) is not None:
                    settings[key] = int(overrides[key])
        settings["min_words"] = max(1, min(settings["min_words"], settings["target_words"]))
        settings["required_chapters"] = max(1, settings["required_chapters"])
        return settings

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    @classmethod
    def assess_project(cls, project_id: int, require_approval: bool = True) -> Dict[str, Any]:
        """Assess a project directly from the database.

        This is the only function UI code should call.  It performs a fresh,
        project-scoped read; it never mixes in global totals.
        """
        from services.outline_service import OutlineService
        from services.research_service import ResearchService

        chapters = OutlineService.get_chapters(project_id)
        sources = ResearchService.get_sources(project_id)
        return cls.assess(
            project_id,
            chapters,
            sources,
            require_approval=require_approval,
        )

    @classmethod
    def assess(
        cls,
        project_id: Optional[int],
        chapters: Optional[List[Dict[str, Any]]] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        target_word_count: Optional[int] = None,
        require_approval: bool = True,
        settings: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """Compute the canonical manuscript report.

        ``chapters``/``sources`` may be supplied by callers that already loaded
        them (the exporters do this), but the scope is always the given project.
        """
        if chapters is None:
            from services.outline_service import OutlineService

            chapters = OutlineService.get_chapters(project_id) if project_id else []
        if sources is None:
            from services.research_service import ResearchService

            sources = ResearchService.get_sources(project_id) if project_id else []

        resolved = cls._settings(project_id, settings)
        if target_word_count is not None:
            resolved["target_words"] = int(target_word_count)
            if resolved["min_words"] > resolved["target_words"]:
                resolved["min_words"] = resolved["target_words"]

        target = resolved["target_words"]
        min_words = resolved["min_words"]
        required = resolved["required_chapters"]

        total = len(chapters)
        complete_chapters: List[int] = []
        written_chapters: List[int] = []
        min_word_chapters: List[int] = []
        approved_chapters: List[int] = []
        human_review_chapters: List[int] = []
        publication_ready_chapters: List[int] = []
        missing_chapters: List[Dict[str, Any]] = []
        unapproved: List[int] = []
        citation_issues: List[Dict[str, Any]] = []
        chapter_details: List[Dict[str, Any]] = []

        for ch in chapters:
            number = int(ch.get("chapter_number", 0))
            content = ch.get("content") or ""
            words = count_words(content)
            validation_status = ch.get("validation_status")
            length_ok = words >= min_words and len(content.strip()) > 100
            structure_ok = validation_status in _STRUCTURE_OK_STATES
            is_complete = length_ok and structure_ok
            is_written = bool(content.strip()) and words > 0
            is_long_enough = is_written and words >= min_words

            audit = cls._latest_audit(ch["id"]) if ch.get("id") else None
            approved = cls._audit_is_verified(audit)
            needs_human = cls._audit_requires_human(audit)

            citations = (
                CitationService.verify_chapter_citations(project_id, content)
                if content and project_id is not None
                else {"total_citations": 0, "invalid_citations": [], "valid_citations": []}
            )
            chapter_citation_ok = True
            if is_complete:
                if citations["invalid_citations"]:
                    chapter_citation_ok = False
                    citation_issues.append({
                        "chapter": number,
                        "issue": "invalid_citations",
                        "detail": citations["invalid_citations"],
                    })
                elif sources and citations["total_citations"] == 0:
                    chapter_citation_ok = False
                    citation_issues.append({
                        "chapter": number,
                        "issue": "no_citations",
                        "detail": "No [Source-ID] citations although sources exist.",
                    })

            if is_written:
                written_chapters.append(number)
            if is_long_enough:
                min_word_chapters.append(number)
            if is_complete:
                complete_chapters.append(number)
            else:
                missing_chapters.append({
                    "chapter": number,
                    "title": ch.get("title", ""),
                    "words": words,
                    "required_words": min_words,
                    "reason": (
                        f"Only {words} words (minimum {min_words})" if not length_ok
                        else f"Structure not validated ({validation_status})"
                    ),
                })
            if approved:
                approved_chapters.append(number)
            if needs_human:
                human_review_chapters.append(number)
            if is_complete and approved and chapter_citation_ok:
                publication_ready_chapters.append(number)
            if is_complete and not approved:
                unapproved.append(number)

            chapter_details.append({
                "chapter": number,
                "chapter_id": ch.get("id"),
                "title": ch.get("title", ""),
                "words": words,
                "target_words": int(ch.get("target_word_count") or target),
                "planned_words": int(ch.get("planned_word_count") or ch.get("target_word_count") or target),
                "actual_words": words,
                "min_words": min_words,
                "written": is_written,
                "min_word_compliant": is_long_enough,
                "complete": is_complete,
                "structural_complete": bool(ch.get("structural_complete")) or (structure_ok and is_written),
                "evidence_complete": bool(citations["total_citations"]) and not citations["invalid_citations"],
                "editorial_status": ch.get("editorial_status") or "NOT_REVIEWED",
                "approval_status": "APPROVED" if approved else ("HUMAN_REVIEW" if needs_human else "UNREVIEWED"),
                "approved": approved,
                "human_review_required": needs_human,
                "publication_ready": is_complete and approved and chapter_citation_ok,
                "validation_status": validation_status or "PENDING",
                "citations": citations,
                "citation_ok": chapter_citation_ok,
            })

        # Required chapters that the outline does not contain at all.
        available_numbers = {int(c.get("chapter_number", 0)) for c in chapters}
        missing_required_numbers = sorted(set(range(1, required + 1)) - available_numbers)
        for number in missing_required_numbers:
            missing_chapters.append({
                "chapter": number,
                "title": "",
                "words": 0,
                "required_words": min_words,
                "reason": "Required chapter is not present in the outline.",
            })

        numbers = [int(c.get("chapter_number", 0)) for c in chapters]
        expected_numbers = list(range(1, total + 1))
        ordering_ok = numbers == expected_numbers
        titles = [str(c.get("title", "")).strip() for c in chapters]
        toc_ok = bool(total) and all(titles) and len(set(titles)) == total

        all_complete = total > 0 and len(complete_chapters) == total and not missing_required_numbers
        all_approved = all_complete and not unapproved
        citations_ok = not citation_issues
        all_publication_ready = (
            all_complete
            and len(publication_ready_chapters) == total
            and not missing_required_numbers
        )

        if (
            all_complete
            and all_publication_ready
            and ordering_ok
            and toc_ok
            and citations_ok
            and (all_approved or not require_approval)
        ):
            status = STATUS_READY
        elif all_complete and ordering_ok and toc_ok and citations_ok:
            status = STATUS_COMPLETED
        else:
            status = STATUS_DRAFT

        if status == STATUS_READY:
            label = f"PUBLICATION READY — {total}/{total} CHAPTERS COMPLETE AND APPROVED"
        elif status == STATUS_COMPLETED:
            label = f"COMPLETED MANUSCRIPT — {total}/{total} CHAPTERS WRITTEN, NOT YET APPROVED"
        else:
            label = f"DRAFT — {len(complete_chapters)}/{required} CHAPTERS COMPLETE"

        blocking: List[str] = []
        if missing_required_numbers:
            blocking.append(
                "Required chapters not present in the outline: "
                + ", ".join(str(n) for n in missing_required_numbers)
                + "."
            )
        if missing_chapters:
            incomplete = [m for m in missing_chapters if m["chapter"] in available_numbers]
            if incomplete:
                nums = ", ".join(str(m["chapter"]) for m in incomplete)
                blocking.append(f"Missing or incomplete chapters: {nums}.")
                for m in incomplete:
                    blocking.append(f"Chapter {m['chapter']} ({m['title']}): {m['reason']}.")
        if human_review_chapters:
            blocking.append(
                "Chapters requiring human review before publication: "
                + ", ".join(str(n) for n in sorted(human_review_chapters)) + "."
            )
        if unapproved and not human_review_chapters:
            blocking.append(
                "Chapters without a verified PASS audit (re-run the independent auditor): "
                + ", ".join(str(n) for n in sorted(unapproved)) + "."
            )
        if unapproved and human_review_chapters:
            remaining = sorted(set(unapproved) - set(human_review_chapters))
            if remaining:
                blocking.append(
                    "Chapters without a verified PASS audit (re-run the independent auditor): "
                    + ", ".join(str(n) for n in remaining) + "."
                )
        for ci in citation_issues:
            blocking.append(f"Chapter {ci['chapter']} citation problem: {ci['issue']} {ci['detail']}.")
        if not ordering_ok:
            blocking.append(f"Chapter order is invalid: found {numbers}, expected {expected_numbers}.")
        if not toc_ok:
            blocking.append("Table of contents is invalid: titles are empty or duplicated.")

        counts = {
            "required_chapters": required,
            "existing_chapters": total,
            "written_chapters": len(written_chapters),
            "min_word_compliant_chapters": len(min_word_chapters),
            "audit_approved_chapters": len(approved_chapters),
            "human_review_required_chapters": len(human_review_chapters),
            "publication_ready_chapters": len(publication_ready_chapters),
        }

        return {
            "status": status,
            "label": label,
            "can_publish": status == STATUS_READY,
            "project_id": project_id,
            # Canonical counts
            "required_chapters": required,
            "existing_chapters": total,
            "total_chapters": total,  # backwards-compatible alias
            "written_count": len(written_chapters),
            "written_chapters": sorted(written_chapters),
            "min_word_compliant_count": len(min_word_chapters),
            "min_word_compliant_chapters": sorted(min_word_chapters),
            "audit_approved_count": len(approved_chapters),
            "audit_approved_chapters": sorted(approved_chapters),
            "human_review_required_count": len(human_review_chapters),
            "human_review_required_chapters": sorted(human_review_chapters),
            "publication_ready_count": len(publication_ready_chapters),
            "publication_ready_chapters": sorted(publication_ready_chapters),
            "counts": counts,
            # Backwards-compatible completeness fields
            "complete_chapters": sorted(complete_chapters),
            "complete_count": len(complete_chapters),
            "missing_chapters": missing_chapters,
            "missing_required_chapters": missing_required_numbers,
            "unapproved_chapters": sorted(unapproved),
            "citation_issues": citation_issues,
            "ordering_ok": ordering_ok,
            "toc_ok": toc_ok,
            "all_approved": all_approved,
            "blocking_reasons": blocking,
            "chapter_details": chapter_details,
            "total_words": sum(int(d["words"]) for d in chapter_details),
            "target_words": target,
            "min_chapter_words": min_words,
            "settings": resolved,
        }
