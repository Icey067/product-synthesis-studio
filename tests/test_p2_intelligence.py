"""P2 regressions: routing, retrieval, structured generation, batch, editorial.

All tests use the isolated temporary database from ``tests/conftest.py`` and a
deterministic fake local model, so no network or real Ollama model is required.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

import pytest

from database.db import get_connection, transaction
from models.schemas import ProjectCreate
from services.batch_service import BatchService
from services.chapter_contract import analyze_chapter, count_words
from services.chapter_planning_service import SECTION_BLUEPRINT, ChapterPlanningService
from services.editorial_service import EditorialService
from services.generation_service import GenerationService
from services.manuscript_service import ManuscriptService
from services.model_evaluation_service import ModelEvaluationService
from services.model_registry import ModelRegistry
from services.project_service import ProjectService
from services.research_service import ResearchService
from services.retrieval_service import RetrievalService


class FakeLocalAI:
    """Deterministic local-model stand-in for structured generation tests."""

    def __init__(self, source_id: int, fail_sections: Optional[List[str]] = None):
        self.source_id = int(source_id)
        self.fail_sections = set(fail_sections or [])
        self.calls = 0

    def _get_selected_model(self, task: Optional[str] = None) -> str:
        return "fake-local:1b"

    def generate(self, prompt: str, **kwargs: Any):
        self.calls += 1
        if "Return only valid JSON" in prompt:
            return True, json.dumps(
                {
                    "title": "Digital growth for micro-businesses",
                    "learning_objectives": ["one", "two", "three"],
                    "key_topics": ["maps", "reviews", "whatsapp"],
                    "practical_exercise": "List five current customers.",
                }
            )
        heading_match = re.search(r"Heading: ## (.+)", prompt)
        if heading_match:
            heading = heading_match.group(1).strip()
            if heading.lower() in self.fail_sections:
                return False, "simulated model failure"
            body = (
                f"This section explains {heading.lower()} for a local micro-business owner "
                "with concrete steps, a short script and a realistic example. "
                f"Evidence label: [Source-{self.source_id}] [Source-999999]. "
            )
            # More than the repetition threshold, but not identical paragraphs.
            body += (
                "Start with the customer conversation, record the exact words, then choose one "
                "small improvement that can be completed this week. Measure the result before "
                "adding another channel. "
            ) * 4
            return True, f"## {heading}\n\n{body}"
        return True, "## Introduction\n\nGeneric deterministic fallback text. [Source-%d]" % self.source_id

    def generate_structured(self, **kwargs: Any):
        return False, None, "structured generation unavailable in fake client"


def _make_project(name_prefix: str = "P2 Test") -> Dict[str, Any]:
    name = f"{name_prefix} {uuid.uuid4().hex[:8]}"
    project_id = ProjectService.create_project(
        ProjectCreate(
            name=name,
            country="India",
            language="English",
            target_customer="local micro-business owners",
            expertise_mode="NON_EXPERT",
            product_category="Business & Productivity",
            product_format="Ebook",
            required_chapter_count=3,
            target_chapter_words=800,
            min_chapter_words=300,
        )
    )

    source_text = (
        "A practical study of local micro-businesses in India found that consistent "
        "Google Business Profile updates, WhatsApp follow-up and simple customer "
        "reviews improve repeat visits. Owners should track calls, messages and "
        "repeat customers weekly. " * 30
    )
    ok, source_id, message = ResearchService.add_source(
        project_id,
        "Digital growth field notes",
        "REPORT",
        source_text,
        publisher="Local Business Research Collective",
    )
    assert ok, message

    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO product_charters (
                project_id, product_title, subtitle, product_category, target_audience,
                customer_problem, core_promise, desired_outcome, unique_mechanism,
                differentiation, product_description, deliverables_json, evidence_base,
                product_limitations_json, scope_exclusions_json, disclaimer_requirements_json,
                product_format
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "Practical Digital Growth for Local Micro-Businesses",
                "A step-by-step field guide",
                "Business & Productivity",
                "local micro-business owners in India",
                "They have little time and no reliable digital customer acquisition process.",
                "A simple weekly digital routine that brings repeat customers.",
                "A repeatable weekly operating routine.",
                "Seven-day digital routine",
                "Built for low-budget local businesses.",
                "A practical ebook with worksheets.",
                json.dumps(["ebook", "worksheets"]),
                "Field notes from local businesses.",
                json.dumps(["No guaranteed revenue outcomes."]),
                json.dumps(["Paid advertising strategy"]),
                json.dumps(["Results vary by business."]),
                "Ebook",
            ),
        )
        conn.execute(
            """
            INSERT INTO transformation_maps (
                project_id, before_json, after_json, milestones_json, product_format
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                project_id,
                json.dumps({"situation": "Inconsistent digital presence"}),
                json.dumps({"situation": "A repeatable weekly digital routine"}),
                json.dumps(["Set up profiles", "Create a weekly routine", "Measure results"]),
                "Ebook",
            ),
        )
        for number, title in enumerate(
            [
                "Set up a trustworthy local profile",
                "Create a weekly customer follow-up routine",
                "Measure repeat customers without complex tools",
            ],
            start=1,
        ):
            conn.execute(
                """
                INSERT INTO chapters (
                    project_id, chapter_number, title, purpose,
                    learning_objectives_json, key_topics_json, expected_outcome,
                    content, target_word_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?)
                """,
                (
                    project_id,
                    number,
                    title,
                    "Teach one practical digital growth skill.",
                    json.dumps(["Understand the task", "Apply it this week"]),
                    json.dumps(["Google Business Profile", "WhatsApp follow-up", "reviews"]),
                    "A completed weekly action.",
                    800,
                ),
            )

    return {"project_id": project_id, "source_id": int(source_id), "name": name}


