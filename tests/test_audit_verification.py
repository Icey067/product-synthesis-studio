"""FIX 4 regression tests — independent quality auditor."""
from unittest.mock import MagicMock

from database.db import transaction
from models.enums import AuditStatus, ProductFormat
from models.schemas import ProjectCreate, QualityAuditReport
from services.audit_service import AuditService
from services.ollama_client import OllamaClient
from services.project_service import ProjectService
from services.research_service import ResearchService
from services.verification_service import VerificationService

SOURCE_TEXT = (
    "A survey of independent dental clinics measured appointment reminders and scheduling behavior. "
    "The report describes front-desk workflows and patient confirmation calls in detail."
)

PASS_REPORT = QualityAuditReport(
    status=AuditStatus.PASS,
    score=99,
    factual_accuracy_assessment="Looks completely accurate to me.",
    unsupported_statements=[],
    repetition_issues=[],
    readability_score="Excellent",
    practical_value_assessment="Very useful.",
    recommended_corrections=[],
    disclaimer_flags=[],
    summary_verdict="Perfect chapter.",
)


def _project_chapter(content, name="Audit Verification Test"):
    p_id = ProjectService.create_project(ProjectCreate(name=name, product_format=ProductFormat.EBOOK))
    with transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO chapters (project_id, chapter_number, title, purpose, content, word_count)
            VALUES (?, 1, 'Fabrication Chapter', 'Test', ?, ?)
            """,
            (p_id, content, len(content.split())),
        )
        ch_id = cur.lastrowid
    return p_id, ch_id


def test_fabricated_statistic_without_citation_is_flagged():
    p_id, ch_id = _project_chapter(
        "## Introduction\n"
        "In 2024, 87% of independent dental clinics lost revenue because patients simply forgot appointments. "
        "This paragraph is long enough to be treated as a real claim by the verifier."
    )
    chapter = {"id": ch_id, "chapter_number": 1, "title": "Fabrication Chapter",
               "content": "In 2024, 87% of independent dental clinics lost revenue because patients forgot appointments."}
    result = VerificationService.deterministic_checks(
        p_id, chapter, {}, [], product_format="Ebook"
    )
    flagged = [i for i in result["issues"] if i["check"] == "unsupported_statistic"]
    assert flagged, result["issues"]
    assert "87%" in flagged[0]["message"]
    status, score, human = VerificationService.final_status(result)
    assert status != "PASS"
    assert score < 100

    ProjectService.delete_project(p_id)


def test_statistic_absent_from_cited_source_is_flagged():
    p_id, ch_id = _project_chapter(
        "## Introduction\n"
        "Research shows that 87% of clinics lost revenue last year according to the operations survey. [Source-1]"
    )
    ok, sid, msg = ResearchService.add_source(
        project_id=p_id,
        title="Dental Operations Survey",
        source_category="REPORT",
        content_text=SOURCE_TEXT,
        reliability_assessment="HIGH",
    )
    assert ok, msg
    sources = ResearchService.get_sources(p_id)
    claim_text = f"Research shows that 87% of clinics lost revenue last year. [Source-{sid}]"
    chapter = {
        "id": ch_id, "chapter_number": 1, "title": "Fabrication Chapter",
        "content": claim_text,
    }
    result = VerificationService.deterministic_checks(
        p_id, chapter, {}, sources, product_format="Ebook"
    )
    assert any(i["check"] == "statistic_not_in_source" for i in result["issues"]), result["issues"]

    ProjectService.delete_project(p_id)


def test_nonexistent_citation_requires_human_verification():
    p_id, ch_id = _project_chapter(
        "## Introduction\nThis claim cites a source that was never imported. [Source-424242]"
    )
    chapter = {
        "id": ch_id, "chapter_number": 1, "title": "Fabrication Chapter",
        "content": "This claim cites a source that was never imported. [Source-424242]",
    }
    result = VerificationService.deterministic_checks(p_id, chapter, {}, [], product_format="Ebook")
    status, score, human = VerificationService.final_status(result)
    assert status == "REQUIRES_HUMAN_VERIFICATION"
    assert human is True
    assert score <= 60

    ProjectService.delete_project(p_id)


def test_model_pass_cannot_override_fabricated_statistic():
    """Acceptance test: fabricated statistic flagged even when the model says PASS."""
    content = (
        "## Introduction\n"
        "In 2024, 87% of independent dental clinics lost revenue because patients forgot appointments. "
        "This paragraph exists so the chapter is long enough for an audit to run at all. "
        "It repeats the context once more so the verifier has something to inspect carefully."
    )
    p_id, ch_id = _project_chapter(content)
    ResearchService.add_source(
        project_id=p_id,
        title="Dental Operations Survey",
        source_category="REPORT",
        content_text=SOURCE_TEXT,
        reliability_assessment="HIGH",
    )

    mock = MagicMock(spec=OllamaClient)
    mock.generate_structured.return_value = (True, PASS_REPORT, "ok")

    service = AuditService(mock)
    chapter = {
        "id": ch_id, "chapter_number": 1, "title": "Fabrication Chapter", "content": content,
    }
    ok, report, msg = service.audit_chapter(p_id, chapter, {"product_title": "Playbook"})
    assert ok, msg
    assert report["status"] != "PASS"
    assert report["score"] < 99
    assert any("87%" in u for u in report["unsupported_statements"]), report["unsupported_statements"]
    assert report["deterministic_checks"]["statistics_supported"] is False

    ProjectService.delete_project(p_id)
