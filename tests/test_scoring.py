import pytest
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


def _verified(field: str, source_id: int = 1, reliability: str = "HIGH"):
    return {
        "field": field,
        "source_id": source_id,
        "reliability": reliability,
        "verified": True,
    }


def test_scoring_weights_and_calculation():
    result = ScoringService.calculate_score(SCORES, EVIDENCE)
    assert result.weighted_score == 100.0


def test_unrelated_imported_sources_never_raise_confidence():
    """Importing sources (verified_sources_count) cannot create confidence.

    Regression for FIX 1: confidence must come from verified claim links only.
    """
    result = ScoringService.calculate_score(SCORES, EVIDENCE, verified_sources_count=3)
    assert result.confidence_level == "HYPOTHESIS"
    assert "imported source(s) were not used" in result.evidence_coverage


def test_high_confidence_requires_multiple_verified_sources_and_claims():
    evidence = [
        _verified("demand_evidence", 1),
        _verified("willingness_to_pay_evidence", 2),
        _verified("customer_complaints", 2),
    ]
    result = ScoringService.calculate_score(
        SCORES, EVIDENCE, verified_sources_count=2, verified_evidence=evidence
    )
    assert result.confidence_level == "HIGH"
    assert "Source-1" in result.evidence_coverage
    assert "Source-2" in result.evidence_coverage


def test_medium_confidence_with_limited_evidence():
    evidence = [
        _verified("demand_evidence", 1),
        _verified("existing_competitors", 1),
    ]
    result = ScoringService.calculate_score(SCORES, EVIDENCE, verified_evidence=evidence)
    assert result.confidence_level == "MEDIUM"


def test_low_confidence_with_single_weak_claim():
    evidence = [_verified("missing_features", 1, reliability="LOW")]
    result = ScoringService.calculate_score(SCORES, EVIDENCE, verified_evidence=evidence)
    assert result.confidence_level == "LOW"


def test_scoring_missing_evidence_detection():
    empty = {
        "demand_evidence": "Unknown",
        "willingness_to_pay_evidence": "Unknown",
        "existing_competitors": "None observed",
        "customer_complaints": "",
        "missing_features": "",
    }
    result = ScoringService.calculate_score(
        {k: 5.0 for k in SCORES}, empty
    )
    assert result.weighted_score == 50.0
    assert result.confidence_level == "HYPOTHESIS"
    assert "Demand verification signals missing" in result.missing_evidence
    assert "No documented willingness to pay observed" in result.missing_evidence
