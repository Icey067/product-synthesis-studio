import streamlit as st
import pandas as pd
from services.project_service import ProjectService
from services.market_service import MarketService
from services.research_service import ResearchService
from services.ollama_client import OllamaClient

def render_market_intelligence():
    st.markdown("## 🌐 Market Intelligence & Problem Discovery")
    st.markdown("Discover and isolate high-urgency customer problems worth solving.")

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    project = ProjectService.get_project(project_id)
    if not project:
        st.error("Active project not found.")
        return

    # Empirical Research Status Banner (Priority 1)
    sources = ResearchService.get_sources(project_id)
    num_sources = len(sources)

    st.markdown(
        f"""
        <div class="pss-card">
            <b>Active Focus:</b> {project['name']} | <b>Country:</b> {project['country']} |
            <b>Category:</b> {project['product_category']} | <b>Product Format:</b> {project['product_format']} |
            <b>Mode:</b> {project['expertise_mode']}<br/>
            <b>Research Library:</b> {num_sources} source(s) available for claim-level verification
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Source count alone never raises confidence. State that explicitly.
    if num_sources == 0:
        st.warning(
            "⚠️ **No research sources.** Every opportunity will be classified **HYPOTHESIS** "
            "because no claim can be linked to a verified source excerpt."
        )
    else:
        st.info(
            f"**{num_sources} source(s) available.** The engine will quote specific excerpts and verify each "
            "claim against the cited source. Importing unrelated sources does not raise confidence; only "
            "verified claim-to-source links do."
        )

    ai = OllamaClient()
    connected, conn_msg = ai.check_connection()

    requested_default = int(project.get("requested_opportunity_count") or 5)
    requested_count = st.number_input(
        "Number of opportunities to request",
        min_value=1,
        max_value=20,
        value=max(1, min(20, requested_default)),
        step=1,
        help=(
            "The engine will not fabricate opportunities to hit this number. "
            "If the evidence supports fewer, it reports the shortfall honestly."
        ),
    )

    col_btn, col_info = st.columns([1, 2])
    with col_btn:
        generate_clicked = st.button("🚀 Discover & Score Opportunities", disabled=not connected, use_container_width=True)
    with col_info:
        if not connected:
            st.error("Ollama offline. Start Ollama to run discovery inference.")
        else:
            st.caption(
                "AI engine clusters customer friction points, calculates model-estimated scores, "
                "and separates them from independently assessed evidence confidence."
            )

    if generate_clicked:
        with st.spinner("Analyzing market signals, clustering problems, and computing multi-factor scores..."):
            market_service = MarketService(ai)
            success, items, msg = market_service.discover_opportunities(
                project, requested_count=int(requested_count)
            )
            if success:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    # Show existing opportunities
    opps = MarketService.get_project_opportunities(project_id)
    if not opps:
        st.info("No opportunities generated yet. Click 'Discover & Score Opportunities' above.")
        return

    supported = [
        o for o in opps
        if (o.get("evidence_confidence") or o.get("confidence_level") or "HYPOTHESIS")
        in ("HIGH", "MEDIUM")
    ]
    if len(supported) < 5:
        st.warning(
            f"⚠️ Only **{len(supported)} of {len(opps)}** opportunities are responsibly supported by "
            "independently assessed evidence. The rest are hypotheses or quote-only/uncertain claims "
            "and require human review before use. The app will not fabricate supported opportunities "
            "to reach five."
        )

    st.markdown("### 🏆 Top Prioritized Market Opportunities")

    # Table preview
    df_data = []
    for o in opps:
        conf = o.get("evidence_confidence") or o.get("confidence_level") or o.get("evidence_status") or "HYPOTHESIS"
        if conf == "HIGH":
            conf_icon = "🟢 HIGH"
        elif conf == "MEDIUM":
            conf_icon = "🟡 MEDIUM"
        elif conf == "LOW":
            conf_icon = "🟠 LOW"
        elif conf == "LEGACY_UNVERIFIED":
            conf_icon = "⚪ LEGACY (unverified)"
        else:
            conf_icon = "🔴 HYPOTHESIS"
        df_data.append({
            "ID": o["id"],
            "Opportunity Name": o["name"],
            "Target Customer": o["exact_customer"],
            "Urgency": o["problem_urgency"],
            "Model Score (estimate)": f"{o.get('weighted_score', 0.0):.1f}/100",
            "Evidence Confidence": conf_icon,
            "Verified Sources": o.get("verified_source_count") or 0,
            "Favorite": "⭐" if o.get("is_favorite") else "—",
            "Status": "✅ Selected" if project.get("selected_opportunity_id") == o["id"] else "",
        })
    df = pd.DataFrame(df_data)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(
        "Model Score is a weighted estimate from sub-scores assigned by the language model. "
        "It is **not** verified profitability. Evidence Confidence is computed independently from "
        "verified, independently assessed claim-to-source links."
    )

    # Detailed Inspection Cards
    st.markdown("### 🔍 Deep Dive: Top Scored Opportunities")
    for opp in opps[:5]:
        opp_conf = opp.get("evidence_confidence") or opp.get("confidence_level") or "HYPOTHESIS"
        with st.expander(
            f"Opportunity #{opp['id']}: {opp['name']} — Model score {opp.get('weighted_score', 0)}/100 "
            f"(estimate) · Evidence: {opp_conf}",
            expanded=(opp['id'] == project.get('selected_opportunity_id')),
        ):
            c1, c2 = st.columns([2, 1])
            with c1:
                st.markdown(f"**Target Customer:** {opp['exact_customer']}")
                st.markdown(f"**Core Problem:** {opp['customer_problem']}")
                st.markdown(f"**Desired Outcome:** {opp['desired_outcome']}")
                st.markdown(f"**Potential Digital Solution:** {opp['potential_solution']}")
                st.markdown(f"**Observed Competitor Products:** {opp['competitor_products']} ({opp['observed_prices']})")
                st.markdown(f"**Missing Market Gaps:** {opp['missing_features']}")
                st.markdown(f"**Customer Complaints:** {opp['customer_complaints']}")
                st.markdown(f"**Ethical / Legal Cautions:** {opp['ethical_considerations']}")

            with c2:
                st.markdown("##### Multi-Attribute Score Breakdown")
                st.write(f"• Demand Evidence (20%): **{opp.get('demand_score', 5)}/10**")
                st.write(f"• Willingness to Pay (20%): **{opp.get('wtp_score', 5)}/10**")
                st.write(f"• Problem Urgency (20%): **{opp.get('urgency_score', 5)}/10**")
                st.write(f"• Competitor Gap (15%): **{opp.get('gap_score', 5)}/10**")
                st.write(f"• Customer Specificity (10%): **{opp.get('specificity_score', 5)}/10**")
                st.write(f"• Practical Solvability (10%): **{opp.get('solvability_score', 5)}/10**")
                st.write(f"• Ethical Feasibility (5%): **{opp.get('feasibility_score', 5)}/10**")
                
                conf = opp.get('evidence_confidence') or opp.get('confidence_level', 'HYPOTHESIS')
                badge_style = "pss-badge-high" if conf == "HIGH" else ("pss-badge-medium" if conf == "MEDIUM" else "pss-badge-low")
                st.caption(
                    "**Model Score** is a weighted estimate from model-assigned sub-scores. "
                    "It is NOT verified profitability."
                )
                st.markdown(
                    f"<span class='pss-badge {badge_style}'>EVIDENCE CONFIDENCE: {conf}</span>",
                    unsafe_allow_html=True,
                )
                st.write(
                    f"**Verified sources:** {opp.get('verified_source_count', 0)} · "
                    f"**Verified important fields:** {opp.get('verified_field_count', 0)}"
                )
                st.caption(f"**Source coverage:** {opp.get('evidence_coverage', 'N/A')}")
                if opp.get("human_review_required"):
                    st.warning(
                        "Human review required: quotes were found, but factual support is uncertain "
                        "or the source is not independently assessed as credible."
                    )

                missing_ev = opp.get('missing_evidence', 'None')
                if missing_ev and missing_ev != "None":
                    st.warning(f"**Identified Research Gaps:**\n{missing_ev}")

                evidence_rows = MarketService.get_opportunity_evidence(opp["id"])
                st.markdown("##### 🔗 Claim → Source Evidence")
                verified_rows = [e for e in evidence_rows if e.get("verified")]
                rejected_rows = [e for e in evidence_rows if not e.get("verified")]
                if verified_rows:
                    for ev in verified_rows:
                        src = ev.get("source_title") or f"Source-{ev.get('source_id')}"
                        assessed = ev.get("assessed_reliability") or ev.get("reliability") or "UNASSESSED"
                        declared = ev.get("declared_reliability") or "UNASSESSED"
                        st.success(
                            f"**[Source-{ev.get('source_id')}]** {src}\n\n"
                            f"*Field:* `{ev.get('claim_field')}` · *Relevance:* {ev.get('relevance_score')} · "
                            f"*Assessed:* `{assessed}` (declared: `{declared}`)\n\n"
                            f"> {(ev.get('excerpt') or '')[:320]}"
                        )
                        if ev.get("claim_text"):
                            st.caption(f"Claim: {ev.get('claim_text')}")
                        if ev.get("verification_notes"):
                            st.caption(ev.get("verification_notes"))
                else:
                    st.error(
                        "No verified claim-to-source links. This opportunity is a **HYPOTHESIS**, "
                        "regardless of how many sources are imported."
                    )
                if rejected_rows:
                    with st.expander(f"Rejected claims ({len(rejected_rows)}) — model cited unsupported evidence"):
                        for ev in rejected_rows:
                            st.warning(
                                f"[Source-{ev.get('source_id')}] `{ev.get('claim_field')}` — "
                                f"{ev.get('verification_notes') or 'Rejected.'}"
                            )

                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    fav_label = "Unfavorite" if opp.get("is_favorite") else "⭐ Favorite"
                    if st.button(fav_label, key=f"fav_{opp['id']}"):
                        MarketService.toggle_favorite(opp["id"])
                        st.rerun()
                with btn_col2:
                    is_sel = project.get("selected_opportunity_id") == opp["id"]
                    sel_label = "Active Solution" if is_sel else "🎯 Select Solution"
                    if st.button(sel_label, key=f"sel_{opp['id']}", disabled=is_sel):
                        MarketService.select_opportunity(project_id, opp["id"])
                        st.success(f"Selected Opportunity #{opp['id']} as core product thesis!")
                        st.rerun()
