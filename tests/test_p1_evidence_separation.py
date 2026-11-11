"""P1 regressions: model score vs evidence confidence, quote vs support."""
from unittest.mock import MagicMock

from database.db import get_connection
from models.enums import ProductFormat
from models.schemas import OpportunityItem, OpportunityResponse, ProjectCreate
from services.evidence_service import verify_claims, sanitize_fields
from services.market_service import MarketService
from services.ollama_client import OllamaClient
from services.project_service import ProjectService
from services.research_service import ResearchService
from services.scoring_service import ScoringService

SCORES = {
    "demand_score": 10.0,
    "willingness_to_pay_score": 10.0,
    "urgency_score": 10.0,
    "competitor_gap_score": 10.0,
    "customer_specificity_score": 10.0,
    "practical_solvability_score": 10.0,
    "feasibility_score": 10.0,
}

EVIDENCE = {
    "demand_evidence": "Measured 500 search queries monthly",
    "willingness_to_pay_evidence": "Observed $47 product sales",
    "existing_competitors": "Competitor A, B",
    "customer_complaints": "Too theoretical",
    "missing_features": "Checklists",
    "scoring_reasoning": "High demand with proven spend",
}

OPP_BASE = {
    "name": "Reduce dental clinic no-shows",
    "exact_customer": "Independent dental clinic owners",
    "customer_problem": "Patients miss appointments, wasting chair time and revenue.",
    "problem_urgency": "High",
    "desired_outcome": "Lower no-show rate using reminders.",
    "potential_solution": "A reminder playbook for dental clinics.",
}


def _project(name):
    return ProjectService.create_project(
        ProjectCreate(name=name, product_format=ProductFormat.EBOOK)
    )


def test_model_score_is_separate_from_evidence_confidence():
    result = ScoringService.calculate_score(SCORES, EVIDENCE)
    # A perfect model estimate must not become verified profitability.
    assert result.weighted_score == 100.0
    assert result.model_score == 100.0
    assert result.evidence_confidence == result.confidence_level == "HYPOTHESIS"
    assert "NOT VERIFIED" in result.model_score_label
    assert result.verified_source_count == 0
    assert result.verified_field_count == 0


def test_declared_high_without_independent_assessment_cannot_raise_confidence():
    evidence = [
        {
            "field": "demand_evidence",
            "source_id": 1,
            "verified": True,
            "declared_reliability": "HIGH",
            "assessed_reliability": "UNASSESSED",
            "credible": False,
            "support_level": "QUOTE_ONLY_UNCERTAIN",
        }
    ]
    result = ScoringService.calculate_score(
        SCORES, EVIDENCE, verified_evidence=evidence
    )
    assert result.evidence_confidence == "HYPOTHESIS"
    assert result.verified_source_count == 0


def test_quote_from_unassessed_source_requires_human_review():
    project_id = _project("Quote Only Test")
    text = "Clinic owners reported that missed appointments cost them money every month."
    ok, source_id, message = ResearchService.add_source(
        project_id,
        "Opinion blog",
        "MANUAL_NOTE",
        text,
        reliability_assessment="HIGH",  # user declaration only
    )
    assert ok, message

    sources = ResearchService.get_sources(project_id)
    opportunity = {
        **OPP_BASE,
        "evidence": [
            {
                "field": "demand_evidence",
                "source_id": source_id,
                "excerpt": text,
                "claim": "Clinic owners lose money from missed appointments.",
            }
        ],
    }
    verdict = verify_claims(opportunity, sources)
    assert verdict["accepted_count"] == 1
    accepted = verdict["accepted"][0]
    assert accepted["quote_exists"] is True
    assert accepted["declared_reliability"] == "HIGH"
    assert accepted["assessed_reliability"] != "HIGH"
    assert accepted["support_level"] != "VERIFIED_SUPPORT"
    assert accepted["credible"] is False
    assert verdict["human_review_required"] is True

    # The claim must not populate a factual field.
    sanitized = sanitize_fields(opportunity, verdict["accepted"])
    assert sanitized["demand_evidence"].startswith("Unverified hypothesis")

    score = ScoringService.calculate_score(
        SCORES, EVIDENCE, verified_evidence=verdict["accepted"]
    )
    assert score.evidence_confidence == "HYPOTHESIS"

    ProjectService.delete_project(project_id)


def test_requested_count_shortfall_is_reported_and_not_fabricated():
    project_id = _project("Requested Count Test")
    ok, _, message = ResearchService.add_source(
        project_id,
        "Dental operations survey",
        "REPORT",
        "A survey of independent dental clinics measured missed appointments and reminder use. " * 20,
        publisher="Dental Association",
    )
    assert ok, message

    opportunity = OpportunityItem(**OPP_BASE)
    mock = MagicMock(spec=OllamaClient)
    mock.generate_structured.return_value = (
        True,
        OpportunityResponse(opportunities=[opportunity]),
        "ok",
    )

    service = MarketService(mock)
    project = ProjectService.get_project(project_id)
    success, items, message = service.discover_opportunities(project, requested_count=5)
    assert success, message
    assert len(items) == 1
    assert "returned only 1 of 5" in message
    assert "fabricated" in message
    assert "Fewer than five" in message

    refreshed = ProjectService.get_project(project_id)
    assert refreshed["requested_opportunity_count"] == 5

    with get_connection() as conn:
        row = conn.execute(
            "SELECT source_coverage_json, human_review_required FROM opportunities WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        score_row = conn.execute(
            """
            SELECT s.verified_source_count, s.model_score_label
            FROM opportunity_scores s
            JOIN opportunities o ON o.id = s.opportunity_id
            WHERE o.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    assert row["source_coverage_json"]
    assert score_row["verified_source_count"] == 0
    assert "NOT VERIFIED" in score_row["model_score_label"]

    ProjectService.delete_project(project_id)
