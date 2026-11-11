from typing import Dict, Any, List, Optional
from models.schemas import OpportunityScoreDetail

class ScoringService:
    """
    Weighted Scoring Matrix (Part 6 requirements):
    - Demand evidence: 20%
    - Willingness to pay: 20%
    - Problem urgency: 20%
    - Competitor gap: 15%
    - Customer specificity: 10%
    - Practical solvability: 10%
    - Ethical and practical feasibility: 5%
    Total = 100%

    Confidence is NOT derived from the number of imported sources. It is derived
    only from verified claim-to-source links produced by EvidenceService.
    """

    WEIGHTS = {
        "demand": 0.20,
        "wtp": 0.20,
        "urgency": 0.20,
        "gap": 0.15,
        "specificity": 0.10,
        "solvability": 0.10,
        "feasibility": 0.05,
    }

    IMPORTANT_FIELDS = {
        "demand_evidence",
        "willingness_to_pay_evidence",
        "existing_competitors",
        "observed_prices",
        "customer_complaints",
        "missing_features",
    }

    @classmethod
    def calculate_score(
        cls,
        scores: Dict[str, float],
        evidence_data: Dict[str, Any],
        verified_sources_count: int = 0,  # kept for backwards compatibility; never used for confidence
        verified_evidence: Optional[List[Dict[str, Any]]] = None,
        relevance_summary: str = "",
    ) -> OpportunityScoreDetail:
        demand = float(scores.get("demand_score", 5.0))
        wtp = float(scores.get("willingness_to_pay_score", 5.0))
        urgency = float(scores.get("urgency_score", 5.0))
        gap = float(scores.get("competitor_gap_score", 5.0))
        specificity = float(scores.get("customer_specificity_score", 5.0))
        solvability = float(scores.get("practical_solvability_score", 5.0))
        feasibility = float(scores.get("feasibility_score", 5.0))

        # Weighted calculation (each score is 1-10, scale to 0-100)
        weighted_val = (
            (demand * cls.WEIGHTS["demand"])
            + (wtp * cls.WEIGHTS["wtp"])
            + (urgency * cls.WEIGHTS["urgency"])
            + (gap * cls.WEIGHTS["gap"])
            + (specificity * cls.WEIGHTS["specificity"])
            + (solvability * cls.WEIGHTS["solvability"])
            + (feasibility * cls.WEIGHTS["feasibility"])
        ) * 10.0

        def _assessed(item: Dict[str, Any]) -> str:
            return str(
                item.get("assessed_reliability") or item.get("reliability", "UNASSESSED")
            ).upper()

        accepted = [e for e in (verified_evidence or []) if e.get("verified")]
        # Only independently assessed HIGH/MEDIUM sources can contribute
        # credibility.  A user-declared HIGH label is never enough.
        credible_accepted = [
            e for e in accepted
            if e.get("credible") is not False and _assessed(e) in ("HIGH", "MEDIUM")
        ]
        # Independently assessed LOW sources still produce a (weak) verified
        # signal, but they can never raise confidence above LOW.
        weak_accepted = [e for e in accepted if _assessed(e) == "LOW" and e.get("credible") is not False]
        verified_fields = {str(e.get("field")) for e in credible_accepted}
        important_verified = verified_fields & cls.IMPORTANT_FIELDS
        credible_sources = {
            int(e.get("source_id"))
            for e in credible_accepted
            if e.get("source_id") is not None
        }

        missing = []
        for field, label in [
            ("demand_evidence", "Demand verification signals missing"),
            ("willingness_to_pay_evidence", "No documented willingness to pay observed"),
            ("existing_competitors", "Competitor audit not conducted"),
            ("customer_complaints", "Direct customer complaints unverified"),
            ("missing_features", "Feature gap evidence missing"),
            ("observed_prices", "Observed prices not verified"),
        ]:
            if field not in verified_fields:
                missing.append(label)

        has_demand = "demand_evidence" in important_verified
        has_money_or_pain = bool(
            important_verified & {"willingness_to_pay_evidence", "customer_complaints", "observed_prices"}
        )

        if len(credible_sources) >= 2 and len(important_verified) >= 3 and has_demand and has_money_or_pain:
            confidence = "HIGH"
            coverage_desc = (
                f"Multiple independently assessed sources ({', '.join(f'Source-{s}' for s in sorted(credible_sources))}) "
                f"support {len(important_verified)} important claims."
            )
        elif (len(credible_sources) >= 1 and len(important_verified) >= 2) or (
            len(credible_sources) >= 2 and len(important_verified) >= 1
        ):
            confidence = "MEDIUM"
            coverage_desc = (
                f"Limited but relevant evidence from "
                f"{', '.join(f'Source-{s}' for s in sorted(credible_sources))}; "
                f"{len(important_verified)} important claims verified."
            )
        elif credible_accepted:
            confidence = "LOW"
            coverage_desc = (
                f"Weak or indirect evidence only ({len(credible_accepted)} verified claim(s)); "
                "important claims remain unsupported."
            )
        elif weak_accepted:
            confidence = "LOW"
            coverage_desc = (
                f"Only LOW-quality independently assessed evidence supports "
                f"{len(weak_accepted)} claim(s). Low-quality sources cannot raise confidence."
            )
        elif accepted:
            confidence = "HYPOTHESIS"
            coverage_desc = (
                f"{len(accepted)} quote(s) were found, but their sources are UNASSESSED or LOW quality. "
                "Quote existence is not factual support; HUMAN REVIEW REQUIRED."
            )
        else:
            confidence = "HYPOTHESIS"
            coverage_desc = "AI-generated market hypothesis. No claim is linked to a verified source excerpt."

        if verified_sources_count and not accepted:
            coverage_desc += (
                f" ({verified_sources_count} imported source(s) were not used because no claim linked to them.)"
            )

        if relevance_summary:
            coverage_desc += f" {relevance_summary}"

        return OpportunityScoreDetail(
            weighted_score=round(weighted_val, 2),
            demand_pct=round(demand * 10, 1),
            wtp_pct=round(wtp * 10, 1),
            urgency_pct=round(urgency * 10, 1),
            gap_pct=round(gap * 10, 1),
            specificity_pct=round(specificity * 10, 1),
            solvability_pct=round(solvability * 10, 1),
            feasibility_pct=round(feasibility * 10, 1),
            evidence_coverage=coverage_desc,
            confidence_level=confidence,
            reasoning=evidence_data.get("scoring_reasoning", "Calculated using standard weighted multi-attribute model."),
            missing_evidence="; ".join(missing) if missing else "All important claims are linked to verified sources.",
            model_score=round(weighted_val, 2),
            evidence_confidence=confidence,
            source_coverage=coverage_desc,
            verified_source_count=len(credible_sources),
            verified_field_count=len(important_verified),
        )
