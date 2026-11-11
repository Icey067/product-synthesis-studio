import pytest
from unittest.mock import MagicMock
from database.db import init_db
from services.ollama_client import OllamaClient
from services.market_service import MarketService
from services.charter_service import CharterService
from services.audit_service import AuditService
from services.project_service import ProjectService
from services.outline_service import OutlineService
from models.schemas import OpportunityResponse, OpportunityItem, ProductCharter, QualityAuditReport, ProjectCreate
from models.enums import AuditStatus

@pytest.fixture(autouse=True)
def setup_db():
    init_db()

def test_mock_structured_generation_and_recovery():
    mock_client = MagicMock(spec=OllamaClient)

    # Create real project in DB
    p_id = ProjectService.create_project(ProjectCreate(name="Structured Gen Test Project"))

    opp_item = OpportunityItem(
        name="Mock Opportunity",
        exact_customer="Retailers",
        customer_problem="Stagnant footfall",
        problem_urgency="High",
        desired_outcome="20% repeat growth",
        potential_solution="Playbook",
    )
    mock_opp_resp = OpportunityResponse(opportunities=[opp_item])
    mock_client.generate_structured.return_value = (True, mock_opp_resp, "Success")

    market_service = MarketService(mock_client)
    project = ProjectService.get_project(p_id)
    success, items, msg = market_service.discover_opportunities(project)
    assert success is True
    assert len(items) == 1
    assert items[0]["name"] == "Mock Opportunity"

    ProjectService.delete_project(p_id)

def test_mock_quality_audit_cannot_self_certify():
    """The model returning PASS/92 must not override deterministic failures."""
    mock_client = MagicMock(spec=OllamaClient)
    # Create valid project and chapter in DB
    p_id = ProjectService.create_project(ProjectCreate(name="Audit Test Project"))
    from database.db import transaction
    with transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO chapters (project_id, chapter_number, title, purpose, content, word_count)
            VALUES (?, 1, 'Audited Chapter', 'Test Purpose', ?, 50)
            """,
            (p_id, "This is a comprehensive chapter with substantial length and actionable points to audit." * 10)
        )
        ch_id = cur.lastrowid

    audit_report = QualityAuditReport(
        status=AuditStatus.PASS,
        score=92,
        factual_accuracy_assessment="Well grounded in cited evidence.",
        unsupported_statements=[],
        repetition_issues=[],
        readability_score="Clear (Grade 8)",
        practical_value_assessment="High immediate utility.",
        recommended_corrections=[],
        disclaimer_flags=[],
        summary_verdict="Excellent ready-to-publish draft.",
    )
    mock_client.generate_structured.return_value = (True, audit_report, "Success")

    audit_service = AuditService(mock_client)
    chapter = {
        "id": ch_id,
        "chapter_number": 1,
        "title": "Audited Chapter",
        "content": "This is a comprehensive chapter with substantial length and actionable points to audit." * 10,
    }
    charter = {"product_title": "Test Title", "core_promise": "Promise", "target_audience": "Audience"}

    ok, rep, msg = audit_service.audit_chapter(p_id, chapter, charter)
    assert ok is True
    # Deterministic checks are authoritative: short, structureless, repeated text
    # cannot pass even when the model says PASS.
    assert rep["status"] == "NEEDS_IMPROVEMENT"
    assert rep["score"] <= 75
    assert rep["deterministic_checks"]["word_target"] is False
    assert rep["deterministic_checks"]["required_sections"] is False
    assert "Model-assisted impression" in rep["factual_accuracy_assessment"]

    ProjectService.delete_project(p_id)
