"""Persisted book architecture and chapter coverage matrix (P2.2).

Before a book is written, every chapter receives a distinct purpose, learning
outcome, main question, required topics, required research, exercises and
examples, plus an explicit list of material already explained elsewhere and
material it must not repeat.

The architecture is derived deterministically from the outline, charter and
transformation map.  A model may be used later to *suggest* refinements, but it
cannot mark overlapping chapters as unique.  Purpose overlap is measured in
code.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from database.db import get_connection, transaction
from services.evidence_service import normalize_text
from services.research_service import tokenize

PURPOSE_OVERLAP_THRESHOLD = 0.7
FRAMEWORK_STAGES = ["Diagnose", "Design", "Implement", "Sustain"]


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
        return [value.strip()]
    return []


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _purpose_text(chapter: Dict[str, Any]) -> str:
    parts = [
        str(chapter.get("purpose") or "").strip(),
        str(chapter.get("title") or "").strip(),
        " ".join(_as_list(chapter.get("key_topics"))),
        " ".join(_as_list(chapter.get("learning_objectives"))),
    ]
    return " ".join(p for p in parts if p)


def _main_question(chapter: Dict[str, Any]) -> str:
    purpose = str(chapter.get("purpose") or "").strip()
    if purpose:
        if purpose.endswith("?"):
            return purpose
        return f"How does the reader {purpose[0].lower() + purpose[1:] if len(purpose) > 1 else purpose}?"
    return f"What must the reader understand and do in '{chapter.get('title', 'this chapter')}'?"


class BookArchitectureService:
    @staticmethod
    def _chapters(project_id: int) -> List[Dict[str, Any]]:
        from services.outline_service import OutlineService

        return OutlineService.get_chapters(project_id)

    @staticmethod
    def _transformation(project_id: int) -> Dict[str, Any]:
        try:
            from services.transformation_service import TransformationService

            return TransformationService.get_map(project_id) or {}
        except Exception:
            return {}

    @classmethod
    def build(
        cls,
        project_id: int,
        chapters: Optional[List[Dict[str, Any]]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        if not force:
            existing = cls.get(project_id)
            if existing:
                return existing

        chapters = chapters if chapters is not None else cls._chapters(project_id)
        chapters = sorted(chapters, key=lambda c: int(c.get("chapter_number") or 0))
        transformation = cls._transformation(project_id)
        before = transformation.get("before_situation") or {}
        after = transformation.get("after_situation") or {}

        entries: List[Dict[str, Any]] = []
        seen_topics: List[str] = []
        seen_titles: List[str] = []
        total = max(1, len(chapters))
        for index, chapter in enumerate(chapters):
            sequence = int(chapter.get("chapter_number") or index + 1)
            topics = _as_list(chapter.get("key_topics"))
            objectives = _as_list(chapter.get("learning_objectives"))
            exercises = _as_list(chapter.get("practical_exercises"))
            purpose = str(chapter.get("purpose") or "").strip()
            if not purpose:
                purpose = (
                    f"Help the reader apply {chapter.get('title', 'this topic')} "
                    f"to their own situation and produce a concrete next action."
                )
            stage = FRAMEWORK_STAGES[min(len(FRAMEWORK_STAGES) - 1, int(index * len(FRAMEWORK_STAGES) / total))]
            entries.append(
                {
                    "chapter_id": chapter.get("id"),
                    "chapter_number": sequence,
                    "title": chapter.get("title", ""),
                    "unique_purpose": purpose,
                    "learning_outcome": str(chapter.get("expected_outcome") or "").strip()
                    or (objectives[0] if objectives else f"The reader can apply {chapter.get('title', 'the chapter')}."),
                    "main_question_answered": _main_question(chapter),
                    "required_topics": topics,
                    "required_research": topics
                    + [f"evidence for {chapter.get('title', '')}", "current local market evidence"],
                    "practical_exercises": exercises
                    or [f"Complete a short self-assessment for {chapter.get('title', 'this chapter')}."],
                    "examples_to_use": [
                        f"An explicitly illustrative scenario specific to '{chapter.get('title', 'this chapter')}'",
                        "A reader worksheet rather than a fabricated customer story",
                    ],
                    "topics_already_explained": list(seen_topics),
                    "must_not_repeat": list(seen_topics) + list(seen_titles),
                    "transformation_stage": stage,
                    "before_state": before.get("current_situation")
                    or before.get("situation")
                    or before.get("description")
                    or "",
                    "after_state": after.get("desired_situation")
                    or after.get("situation")
                    or after.get("description")
                    or "",
                    "purpose_tokens": sorted(tokenize(_purpose_text(chapter))),
                }
            )
            for topic in topics:
                if topic not in seen_topics:
                    seen_topics.append(topic)
            title = str(chapter.get("title") or "").strip()
            if title:
                seen_titles.append(title)

        matrix: List[Dict[str, Any]] = []
        overlaps: List[Dict[str, Any]] = []
        for index, entry in enumerate(entries):
            entry_tokens = set(entry.pop("purpose_tokens", []))
            entry["purpose_fingerprint"] = normalize_text(entry["unique_purpose"])
            overlap_with: List[Dict[str, Any]] = []
            for other in entries:
                if other is entry:
                    continue
                other_tokens = set(tokenize(_purpose_text(other)))
                similarity = _jaccard(entry_tokens, other_tokens)
                same_purpose = normalize_text(entry["unique_purpose"]) == normalize_text(other["unique_purpose"])
                shared_topics = sorted(set(entry["required_topics"]) & set(other["required_topics"]))
                if same_purpose or similarity >= PURPOSE_OVERLAP_THRESHOLD:
                    record = {
                        "chapter": entry["chapter_number"],
                        "overlaps_with": other["chapter_number"],
                        "similarity": round(similarity, 3) if not same_purpose else 1.0,
                        "shared_topics": shared_topics,
                        "reason": (
                            "Identical purpose text"
                            if same_purpose
                            else "Purpose/topics are substantially similar"
                        ),
                    }
                    overlap_with.append(record)
                    if entry["chapter_number"] < other["chapter_number"]:
                        overlaps.append(record)
            matrix.append(
                {
                    "chapter": entry["chapter_number"],
                    "title": entry["title"],
                    "unique_purpose": entry["unique_purpose"],
                    "learning_outcome": entry["learning_outcome"],
                    "main_question_answered": entry["main_question_answered"],
                    "required_topics": entry["required_topics"],
                    "required_research": entry["required_research"],
                    "practical_exercises": entry["practical_exercises"],
                    "examples_to_use": entry["examples_to_use"],
                    "topics_already_explained": entry["topics_already_explained"],
                    "must_not_repeat": entry["must_not_repeat"],
                    "transformation_stage": entry["transformation_stage"],
                    "overlaps_with": overlap_with,
                    "unique": not overlap_with,
                }
            )

        architecture = {
            "project_id": project_id,
            "chapters": entries,
            "coverage_matrix": matrix,
            "overlaps": overlaps,
            "status": "APPROVED_ARCHITECTURE" if not overlaps else "NEEDS_REVISION",
            "overlap_count": len(overlaps),
            "purpose_overlap_threshold": PURPOSE_OVERLAP_THRESHOLD,
        }
        cls._save(project_id, architecture)
        return architecture

    @staticmethod
    def _save(project_id: int, architecture: Dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO book_architectures (
                    project_id, architecture_json, coverage_matrix_json, status, overlap_count
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    architecture_json = excluded.architecture_json,
                    coverage_matrix_json = excluded.coverage_matrix_json,
                    status = excluded.status,
                    overlap_count = excluded.overlap_count,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project_id,
                    json.dumps({k: v for k, v in architecture.items() if k != "coverage_matrix"}),
                    json.dumps(architecture.get("coverage_matrix") or []),
                    architecture.get("status", "PLANNED"),
                    int(architecture.get("overlap_count") or 0),
                ),
            )

    @staticmethod
    def get(project_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM book_architectures WHERE project_id = ?", (project_id,)
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                architecture = json.loads(data.get("architecture_json") or "{}")
            except (TypeError, ValueError):
                architecture = {}
            try:
                architecture["coverage_matrix"] = json.loads(
                    data.get("coverage_matrix_json") or "[]"
                )
            except (TypeError, ValueError):
                architecture["coverage_matrix"] = []
            architecture["status"] = data.get("status")
            architecture["overlap_count"] = int(data.get("overlap_count") or 0)
            return architecture

    @classmethod
    def for_chapter(cls, project_id: int, chapter_id: int) -> Optional[Dict[str, Any]]:
        architecture = cls.get(project_id) or cls.build(project_id)
        for entry in architecture.get("chapters") or []:
            if int(entry.get("chapter_id") or 0) == int(chapter_id):
                return entry
        return None

    @classmethod
    def gate(cls, project_id: int) -> Dict[str, Any]:
        architecture = cls.get(project_id) or cls.build(project_id)
        overlaps = architecture.get("overlaps") or []
        return {
            "ok": not overlaps,
            "overlap_count": len(overlaps),
            "overlaps": overlaps,
            "status": architecture.get("status", "PLANNED"),
            "matrix": architecture.get("coverage_matrix") or [],
        }

    @staticmethod
    def prompt_block(entry: Optional[Dict[str, Any]]) -> str:
        if not entry:
            return "No book-level architecture is available for this chapter."
        must_not = entry.get("must_not_repeat") or []
        return (
            f"Unique chapter purpose: {entry.get('unique_purpose', '')}\n"
            f"Main question answered: {entry.get('main_question_answered', '')}\n"
            f"Target learning outcome: {entry.get('learning_outcome', '')}\n"
            f"Transformation stage: {entry.get('transformation_stage', '')}\n"
            f"Required topics: {', '.join(entry.get('required_topics') or []) or 'Not specified'}\n"
            f"Topics already explained elsewhere (do not re-explain): "
            f"{', '.join(must_not[:20]) or 'None yet'}\n"
            "If an earlier topic is genuinely needed, reference it in one sentence and "
            "move on to new material."
        )
