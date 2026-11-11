import json
from typing import List, Dict, Any, Optional
from database.db import get_connection, transaction
from models.schemas import OpportunityResponse
from services.scoring_service import ScoringService
from services.evidence_service import verify_claims, sanitize_fields
from services.research_service import ResearchService
from services.ollama_client import OllamaClient
from config.prompts import PROMPT_MARKET_INTELLIGENCE

DEFAULT_REQUESTED_OPPORTUNITIES = 5


class MarketService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    @staticmethod
    def _requested_count(project: Dict[str, Any], override: Optional[int] = None) -> int:
        if override is not None:
            return max(1, int(override))
        try:
            value = int(project.get("requested_opportunity_count") or DEFAULT_REQUESTED_OPPORTUNITIES)
        except (TypeError, ValueError):
            value = DEFAULT_REQUESTED_OPPORTUNITIES
        return max(1, value)

    def discover_opportunities(
        self,
        project: Dict[str, Any],
        requested_count: Optional[int] = None,
        batch_ids: Optional[List[int]] = None,
    ) -> tuple[bool, List[Dict[str, Any]], str]:
        """
        Evidence-linked market intelligence.

        The model receives only a bounded set of relevance-ranked research
        excerpts.  Every evidence claim is re-verified against the exact source
        text.  Declared reliability never establishes credibility; only the
        independent source assessment can do that.
        """
        sources = ResearchService.get_sources(project["id"], batch_ids=batch_ids)
        research_context, selected_sources = ResearchService.build_evidence_context(
            project,
            sources,
            query_extra=str(project.get("creator_interests", "")),
            limit=8,
            max_chars_per_source=900,
            max_chunks=16,
            batch_ids=batch_ids,
        )
        requested = self._requested_count(project, requested_count)

        def _prompt(extra: str = "") -> str:
            base = PROMPT_MARKET_INTELLIGENCE.format(
                project_name=project["name"],
                country=project.get("country", "United States"),
                language=project.get("language", "English"),
                format=project.get("product_format", "Ebook"),
                category=project.get("product_category", "Business"),
                mode=project.get("expertise_mode", "NON_EXPERT"),
                target_customer=project.get("target_customer") or "General target segment",
                creator_interests=project.get("creator_interests") or "Broad practical problem solving",
                research_context=research_context,
                requested_count=requested,
            )
            return base + extra

        success, validated, msg = self.ai.generate_structured(
            prompt=_prompt(),
            schema=OpportunityResponse,
            temperature=0.4,
            task="market_research",
        )
        if not success or not validated:
            return False, [], f"AI Market Intelligence generation failed: {msg}"

        model_items: List[Dict[str, Any]] = [opp.model_dump() for opp in validated.opportunities]

        # Ask for additional distinct opportunities only when the model returned
        # fewer than requested.  The application never fabricates extras; it
        # simply reports the shortfall.
        if len(model_items) < requested and sources:
            seen = {str(item.get("name", "")).strip().lower() for item in model_items}
            remaining = requested - len(model_items)
            supplement = (
                f"\n\nSUPPLEMENTARY REQUEST: The previous response contained {len(model_items)} "
                f"opportunities. Produce {remaining} additional, distinct opportunities that are not "
                "duplicates. Use only the same source IDs and excerpts. If the evidence cannot support "
                "more, return an empty list rather than inventing evidence."
            )
            ok2, validated2, _ = self.ai.generate_structured(
                prompt=_prompt(supplement),
                schema=OpportunityResponse,
                temperature=0.45,
                task="market_research",
            )
            if ok2 and validated2:
                for opp in validated2.opportunities:
                    item = opp.model_dump()
                    key = str(item.get("name", "")).strip().lower()
                    if key and key not in seen:
                        seen.add(key)
                        model_items.append(item)

        saved_opportunities = []
        with transaction() as conn:
            cur = conn.cursor()
            # Clear previous opportunities for this project to refresh (cascades evidence)
            cur.execute("DELETE FROM opportunities WHERE project_id = ?", (project["id"],))
            if requested_count is not None:
                cur.execute(
                    "UPDATE projects SET requested_opportunity_count = ? WHERE id = ?",
                    (requested, project["id"]),
                )

            for opp_dict in model_items:
                verdict = verify_claims(opp_dict, sources)
                accepted = verdict["accepted"]
                sanitized = sanitize_fields(opp_dict, accepted)

                scores_dict = {
                    "demand_score": opp_dict.get("demand_score", 5.0),
                    "willingness_to_pay_score": opp_dict.get("willingness_to_pay_score", 5.0),
                    "urgency_score": opp_dict.get("urgency_score", 5.0),
                    "competitor_gap_score": opp_dict.get("competitor_gap_score", 5.0),
                    "customer_specificity_score": opp_dict.get("customer_specificity_score", 5.0),
                    "practical_solvability_score": opp_dict.get("practical_solvability_score", 5.0),
                    "feasibility_score": opp_dict.get("feasibility_score", 5.0),
                }
                evidence_data = {
                    **sanitized,
                    "scoring_reasoning": opp_dict.get("scoring_reasoning", ""),
                }
                score_detail = ScoringService.calculate_score(
                    scores_dict,
                    evidence_data,
                    verified_sources_count=len(sources),
                    verified_evidence=accepted,
                    relevance_summary=(
                        f"Selected {len(selected_sources)} relevance-ranked source(s) "
                        f"from {len(sources)} imported source(s)."
                    ),
                )

                coverage = {
                    "requested_count": requested,
                    "verified_claim_count": verdict["accepted_count"],
                    "quote_only_claim_count": verdict["quote_only_count"],
                    "rejected_claim_count": verdict["rejected_count"],
                    "verified_fields": verdict["verified_fields"],
                    "credible_fields": verdict["credible_fields"],
                    "credible_source_ids": verdict["credible_source_ids"],
                    "cited_source_ids": sorted({
                        int(e["source_id"]) for e in accepted if e.get("source_id") is not None
                    }),
                    "assessed_sources": {
                        str(e["source_id"]): e.get("assessed_reliability", "UNASSESSED")
                        for e in accepted
                    },
                    "imported_source_count": len(sources),
                    "selected_source_ids": [int(s["id"]) for s in selected_sources],
                    "selected_source_count": len(selected_sources),
                    "human_review_required": verdict["human_review_required"],
                }
                citation_ids = coverage["cited_source_ids"]
                human_review = bool(
                    verdict["human_review_required"] or score_detail.evidence_confidence == "LOW"
                )

                cur.execute(
                    """
                    INSERT INTO opportunities (
                        project_id, name, exact_customer, customer_problem, problem_urgency,
                        desired_outcome, demand_evidence, willingness_to_pay_evidence,
                        existing_competitors, competitor_products, observed_prices,
                        customer_complaints, missing_features, potential_solution,
                        commercial_risks, ethical_considerations, evidence_confidence,
                        evidence_status, evidence_summary, source_coverage_json,
                        human_review_required, citation_ids_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project["id"],
                        opp_dict.get("name", "Untitled opportunity"),
                        opp_dict.get("exact_customer", ""),
                        opp_dict.get("customer_problem", ""),
                        opp_dict.get("problem_urgency", ""),
                        opp_dict.get("desired_outcome", ""),
                        sanitized["demand_evidence"],
                        sanitized["willingness_to_pay_evidence"],
                        sanitized["existing_competitors"],
                        sanitized["competitor_products"],
                        sanitized["observed_prices"],
                        sanitized["customer_complaints"],
                        sanitized["missing_features"],
                        opp_dict.get("potential_solution", ""),
                        opp_dict.get("commercial_risks", ""),
                        opp_dict.get("ethical_considerations", ""),
                        score_detail.evidence_confidence,
                        score_detail.evidence_confidence,
                        json.dumps(coverage),
                        json.dumps(coverage),
                        1 if human_review else 0,
                        json.dumps(citation_ids),
                    ),
                )
                opp_id = cur.lastrowid

                # Only real rows may be referenced by a foreign key.  A rejected
                # claim can cite a hallucinated or deleted source ID; keep that
                # fact in the verification note but store NULL for the FK.
                valid_source_ids = {int(s["id"]) for s in sources}

                def _existing_source_id(value):
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        return None
                    return value if value in valid_source_ids else None

                for ev in accepted:
                    cur.execute(
                        """
                        INSERT INTO opportunity_evidence (
                            opportunity_id, source_id, claim_field, claim_text, excerpt,
                            relevance_score, verified, verification_notes
                        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            opp_id, _existing_source_id(ev.get("source_id")), ev["field"],
                            ev.get("claim", ""), ev.get("excerpt", ""),
                            ev.get("relevance_score", 0.0), ev.get("notes", ""),
                        ),
                    )
                for ev in verdict["rejected"]:
                    cur.execute(
                        """
                        INSERT INTO opportunity_evidence (
                            opportunity_id, source_id, claim_field, claim_text, excerpt,
                            relevance_score, verified, verification_notes
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                        """,
                        (
                            opp_id, _existing_source_id(ev.get("source_id")),
                            ev.get("field", ""), ev.get("claim", ""),
                            ev.get("excerpt", ""), ev.get("relevance_score", 0.0),
                            ev.get("notes", ""),
                        ),
                    )

                cur.execute(
                    """
                    INSERT INTO opportunity_scores (
                        opportunity_id, weighted_score, demand_score, wtp_score, urgency_score,
                        gap_score, specificity_score, solvability_score, feasibility_score,
                        evidence_coverage, confidence_level, evidence_confidence, model_score_label,
                        source_coverage_json, verified_source_count, verified_field_count,
                        reasoning, missing_evidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        opp_id,
                        score_detail.weighted_score,
                        scores_dict["demand_score"],
                        scores_dict["willingness_to_pay_score"],
                        scores_dict["urgency_score"],
                        scores_dict["competitor_gap_score"],
                        scores_dict["customer_specificity_score"],
                        scores_dict["practical_solvability_score"],
                        scores_dict["feasibility_score"],
                        score_detail.evidence_coverage,
                        score_detail.evidence_confidence,
                        score_detail.evidence_confidence,
                        score_detail.model_score_label,
                        json.dumps(coverage),
                        score_detail.verified_source_count,
                        score_detail.verified_field_count,
                        score_detail.reasoning,
                        score_detail.missing_evidence,
                    ),
                )
                saved_opportunities.append({
                    "id": opp_id,
                    "name": opp_dict.get("name", ""),
                    "score": score_detail.weighted_score,
                    "model_score": score_detail.model_score,
                    "confidence": score_detail.evidence_confidence,
                    "evidence_confidence": score_detail.evidence_confidence,
                    "verified_sources": score_detail.verified_source_count,
                    "human_review_required": human_review,
                })

            supported_count = sum(
                1 for o in saved_opportunities if o["confidence"] in ("HIGH", "MEDIUM")
            )
            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (
                    project["id"],
                    "OPPORTUNITIES_DISCOVERED",
                    f"Discovered {len(saved_opportunities)} opportunities; requested {requested}; "
                    f"{supported_count} independently evidence-supported.",
                ),
            )

        supported_count = sum(
            1 for o in saved_opportunities if o["confidence"] in ("HIGH", "MEDIUM")
        )
        high = sum(1 for o in saved_opportunities if o["confidence"] == "HIGH")
        medium = sum(1 for o in saved_opportunities if o["confidence"] == "MEDIUM")
        message = (
            f"Discovered {len(saved_opportunities)} opportunities (requested {requested}): "
            f"{high} HIGH, {medium} MEDIUM, {len(saved_opportunities) - supported_count} hypothesis/uncertain."
        )
        if supported_count < 5:
            message += (
                " Fewer than five opportunities can be responsibly supported by independently "
                "assessed evidence; the remaining items are hypotheses and require human review."
            )
        if len(saved_opportunities) < requested:
            message += (
                f" The model returned only {len(saved_opportunities)} of {requested} requested "
                "opportunities; no additional opportunities were fabricated."
            )
        return True, saved_opportunities, message

    @staticmethod
    def get_project_opportunities(project_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT o.*, s.weighted_score, s.demand_score, s.wtp_score, s.urgency_score,
                       s.gap_score, s.specificity_score, s.solvability_score, s.feasibility_score,
                       s.evidence_coverage, s.confidence_level, s.evidence_confidence,
                       s.model_score_label, s.source_coverage_json,
                       s.verified_source_count, s.verified_field_count,
                       s.reasoning, s.missing_evidence
                FROM opportunities o
                LEFT JOIN opportunity_scores s ON o.id = s.opportunity_id
                WHERE o.project_id = ?
                ORDER BY s.weighted_score DESC
                """,
                (project_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def get_opportunity_evidence(opportunity_id: int, verified_only: bool = False) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            query = """
                SELECT e.*, s.title AS source_title, s.url AS source_url,
                       s.publisher, s.reliability_assessment,
                       s.declared_reliability, s.assessed_reliability,
                       s.assessment_score
                FROM opportunity_evidence e
                LEFT JOIN research_sources s ON e.source_id = s.id
                WHERE e.opportunity_id = ?
            """
            params: list = [opportunity_id]
            if verified_only:
                query += " AND e.verified = 1"
            query += " ORDER BY e.verified DESC, e.id ASC"
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def toggle_favorite(opportunity_id: int) -> bool:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE opportunities SET is_favorite = NOT is_favorite WHERE id = ?", (opportunity_id,))
            return True

    @staticmethod
    def select_opportunity(project_id: int, opportunity_id: int) -> bool:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE projects SET selected_opportunity_id = ? WHERE id = ?", (opportunity_id, project_id))
            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (project_id, "OPPORTUNITY_SELECTED", f"Selected Opportunity #{opportunity_id} as primary product direction"),
            )
            return True
