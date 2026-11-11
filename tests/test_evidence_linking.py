"""FIX 1 regression tests — evidence-linked market intelligence."""
from unittest.mock import MagicMock

from database.db import get_connection, transaction
from models.enums import ProductFormat
from models.schemas import OpportunityItem, OpportunityResponse, ProjectCreate
from services.evidence_service import verify_claims
from services.market_service import MarketService
from services.ollama_client import OllamaClient
from services.project_service import ProjectService
from services.research_service import ResearchService
from services.scoring_service import ScoringService


def _project(name="Evidence Linking Test"):
    return ProjectService.create_project(ProjectCreate(name=name, product_format=ProductFormat.EBOOK))


def _source(project_id, title, text, reliability="HIGH"):
    ok, sid, msg = ResearchService.add_source(
        project_id=project_id,
        title=title,
        source_category="REPORT",
        content_text=text,
        publisher="Test Publisher",
        reliability_assessment=reliability,
    )
    assert ok, msg
    return sid


GARDENING = (
    "Tomato plants require consistent watering and six hours of direct sunlight. "
    "Gardeners who rotate crops each season reduce soil-borne disease significantly. "
    "Compost should be turned every two weeks for best results."
)
KNITTING = (
    "Knitting patterns use cable stitches to create textured fabric. "
    "Wool yarn should be blocked after finishing to hold its final shape. "
    "Beginners should practice garter stitch before attempting cables."
)
COOKING = (
    "Sourdough starter needs feeding with flour and water every twelve hours. "
    "A dutch oven retains steam and produces a crisp crust. "
    "Bulk fermentation usually takes four to six hours at room temperature."
)
DENTAL = (
    "An analysis of 350 independent dental clinics found that the average no-show rate "
    "is 14.8%, costing a two-chair practice about 48000 dollars annually in lost chair time. "
    "Clinics using two-way SMS appointment reminders reduced no-shows by 68%."
)

OPP_BASE = {
    "name": "Reduce dental clinic no-shows",
    "exact_customer": "Independent dental clinic owners",
    "customer_problem": "Patients miss appointments, wasting chair time and revenue.",
    "problem_urgency": "High",
    "desired_outcome": "Lower no-show rate using reminders.",
    "potential_solution": "A reminder playbook for dental clinics.",
}


def test_unrelated_sources_never_produce_high_confidence():
    """Acceptance test: 3 unrelated sources must not create HIGH confidence."""
    p_id = _project("Unrelated Source Test")
    _source(p_id, "Gardening Basics", GARDENING)
    _source(p_id, "Knitting Basics", KNITTING)
    _source(p_id, "Sourdough Basics", COOKING)

    opp = OpportunityItem(**OPP_BASE)  # no evidence links
    mock = MagicMock(spec=OllamaClient)
    mock.generate_structured.return_value = (True, OpportunityResponse(opportunities=[opp]), "ok")

    service = MarketService(mock)
    project = ProjectService.get_project(p_id)
    ok, items, msg = service.discover_opportunities(project)
    assert ok, msg
    assert items
    assert all(i["confidence"] == "HYPOTHESIS" for i in items)

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT evidence_status FROM opportunities WHERE project_id = ?", (p_id,)
        ).fetchall()
    assert rows and all(r["evidence_status"] == "HYPOTHESIS" for r in rows)

    ProjectService.delete_project(p_id)


def test_nonexistent_source_id_is_rejected():
    p_id = _project("Nonexistent Source Test")
    _source(p_id, "Gardening Basics", GARDENING)
    sources = ResearchService.get_sources(p_id)

    opp = {
        **OPP_BASE,
        "evidence": [{
            "field": "demand_evidence",
            "source_id": 99999,
            "excerpt": GARDENING,
            "claim": "Dental clinics lose revenue.",
        }],
    }
    result = verify_claims(opp, sources)
    assert result["accepted_count"] == 0
    assert result["rejected_count"] == 1
    assert "does not exist" in result["rejected"][0]["notes"]

    ProjectService.delete_project(p_id)


def test_unrelated_source_excerpt_is_rejected():
    """A verbatim quote from an unrelated source is not valid evidence."""
    p_id = _project("Unrelated Excerpt Test")
    sid = _source(p_id, "Gardening Basics", GARDENING)
    sources = ResearchService.get_sources(p_id)

    opp = {
        **OPP_BASE,
        "evidence": [{
            "field": "demand_evidence",
            "source_id": sid,
            "excerpt": "Tomato plants require consistent watering and six hours of direct sunlight.",
            "claim": "Dental clinics lose revenue from missed appointments.",
        }],
    }
    result = verify_claims(opp, sources)
    assert result["accepted_count"] == 0
    assert result["rejected_count"] == 1
    assert "not relevant" in result["rejected"][0]["notes"].lower()

    ProjectService.delete_project(p_id)


def test_excerpt_not_present_in_source_is_rejected():
    p_id = _project("Fabricated Excerpt Test")
    sid = _source(p_id, "Dental Operations Report", DENTAL)
    sources = ResearchService.get_sources(p_id)

    opp = {
        **OPP_BASE,
        "evidence": [{
            "field": "demand_evidence",
            "source_id": sid,
            "excerpt": "Dental clinics report a 95% no-show rate across every region.",
            "claim": "Dental clinics have extremely high no-show rates.",
        }],
    }
    result = verify_claims(opp, sources)
    assert result["accepted_count"] == 0
    assert "not found" in result["rejected"][0]["notes"].lower()

    ProjectService.delete_project(p_id)


def test_relevant_source_links_are_accepted_and_persisted():
    p_id = _project("Relevant Evidence Test")
    sid = _source(p_id, "Dental Operations Report", DENTAL)

    opp = OpportunityItem(
        **OPP_BASE,
        evidence=[{
            "field": "demand_evidence",
            "source_id": sid,
            "excerpt": "An analysis of 350 independent dental clinics found that the average no-show rate is 14.8%",
            "claim": "Dental clinics experience measurable no-show losses.",
        }],
    )
    mock = MagicMock(spec=OllamaClient)
    mock.generate_structured.return_value = (True, OpportunityResponse(opportunities=[opp]), "ok")

    service = MarketService(mock)
    project = ProjectService.get_project(p_id)
    ok, items, msg = service.discover_opportunities(project)
    assert ok, msg
    assert items

    sources = ResearchService.get_sources(p_id)
    verdict = verify_claims(opp.model_dump(), sources)
    assert verdict["accepted_count"] == 1
    assert verdict["accepted"][0]["source_id"] == sid

    with get_connection() as conn:
        ev = conn.execute(
            "SELECT * FROM opportunity_evidence WHERE verified = 1 AND source_id = ?", (sid,)
        ).fetchall()
        opp_row = conn.execute(
            "SELECT citation_ids_json FROM opportunities WHERE id = ?", (items[0]["id"],)
        ).fetchone()
    assert ev, "Verified evidence row was not persisted"
    assert str(sid) in opp_row["citation_ids_json"]

    ProjectService.delete_project(p_id)


def test_confidence_ignores_imported_source_count():
    """Direct regression: 3 sources imported, 0 verified -> no HIGH/MEDIUM."""
    scores = {k: 9.0 for k in [
        "demand_score", "willingness_to_pay_score", "urgency_score",
        "competitor_gap_score", "customer_specificity_score",
        "practical_solvability_score", "feasibility_score",
    ]}
    result = ScoringService.calculate_score(scores, {}, verified_sources_count=3)
    assert result.confidence_level == "HYPOTHESIS"
