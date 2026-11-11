"""Structured, resumable chapter generation.

A chapter is planned first, then written section by section, assembled, checked
for repetition/continuity, validated by the deterministic contract, and saved as
an editable revision.  Every section is persisted immediately so an interrupted
run resumes instead of restarting.

The model receives bounded research evidence retrieved by
:mod:`services.retrieval_service` and is only allowed to cite the exact
``[Source-<id>]`` labels it was given.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from config.prompts_p2 import (
    PROMPT_SECTION_GENERATION,
    PROMPT_TRANSITION_CHECK,
    SECTION_SYSTEM,
)
from database.db import get_connection, transaction
from services.chapter_contract import (
    CITATION_RE,
    analyze_chapter,
    count_words,
    min_publication_words,
    target_words,
)
from services.ollama_client import OllamaClient
from services.product_format_service import contract_text
from services.research_service import ResearchService
from services.retrieval_service import RetrievalService
from utils.security import wrap_untrusted_content

# key, heading, purpose, share of the chapter word budget
SECTION_BLUEPRINT: List[Tuple[str, str, str, float]] = [
    (
        "introduction",
        "Introduction",
        "State the concrete problem, who it affects, what the reader will be able to do, and why the chapter matters.",
        0.10,
    ),
    (
        "explanation",
        "Understanding the Core Problem",
        "Explain the mechanism behind the problem in plain language, including the evidence and the limits of that evidence.",
        0.13,
    ),
    (
        "framework",
        "Practical Framework",
        "Present a named, reusable decision framework or operating model the reader can apply immediately.",
        0.12,
    ),
    (
        "step_by_step",
        "Step-by-Step Implementation",
        "Give ordered implementation steps with owners, inputs, outputs and realistic time estimates.",
        0.20,
    ),
    (
        "practical_example",
        "Practical Example",
        "Walk through a realistic, clearly illustrative scenario. Label it as illustrative; never invent a study or customer.",
        0.13,
    ),
    (
        "common_mistakes",
        "Common Mistakes & Troubleshooting",
        "List failure modes, why they happen, how to detect them and what to do instead.",
        0.10,
    ),
    (
        "worksheet",
        "Worksheet / Exercise",
        "Provide a fill-in worksheet, checklist or exercise the reader completes before moving on.",
        0.11,
    ),
    (
        "summary",
        "Chapter Summary & 24-Hour Action Step",
        "Summarise the actionable takeaways and give one concrete 24-hour action plus the next step.",
        0.11,
    ),
]

MIN_SECTION_WORDS = 80


class ChapterPlanningService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------
    @staticmethod
    def _project(project_id: int) -> Dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return dict(row) if row else {}

    @staticmethod
    def _charter(project_id: int) -> Dict[str, Any]:
        from services.charter_service import CharterService

        return CharterService.get_charter(project_id) or {}

    @staticmethod
    def _transformation(project_id: int) -> Dict[str, Any]:
        from services.transformation_service import TransformationService

        return TransformationService.get_map(project_id) or {}

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str) and value:
            try:
                parsed = json.loads(value)
                return [str(v) for v in parsed] if isinstance(parsed, list) else [value]
            except Exception:
                return [value]
        return []

    @classmethod
    def _chapter_query(cls, chapter: Dict[str, Any]) -> str:
        return " ".join(
            [
                str(chapter.get("title", "")),
                str(chapter.get("purpose", "")),
                " ".join(cls._as_list(chapter.get("key_topics"))),
                " ".join(cls._as_list(chapter.get("learning_objectives"))),
                str(chapter.get("expected_outcome", "")),
            ]
        )

    @staticmethod
    def _chapter_row(chapter_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------
    @classmethod
    def _section_budgets(cls, target: int) -> List[int]:
        target = max(len(SECTION_BLUEPRINT), int(target or 0))
        # The minimum has to scale down for short chapters; otherwise eight
        # sections would always exceed the target and "planned words" would lie.
        min_section = max(30, min(MIN_SECTION_WORDS, target // (len(SECTION_BLUEPRINT) + 2)))
        budgets = [
            max(min_section, int(round(target * share)))
            for _, _, _, share in SECTION_BLUEPRINT
        ]
        # Make the budgets add up to the chapter target without starving a
        # section: distribute the remainder onto the largest sections first.
        difference = target - sum(budgets)
        order = sorted(range(len(budgets)), key=lambda i: (-budgets[i], i))
        index = 0
        while difference != 0 and order:
            idx = order[index % len(order)]
            if difference > 0:
                budgets[idx] += 1
                difference -= 1
            elif budgets[idx] > min_section:
                budgets[idx] -= 1
                difference += 1
            index += 1
            if index > 100000:
                break
        return budgets

    @classmethod
    def build_plan(
        cls,
        project_id: int,
        chapter: Dict[str, Any],
        sources: Optional[List[Dict[str, Any]]] = None,
        target: Optional[int] = None,
        min_words: Optional[int] = None,
    ) -> Dict[str, Any]:
        project = cls._project(project_id)
        charter = cls._charter(project_id)
        transformation = cls._transformation(project_id)
        sources = sources if sources is not None else ResearchService.get_sources(project_id)

        chapter_target = int(
            target
            or chapter.get("target_word_count")
            or project.get("target_chapter_words")
            or target_words(project_id)
        )
        chapter_min = int(
            min_words
            or project.get("min_chapter_words")
            or min_publication_words(project_id)
        )
        chapter_min = min(chapter_min, chapter_target)
        budgets = cls._section_budgets(chapter_target)

        evidence = RetrievalService.retrieve(
            project_id,
            cls._chapter_query(chapter),
            sources=sources,
            limit=8,
        )

        before = transformation.get("before_situation") or {}
        after = transformation.get("after_situation") or {}
        sections = [
            {
                "sequence": index + 1,
                "section_key": key,
                "heading": heading,
                "purpose": purpose,
                "word_budget": budgets[index],
            }
            for index, (key, heading, purpose, _share) in enumerate(SECTION_BLUEPRINT)
        ]

        return {
            "chapter_id": chapter.get("id"),
            "chapter_number": int(chapter.get("chapter_number") or 0),
            "title": chapter.get("title", ""),
            "purpose": chapter.get("purpose", ""),
            "learning_objectives": cls._as_list(chapter.get("learning_objectives")),
            "key_topics": cls._as_list(chapter.get("key_topics")),
            "expected_outcome": chapter.get("expected_outcome", ""),
            "product_format": chapter.get("product_format") or project.get("product_format") or "Ebook",
            "country": project.get("country", "India"),
            "language": project.get("language", "English"),
            "expertise_mode": project.get("expertise_mode", "NON_EXPERT"),
            "target_words": chapter_target,
            "min_words": chapter_min,
            "planned_words": sum(budgets),
            "sections": sections,
            "product_context": {
                "product_title": charter.get("product_title") or project.get("name", ""),
                "product_promise": charter.get("core_promise", ""),
                "target_audience": charter.get("target_audience")
                or project.get("target_customer", ""),
                "customer_problem": charter.get("customer_problem", ""),
                "desired_outcome": charter.get("desired_outcome", ""),
                "before_state": before.get("current_situation") or before.get("situation") or before.get("description") or "",
                "after_state": after.get("desired_situation") or after.get("situation") or after.get("description") or "",
                "milestones": transformation.get("milestones") or [],
            },
            "evidence": {
                "source_ids": [s["id"] for s in evidence.get("sources", [])],
                "source_count": len(evidence.get("sources", [])),
                "excerpt_count": len(evidence.get("items", [])),
                "contradictions": evidence.get("contradictions", []),
                "labels": [f"[Source-{s['id']}]" for s in evidence.get("sources", [])],
            },
            "evidence_query": cls._chapter_query(chapter),
        }

    @classmethod
    def plan_chapter(
        cls,
        project_id: int,
        chapter: Dict[str, Any],
        sources: Optional[List[Dict[str, Any]]] = None,
        target: Optional[int] = None,
        min_words: Optional[int] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """Create (or reuse) a persisted chapter plan and its section budgets."""
        chapter_id = int(chapter["id"])
        if not refresh:
            existing = cls.get_plan(chapter_id)
            if existing:
                existing["sections"] = cls.get_sections(chapter_id)
                return existing

        plan = cls.build_plan(project_id, chapter, sources=sources, target=target, min_words=min_words)
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO chapter_plans (
                    project_id, chapter_id, plan_json, planned_words, target_words, min_words, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'PLANNED')
                ON CONFLICT(chapter_id) DO UPDATE SET
                    plan_json = excluded.plan_json,
                    planned_words = excluded.planned_words,
                    target_words = excluded.target_words,
                    min_words = excluded.min_words,
                    status = 'PLANNED',
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project_id,
                    chapter_id,
                    json.dumps(plan),
                    plan["planned_words"],
                    plan["target_words"],
                    plan["min_words"],
                ),
            )
            conn.execute("DELETE FROM chapter_sections WHERE chapter_id = ?", (chapter_id,))
            for section in plan["sections"]:
                conn.execute(
                    """
                    INSERT INTO chapter_sections (
                        project_id, chapter_id, section_key, heading, sequence,
                        plan_notes, word_budget, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PLANNED')
                    """,
                    (
                        project_id,
                        chapter_id,
                        section["section_key"],
                        section["heading"],
                        section["sequence"],
                        section["purpose"],
                        section["word_budget"],
                    ),
                )
        plan["sections"] = cls.get_sections(chapter_id)
        return plan

    @staticmethod
    def get_plan(chapter_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM chapter_plans WHERE chapter_id = ?", (chapter_id,)
            ).fetchone()
            if not row:
                return None
            record = dict(row)
            try:
                plan = json.loads(record.get("plan_json") or "{}")
            except (TypeError, ValueError):
                plan = {}
            plan.setdefault("chapter_id", record["chapter_id"])
            plan.setdefault("project_id", record["project_id"])
            plan.setdefault("target_words", record.get("target_words") or 0)
            plan.setdefault("min_words", record.get("min_words") or 0)
            plan.setdefault("planned_words", record.get("planned_words") or 0)
            plan["plan_status"] = record.get("status")
            return plan

    @staticmethod
    def get_sections(chapter_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chapter_sections WHERE chapter_id = ? ORDER BY sequence ASC",
                (chapter_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Section generation
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_invalid_citations(text: str, valid_ids: set[int]) -> Tuple[str, List[int]]:
        removed: List[int] = []

        def replace(match: re.Match) -> str:
            source_id = int(match.group(1))
            if source_id in valid_ids:
                return match.group(0)
            removed.append(source_id)
            return ""

        cleaned = CITATION_RE.sub(replace, text or "")
        return cleaned, sorted(set(removed))

    def _evidence_for_chapter(self, project_id: int, plan: Dict[str, Any]) -> Dict[str, Any]:
        sources = ResearchService.get_sources(project_id)
        _context, evidence = RetrievalService.build_context(
            project_id,
            plan.get("evidence_query") or plan.get("title", ""),
            sources=sources,
            limit=8,
        )
        return evidence

    def generate_section(
        self,
        project_id: int,
        chapter_id: int,
        section_key: str,
        force: bool = False,
        evidence: Optional[Dict[str, Any]] = None,
        previous_sections: Optional[List[str]] = None,
        allow_approved_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Generate and persist one section.  Drafted sections are reused."""
        from services.manuscript_service import ManuscriptService

        if (
            not allow_approved_overwrite
            and ManuscriptService.chapter_is_approved(chapter_id)
        ):
            return {
                "ok": False,
                "section_key": section_key,
                "error": (
                    "This chapter passed an independent audit. Confirm the "
                    "approved-content overwrite checkbox before regenerating sections."
                ),
            }

        plan = self.get_plan(chapter_id)
        if not plan:
            chapter = self._chapter_row(chapter_id)
            if not chapter:
                return {"ok": False, "error": "Chapter not found."}
            plan = self.plan_chapter(project_id, chapter)

        sections = self.get_sections(chapter_id)
        target_section = next((s for s in sections if s["section_key"] == section_key), None)
        if target_section is None:
            return {"ok": False, "error": f"Unknown section '{section_key}'."}
        if target_section["status"] == "DRAFTED" and not force and (target_section.get("content") or "").strip():
            return {
                "ok": True,
                "skipped": True,
                "section_key": section_key,
                "content": target_section["content"],
                "word_count": target_section["word_count"],
            }

        evidence = evidence or self._evidence_for_chapter(project_id, plan)
        valid_ids = {int(item["source_id"]) for item in evidence.get("items", [])}
        context = evidence.get("context")
        if not context:
            context, evidence = RetrievalService.build_context(
                project_id, plan.get("evidence_query") or plan.get("title", "")
            )
            valid_ids = {int(item["source_id"]) for item in evidence.get("items", [])}

        # Persist the evidence trace even when only one section is generated, so
        # the chapter always shows which research it drew on.
        if evidence.get("items"):
            RetrievalService.store_chapter_sources(project_id, chapter_id, evidence)

        if previous_sections is None:
            previous_sections = [
                f"## {s['heading']}\n{(s.get('content') or '')[:700]}"
                for s in sections
                if s["sequence"] < target_section["sequence"]
                and s["status"] == "DRAFTED"
                and (s.get("content") or "").strip()
            ]

        product = plan.get("product_context", {})
        budget = int(target_section.get("word_budget") or 0)
        prompt = PROMPT_SECTION_GENERATION.format(
            sequence=target_section["sequence"],
            section_total=len(sections),
            chapter_number=plan.get("chapter_number", 0),
            chapter_title=plan.get("title", ""),
            heading=target_section["heading"],
            section_purpose=target_section.get("plan_notes") or "",
            word_budget=budget,
            word_cap=int(budget * 1.35) if budget else 0,
            expertise_mode=plan.get("expertise_mode", "NON_EXPERT"),
            country=plan.get("country", "India"),
            product_format=plan.get("product_format", "Ebook"),
            product_title=product.get("product_title", ""),
            product_promise=product.get("product_promise", ""),
            target_audience=product.get("target_audience", ""),
            before_state=product.get("before_state", ""),
            after_state=product.get("after_state", ""),
            chapter_purpose=plan.get("purpose", ""),
            learning_objectives="; ".join(plan.get("learning_objectives", [])) or "Not specified",
            key_topics="; ".join(plan.get("key_topics", [])) or "Not specified",
            expected_outcome=plan.get("expected_outcome", ""),
            evidence_context=context,
            previous_sections=(
                wrap_untrusted_content("\n\n".join(previous_sections), label="PREVIOUS_SECTIONS")
                if previous_sections
                else "None yet."
            ),
            min_citations=1 if valid_ids else 0,
        )
        prompt += "\n\nFORMAT CONTRACT:\n" + contract_text(plan.get("product_format", "Ebook"))

        started = time.time()
        ok, response = self.ai.generate(
            prompt=prompt,
            system=SECTION_SYSTEM,
            temperature=0.6,
            timeout=300,
            task="long_form_writing",
        )
        elapsed_ms = round((time.time() - started) * 1000, 1)
        if not ok:
            with transaction() as conn:
                conn.execute(
                    "UPDATE chapter_sections SET status = 'FAILED', error_message = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (str(response)[:500], target_section["id"]),
                )
            return {"ok": False, "section_key": section_key, "error": response}

        content = (response or "").strip()
        content, removed = self._strip_invalid_citations(content, valid_ids)
        heading_line = f"## {target_section['heading']}"
        if not content.lstrip().lower().startswith("##"):
            content = heading_line + "\n\n" + content
        words = count_words(content)
        model_name = getattr(self.ai, "_get_selected_model", lambda task=None: None)("long_form_writing")

        with transaction() as conn:
            conn.execute(
                """
                UPDATE chapter_sections
                SET content = ?, word_count = ?, status = 'DRAFTED', model = ?,
                    generation_time_ms = ?, error_message = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (content, words, model_name, elapsed_ms, target_section["id"]),
            )

        return {
            "ok": True,
            "section_key": section_key,
            "heading": target_section["heading"],
            "content": content,
            "word_count": words,
            "budget": budget,
            "generation_time_ms": elapsed_ms,
            "model": model_name,
            "invalid_citations_removed": removed,
        }

    def generate_next_section(
        self,
        project_id: int,
        chapter_id: int,
        force: bool = False,
    ) -> Dict[str, Any]:
        sections = self.get_sections(chapter_id)
        pending = next(
            (s for s in sections if s["status"] in ("PLANNED", "FAILED")),
            None,
        )
        if pending is None:
            return {"ok": True, "done": True, "sections": sections}
        result = self.generate_section(project_id, chapter_id, pending["section_key"], force=force)
        result["done"] = False
        result["sections"] = self.get_sections(chapter_id)
        return result

    def continue_chapter(self, project_id: int, chapter_id: int) -> Dict[str, Any]:
        """Alias used by the UI: finish the next missing section only."""
        return self.generate_next_section(project_id, chapter_id)

    # ------------------------------------------------------------------
    # Assembly and checks
    # ------------------------------------------------------------------
    @staticmethod
    def assemble_chapter(chapter_id: int) -> str:
        parts: List[str] = []
        for section in ChapterPlanningService.get_sections(chapter_id):
            content = (section.get("content") or "").strip()
            if not content:
                continue
            heading = f"## {section['heading']}"
            if not content.lower().startswith("##"):
                content = heading + "\n\n" + content
            parts.append(content)
        return "\n\n".join(parts).strip()

    @staticmethod
    def check_consistency(chapter_id: int) -> Dict[str, Any]:
        """Deterministic repetition, heading and budget checks."""
        sections = ChapterPlanningService.get_sections(chapter_id)
        content = ChapterPlanningService.assemble_chapter(chapter_id)
        issues: List[Dict[str, Any]] = []

        seen_paragraphs: Dict[str, int] = {}
        for paragraph in re.split(r"\n{2,}", content):
            normalized = re.sub(r"\s+", " ", paragraph).strip().lower()
            if len(normalized.split()) < 25:
                continue
            if normalized in seen_paragraphs:
                issues.append(
                    {
                        "section": "chapter",
                        "problem": "repetition",
                        "detail": "A paragraph is repeated verbatim.",
                        "recommendation": "Remove the repeated paragraph or replace it with new detail.",
                    }
                )
            else:
                seen_paragraphs[normalized] = 1

        keys = [s["section_key"] for s in sections]
        expected = [key for key, _h, _p, _s in SECTION_BLUEPRINT]
        missing = [k for k in expected if k not in keys]
        if missing:
            issues.append(
                {
                    "section": "structure",
                    "problem": "missing_section",
                    "detail": f"Plan is missing: {', '.join(missing)}.",
                    "recommendation": "Rebuild the chapter plan.",
                }
            )

        for section in sections:
            if section["status"] != "DRAFTED" or not (section.get("content") or "").strip():
                continue
            budget = int(section.get("word_budget") or 0)
            words = int(section.get("word_count") or 0)
            if budget and words < int(budget * 0.45):
                issues.append(
                    {
                        "section": section["section_key"],
                        "problem": "under_budget",
                        "detail": f"{section['heading']} has {words} words against a {budget}-word budget.",
                        "recommendation": "Expand with concrete, non-repetitive detail or lower the budget.",
                    }
                )
            if words > int(budget * 1.6) and budget:
                issues.append(
                    {
                        "section": section["section_key"],
                        "problem": "over_budget",
                        "detail": f"{section['heading']} has {words} words against a {budget}-word budget.",
                        "recommendation": "Trim repetition or move detail to a more relevant section.",
                    }
                )

        drafted = [s for s in sections if s["status"] == "DRAFTED"]
        return {
            "issues": issues,
            "section_count": len(sections),
            "drafted_count": len(drafted),
            "word_count": count_words(content),
            "complete_sections": len(drafted) == len(sections) and bool(sections),
        }

    def model_transition_check(
        self,
        chapter_id: int,
        chapter_title: str,
        plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Advisory model review of transitions; never authoritative."""
        content = self.assemble_chapter(chapter_id)
        if not content:
            return {"advisory": True, "issues": [], "summary": "No content to review."}
        prompt = PROMPT_TRANSITION_CHECK.format(
            chapter_title=chapter_title,
            chapter_plan=json.dumps(plan.get("sections", []), indent=2),
            chapter_content=wrap_untrusted_content(content[:12000], label="CURRENT_CHAPTER"),
        )
        ok, response = self.ai.generate(
            prompt=prompt,
            temperature=0.2,
            format_json=True,
            timeout=180,
            task="quality_review",
        )
        if not ok:
            return {"advisory": True, "issues": [], "summary": f"Model check unavailable: {response}"}
        try:
            start, end = response.find("{"), response.rfind("}")
            data = json.loads(response[start : end + 1])
        except Exception:
            return {"advisory": True, "issues": [], "summary": "Model check returned unparsable output."}
        data["advisory"] = True
        return data

    # ------------------------------------------------------------------
    # Full structured generation
    # ------------------------------------------------------------------
    def generate_chapter_structured(
        self,
        project_id: int,
        chapter: Dict[str, Any],
        ai: Optional[OllamaClient] = None,
        max_section_failures: int = 2,
        allow_approved_overwrite: bool = False,
        check_transitions: bool = False,
    ) -> Dict[str, Any]:
        """Plan, write every section, assemble, validate and save."""
        if ai is not None:
            previous_ai = self.ai
            self.ai = ai
        else:
            previous_ai = None

        try:
            chapter_id = int(chapter["id"])
            plan = self.plan_chapter(project_id, chapter)
            evidence = self._evidence_for_chapter(project_id, plan)
            RetrievalService.store_chapter_sources(project_id, chapter_id, evidence)

            failures = 0
            generated: List[Dict[str, Any]] = []
            for section in self.get_sections(chapter_id):
                if section["status"] == "DRAFTED" and (section.get("content") or "").strip():
                    continue
                result = self.generate_section(
                    project_id,
                    chapter_id,
                    section["section_key"],
                    evidence=evidence,
                    allow_approved_overwrite=allow_approved_overwrite,
                )
                generated.append(result)
                if not result.get("ok"):
                    failures += 1
                    if failures >= max_section_failures:
                        break

            content = self.assemble_chapter(chapter_id)
            consistency = self.check_consistency(chapter_id)
            transition = (
                self.model_transition_check(chapter_id, plan.get("title", ""), plan)
                if check_transitions and content
                else None
            )
            report = analyze_chapter(
                content,
                target=plan.get("target_words"),
                min_words=plan.get("min_words"),
                source_ids=[int(item["source_id"]) for item in evidence.get("items", [])],
                product_format=plan.get("product_format", "Ebook"),
            )

            saved = False
            message = ""
            if content and consistency.get("complete_sections"):
                from services.generation_service import GenerationService

                saved = GenerationService.save_chapter_content(
                    chapter_id,
                    content,
                    note="Structured section-by-section generation",
                    validation=report,
                    allow_approved_overwrite=allow_approved_overwrite,
                )
                message = (
                    f"Structured chapter saved ({report['word_count']} words, "
                    f"{consistency['drafted_count']}/{consistency['section_count']} sections)."
                    if saved
                    else "Chapter was not saved (locked or independently approved)."
                )
            else:
                message = (
                    "Chapter assembly is incomplete; no chapter overwrite was performed. "
                    "Section drafts are saved and can be resumed."
                )

            with transaction() as conn:
                conn.execute(
                    """
                    UPDATE chapters
                    SET planned_word_count = ?, actual_word_count = ?,
                        structural_complete = ?, evidence_complete = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        int(plan.get("planned_words") or 0),
                        count_words(content),
                        1 if consistency.get("complete_sections") else 0,
                        1 if report.get("evidence_complete") else 0,
                        chapter_id,
                    ),
                )

            return {
                "ok": bool(saved),
                "saved": saved,
                "chapter_id": chapter_id,
                "content": content,
                "report": report,
                "consistency": consistency,
                "transition_check": transition,
                "evidence": {
                    "source_count": len(evidence.get("sources", [])),
                    "excerpt_count": len(evidence.get("items", [])),
                    "contradictions": evidence.get("contradictions", []),
                    "source_ids": [s["id"] for s in evidence.get("sources", [])],
                },
                "sections_generated": len(generated),
                "message": message,
            }
        finally:
            if previous_ai is not None:
                self.ai = previous_ai

    # ------------------------------------------------------------------
    # Controlled overwrite helper
    # ------------------------------------------------------------------
    @staticmethod
    def section_progress(chapter_id: int) -> Dict[str, Any]:
        sections = ChapterPlanningService.get_sections(chapter_id)
        drafted = [s for s in sections if s["status"] == "DRAFTED"]
        return {
            "total": len(sections),
            "drafted": len(drafted),
            "failed": len([s for s in sections if s["status"] == "FAILED"]),
            "planned_words": sum(int(s.get("word_budget") or 0) for s in sections),
            "actual_words": sum(int(s.get("word_count") or 0) for s in sections),
            "percent": round((len(drafted) / len(sections)) * 100, 1) if sections else 0.0,
        }