def _chapters(project_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM chapters WHERE project_id = ? ORDER BY chapter_number",
                (project_id,),
            ).fetchall()
        ]


def _approve(chapter_id: int, project_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO quality_reports (
                project_id, chapter_id, status, score, deterministic_checks_json,
                issues_json, human_review_required
            ) VALUES (?, ?, 'PASS', 95, ?, '[]', 0)
            """,
            (project_id, chapter_id, json.dumps({"counts": {"passed": 7}})),
        )


# ----------------------------------------------------------------------
# 1. Model detection and selection
# ----------------------------------------------------------------------
def test_model_detection_and_task_selection(monkeypatch):
    installed = [
        {"name": "model-a:1b", "available": True, "size_label": "1 GB"},
        {"name": "model-b:7b", "available": True, "size_label": "4 GB"},
    ]
    monkeypatch.setattr(
        ModelRegistry, "detect_models", classmethod(lambda cls, refresh=False: installed)
    )
    monkeypatch.setattr(
        ModelRegistry, "installed_names", classmethod(lambda cls: ["model-a:1b", "model-b:7b"])
    )

    ModelRegistry.set_global_model("model-b:7b")
    ModelRegistry.set_task_model("outline_generation", "model-a:1b")
    assert ModelRegistry.resolve_model("outline_generation") == "model-a:1b"
    assert ModelRegistry.resolve_model("long_form_writing") == "model-b:7b"

    # A configured but uninstalled model is skipped, never sent to Ollama.
    ModelRegistry.set_task_model("long_form_writing", "missing:70b")
    assert ModelRegistry.resolve_model("long_form_writing") == "model-b:7b"

    routing = ModelRegistry.routing_table()
    assert {row["task"] for row in routing} >= {
        "market_research",
        "long_form_writing",
        "quality_review",
    }
    assert all(row["available"] for row in routing)

    # A configured global model that is installed is labelled "global default".
    ModelRegistry.set_task_model("long_form_writing", "")
    ModelRegistry.set_global_model("model-b:7b")
    row = next(r for r in ModelRegistry.routing_table() if r["task"] == "long_form_writing")
    assert row["resolved_model"] == "model-b:7b"
    assert row["source"] == "global default"
    assert row["global_model_available"] is True

    # An uninstalled global choice falls back to an installed model and is
    # reported as such instead of pretending the configured value was used.
    ModelRegistry.set_global_model("missing:70b")
    row = next(r for r in ModelRegistry.routing_table() if r["task"] == "long_form_writing")
    assert row["resolved_model"] in ("model-a:1b", "model-b:7b")
    assert row["source"] == "installed fallback"
    assert row["global_model_available"] is False


def test_no_installed_model_returns_none(monkeypatch):
    monkeypatch.setattr(
        ModelRegistry, "installed_names", classmethod(lambda cls: [])
    )
    assert ModelRegistry.resolve_model("long_form_writing") is None


# ----------------------------------------------------------------------
# 2. Retrieval, dedupe and traceability
# ----------------------------------------------------------------------
def test_retrieval_ranks_dedupes_and_stores_trace():
    ctx = _make_project("P2 Retrieval")
    project_id = ctx["project_id"]

    # Add a near-identical source to prove duplicate suppression works.
    ResearchService.add_source(
        project_id,
        "Digital growth field notes (copy)",
        "REPORT",
        "A practical study of local micro-businesses in India found that consistent "
        "Google Business Profile updates, WhatsApp follow-up and simple customer "
        "reviews improve repeat visits. Owners should track calls, messages and "
        "repeat customers weekly. " * 30,
        publisher="Local Business Research Collective",
    )

    evidence = RetrievalService.retrieve(
        project_id, "Google Business Profile reviews WhatsApp follow-up", limit=6
    )
    assert evidence["items"], "Expected at least one evidence excerpt"
    assert evidence["sources"], "Expected at least one selected source"
    assert evidence["duplicates_suppressed"] >= 1
    assert all(item["citation_label"].startswith("[Source-") for item in evidence["items"])

    context, built = RetrievalService.build_context(
        project_id, "Google Business Profile reviews", limit=4
    )
    assert "[Source-" in context
    assert "<RESEARCH_SOURCE>" in context

    chapter_id = _chapters(project_id)[0]["id"]
    count = RetrievalService.store_chapter_sources(project_id, chapter_id, built)
    assert count >= 1
    stored = RetrievalService.get_chapter_sources(chapter_id)
    assert stored and all(row["chapter_id"] == chapter_id for row in stored)
    assert RetrievalService.used_source_ids(chapter_id)


# ----------------------------------------------------------------------
# 3. Chapter planning and section budgets
# ----------------------------------------------------------------------
def test_chapter_plan_has_section_budgets_that_sum_to_target():
    ctx = _make_project("P2 Plan")
    chapter = _chapters(ctx["project_id"])[0]
    plan = ChapterPlanningService().plan_chapter(ctx["project_id"], chapter)

    assert len(plan["sections"]) == len(SECTION_BLUEPRINT)
    assert sum(s["word_budget"] for s in plan["sections"]) == plan["target_words"]
    assert plan["planned_words"] == plan["target_words"]
    assert plan["evidence"]["source_count"] >= 1

    persisted = ChapterPlanningService.get_plan(chapter["id"])
    assert persisted and persisted["title"] == chapter["title"]
    sections = ChapterPlanningService.get_sections(chapter["id"])
    assert [s["section_key"] for s in sections] == [s[0] for s in SECTION_BLUEPRINT]
    assert all(s["status"] == "PLANNED" for s in sections)


# ----------------------------------------------------------------------
# 4. Section generation preserves valid citations and strips invented ones
# ----------------------------------------------------------------------
def test_section_generation_preserves_valid_citations_only():
    ctx = _make_project("P2 Citations")
    project_id, source_id = ctx["project_id"], ctx["source_id"]
    chapter = _chapters(project_id)[0]
    ai = FakeLocalAI(source_id)
    planner = ChapterPlanningService(ollama_client=ai)
    planner.plan_chapter(project_id, chapter)

    result = planner.generate_section(project_id, chapter["id"], "introduction")
    assert result["ok"], result.get("error")
    assert f"[Source-{source_id}]" in result["content"]
    assert "[Source-999999]" not in result["content"]
    assert 999999 in result["invalid_citations_removed"]

    stored = ChapterPlanningService.get_sections(chapter["id"])[0]
    assert stored["status"] == "DRAFTED"
    assert stored["word_count"] == count_words(stored["content"])


# ----------------------------------------------------------------------
# 5. Continuation and consistency
# ----------------------------------------------------------------------
def test_section_continuation_is_resumable():
    ctx = _make_project("P2 Continue")
    project_id, source_id = ctx["project_id"], ctx["source_id"]
    chapter = _chapters(project_id)[0]
    planner = ChapterPlanningService(ollama_client=FakeLocalAI(source_id))
    planner.plan_chapter(project_id, chapter)

    first = planner.generate_next_section(project_id, chapter["id"])
    assert first["ok"] and not first["done"]
    second = planner.continue_chapter(project_id, chapter["id"])
    assert second["ok"] and not second["done"]
    progress = planner.section_progress(chapter["id"])
    assert progress["drafted"] == 2

    # Calling continuation again must not overwrite a drafted section.
    drafted_before = {
        s["section_key"]: s["content"]
        for s in planner.get_sections(chapter["id"])
        if s["status"] == "DRAFTED"
    }
    third = planner.generate_next_section(project_id, chapter["id"])
    after = {
        s["section_key"]: s["content"]
        for s in planner.get_sections(chapter["id"])
        if s["section_key"] in drafted_before
    }
    assert third["ok"]
    assert after == drafted_before


def test_structured_chapter_completes_and_flags_budget_shortfalls():
    ctx = _make_project("P2 Structured")
    project_id, source_id = ctx["project_id"], ctx["source_id"]
    chapter = _chapters(project_id)[0]
    ai = FakeLocalAI(source_id)
    planner = ChapterPlanningService(ollama_client=ai)

    result = planner.generate_chapter_structured(project_id, chapter)
    assert result["saved"], result.get("message")
    assert result["report"]["status"] == "COMPLETE"
    assert result["consistency"]["complete_sections"] is True
    assert result["evidence"]["source_count"] >= 1

    content = result["content"]
    for heading in (
        "## Introduction",
        "## Step-by-Step Implementation",
        "## Practical Example",
        "## Common Mistakes & Troubleshooting",
        "## Worksheet / Exercise",
        "## Chapter Summary & 24-Hour Action Step",
    ):
        assert heading in content

    with get_connection() as conn:
        row = conn.execute(
            "SELECT content, actual_word_count, structural_complete, evidence_complete "
            "FROM chapters WHERE id = ?",
            (chapter["id"],),
        ).fetchone()
    assert row["content"].strip()
    assert row["actual_word_count"] > 0
    assert row["structural_complete"] == 1
    assert row["evidence_complete"] == 1

    consistency = planner.check_consistency(chapter["id"])
    assert consistency["drafted_count"] == len(SECTION_BLUEPRINT)


# ----------------------------------------------------------------------
# 6. Batch generation: pause, resume and approved protection
# ----------------------------------------------------------------------
def test_batch_pause_and_resume():
    ctx = _make_project("P2 Batch")
    project_id, source_id = ctx["project_id"], ctx["source_id"]
    batch = BatchService(ollama_client=FakeLocalAI(source_id))
    session_id = batch.create_session(project_id, mode="ALL_REMAINING", total=3)

    def _pause_after_first(done, total, chapter, message):
        if done == 1:
            BatchService.set_status(session_id, "PAUSED")

    first = batch.run(
        project_id,
        session_id=session_id,
        structured=True,
        progress_cb=_pause_after_first,
    )
    assert first["stop_reason"] == "PAUSED"
    assert first["produced"] >= 1
    assert BatchService.get_session(session_id)["status"] == "PAUSED"

    resumed = batch.resume(project_id, session_id=session_id, structured=True)
    assert resumed["ok"]
    assert BatchService.get_session(session_id)["status"] in ("COMPLETED", "FAILED")
    progress = BatchService.progress(project_id, session_id)
    assert progress["written_chapters"] == 3
    assert progress["research_covered_chapters"] >= 2


def test_approved_chapter_protection():
    ctx = _make_project("P2 Approved")
    project_id = ctx["project_id"]
    chapter = _chapters(project_id)[0]
    GenerationService.save_chapter_content(
        chapter["id"], "## Introduction\n\nOriginal approved content. " * 20 + "\n"
    )
    _approve(chapter["id"], project_id)
    assert ManuscriptService.chapter_is_approved(chapter["id"])

    replaced = GenerationService.save_chapter_content(
        chapter["id"], "## Introduction\n\nDifferent content that should be refused. " * 20
    )
    assert replaced is False

    forced = GenerationService.save_chapter_content(
        chapter["id"],
        "## Introduction\n\nConfirmed replacement content. " * 20,
        allow_approved_overwrite=True,
    )
    assert forced is True

    # Batch generation skips an approved chapter unless force is explicit.
    batch = BatchService(ollama_client=FakeLocalAI(ctx["source_id"]))
    outcome = batch.run(project_id, structured=True, force=False, max_chapters=10)
    assert any(r.get("status") == "SKIPPED_APPROVED" for r in outcome["results"])


# ----------------------------------------------------------------------
# 7. Editorial rubric and human review
# ----------------------------------------------------------------------
def test_editorial_rubric_separates_deterministic_from_advisory():
    ctx = _make_project("P2 Editorial")
    project_id, source_id = ctx["project_id"], ctx["source_id"]
    chapter = _chapters(project_id)[0]
    content = (
        "## Introduction\n\nThis chapter explains a practical digital routine. "
        f"[Source-{source_id}]\n\n"
        "## Step-by-Step Implementation\n\n1. Update the profile.\n2. Ask for a review.\n\n"
        "## Practical Example\n\nAn illustrative shop owner follows the steps.\n\n"
        "## Common Mistakes & Troubleshooting\n\nAvoid inconsistent hours.\n\n"
        "## Worksheet / Exercise\n\n- [ ] List five customers\n\n"
        "## Chapter Summary & 24-Hour Action Step\n\nDo one update today.\n"
    ) + ("Specific actionable detail for the reader. " * 60)
    chapter["content"] = content
    chapter["learning_objectives"] = ["Apply a routine"]

    deterministic = {
        "score": 88,
        "checks": {"required_sections": True},
        "issues": [
            {"check": "unsupported_statistic", "severity": "HIGH", "message": "Number without source."}
        ],
        "structure": analyze_chapter(
            content,
            target=800,
            min_words=300,
            source_ids=[source_id],
            product_format="Ebook",
        ),
    }
    service = EditorialService(ollama_client=FakeLocalAI(source_id))
    result = service.assess(chapter, {"product_title": "Test", "target_audience": "owners"}, deterministic, include_model=False)

    assert set(result["dimensions"]) == {
        "accuracy",
        "practical_usefulness",
        "depth",
        "clarity",
        "originality_risk",
        "relevance",
        "logical_flow",
        "reader_engagement",
        "evidence_support",
        "chapter_consistency",
    }
    assert all(entry["authoritative"] for entry in result["deterministic_rubric"].values())
    assert result["advisory_available"] is False
    assert result["status"] == "NEEDS_REVISION"
    assert any(r["source"] == "DETERMINISTIC" for r in result["recommendations"])

    saved = EditorialService.save_recommendations(
        project_id, chapter["id"], None, result["recommendations"]
    )
    assert saved >= 1
    pending = EditorialService.list_recommendations(project_id, chapter["id"], status="PENDING")
    assert pending
    assert EditorialService.set_recommendation_status(pending[0]["id"], "APPROVED")
    assert EditorialService.list_recommendations(project_id, chapter["id"], status="APPROVED")


# ----------------------------------------------------------------------
# 8. Model evaluation uses a temporary project and stores advisory metrics
# ----------------------------------------------------------------------
def test_model_evaluation_records_metrics_and_human_rubric():
    ai = FakeLocalAI(source_id=1)
    service = ModelEvaluationService(ollama_client=ai)
    result = service.evaluate_model("fake-local:1b", target_words=200, timeout=30)

    assert result["evaluation_id"]
    assert result["scores"]["json_valid"] == 1.0
    assert 0.0 <= result["scores"]["citation_compliance"] <= 1.0
    assert result["scores"]["structure"] > 0
    assert result["rubric"]["human_rubric_pending"] is True
    assert "do not prove" in result["rubric"]["advisory_note"].lower()

    assert ModelEvaluationService.set_human_score(result["evaluation_id"], 4.0, "Clear and practical")
    evaluations = ModelEvaluationService.list_evaluations(project_id=result["project_id"])
    assert evaluations and evaluations[0]["human_score"] == 4.0


# ----------------------------------------------------------------------
# 9. Duplication covers the P2 tables and remaps foreign keys
# ----------------------------------------------------------------------
def _p2_count(project_id: int, table: str) -> int:
    with get_connection() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project_id,)
        ).fetchone()[0]


def test_duplication_copies_p2_tables_and_remaps_ids():
    ctx = _make_project("P2 Duplication")
    project_id, source_id = ctx["project_id"], ctx["source_id"]
    chapter = _chapters(project_id)[0]

    planner = ChapterPlanningService(ollama_client=FakeLocalAI(source_id))
    planner.plan_chapter(project_id, chapter)
    evidence = RetrievalService.retrieve(project_id, "reviews WhatsApp", limit=3)
    assert RetrievalService.store_chapter_sources(project_id, chapter["id"], evidence) >= 1

    session_id = BatchService.create_session(project_id, mode="ALL_REMAINING", total=3)

    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO quality_reports (project_id, chapter_id, status, score)
            VALUES (?, ?, 'PASS', 90)
            """,
            (project_id, chapter["id"]),
        )
        report_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO editorial_recommendations (
                project_id, chapter_id, report_id, code, category, severity, message, source
            ) VALUES (?, ?, ?, 'unsupported_statistic', 'deterministic', 'HIGH', ?, 'DETERMINISTIC')
            """,
            (project_id, chapter["id"], report_id, "Add a source for the number."),
        )
        conn.execute(
            """
            INSERT INTO model_evaluations (project_id, model, task, status, json_valid)
            VALUES (?, 'fake-local:1b', 'long_form_writing', 'COMPLETED', 1)
            """,
            (project_id,),
        )
        conn.execute(
            "UPDATE batch_sessions SET processed_chapters = 1 WHERE id = ?",
            (session_id,),
        )

    duplicate_id = ProjectService.duplicate_project(project_id)
    assert duplicate_id

    for table in (
        "chapter_plans",
        "chapter_sections",
        "chapter_sources",
        "batch_sessions",
        "model_evaluations",
        "editorial_recommendations",
    ):
        assert _p2_count(duplicate_id, table) == _p2_count(project_id, table), (
            f"{table} was not duplicated"
        )

    with get_connection() as conn:
        duplicate_chapter = conn.execute(
            "SELECT id FROM chapters WHERE project_id = ?", (duplicate_id,)
        ).fetchone()["id"]
        assert duplicate_chapter != chapter["id"]

        assert conn.execute(
            "SELECT COUNT(*) FROM chapter_plans WHERE chapter_id = ?", (duplicate_chapter,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM chapter_sections WHERE chapter_id = ?", (duplicate_chapter,)
        ).fetchone()[0] == len(SECTION_BLUEPRINT)

        duplicate_sources = conn.execute(
            "SELECT source_id FROM chapter_sources WHERE chapter_id = ?", (duplicate_chapter,)
        ).fetchall()
        duplicate_source_ids = {row["source_id"] for row in duplicate_sources}
        assert duplicate_source_ids and source_id not in duplicate_source_ids
        assert all(
            conn.execute(
                "SELECT project_id FROM research_sources WHERE id = ?", (sid,)
            ).fetchone()["project_id"] == duplicate_id
            for sid in duplicate_source_ids
        )

        # Recommendations point at the duplicated report, not the original.
        duplicate_report = conn.execute(
            "SELECT id FROM quality_reports WHERE project_id = ?", (duplicate_id,)
        ).fetchone()["id"]
        duplicate_rec = conn.execute(
            "SELECT * FROM editorial_recommendations WHERE project_id = ?", (duplicate_id,)
        ).fetchone()
        assert duplicate_rec["report_id"] == duplicate_report
        assert duplicate_rec["chapter_id"] == duplicate_chapter

        # A duplicated batch session must not appear to be actively running.
        duplicate_session = conn.execute(
            "SELECT status FROM batch_sessions WHERE project_id = ?", (duplicate_id,)
        ).fetchone()
        assert duplicate_session["status"] == "PAUSED"


# ----------------------------------------------------------------------
# 10. Benchmark project metadata and honest deterministic reporting
# ----------------------------------------------------------------------
def test_benchmark_project_is_real_and_reports_gaps():
    from services.benchmark_project import (
        BENCHMARK_NAME,
        deterministic_report,
        ensure_benchmark_project,
    )

    project_id = ensure_benchmark_project()
    assert ensure_benchmark_project() == project_id  # idempotent

    project = ProjectService.get_project(project_id)
    assert project["name"] == BENCHMARK_NAME
    assert project["country"] == "India"
    assert project["language"] == "English"
    assert project["expertise_mode"] == "NON_EXPERT"
    assert project["product_format"] == "Ebook"

    report = deterministic_report(project_id)
    assert report["chapter_count"] == 0  # no outline has been generated yet
    assert report["research_sources"] == 0
    assert report["commercial_readiness_claim"] is False
    assert "No research sources are seeded" in report["evidence_note"]
    assert "deterministic" in report["limitations"].lower()
    assert report["exports"] == []
