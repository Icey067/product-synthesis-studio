import streamlit as st
from services.project_service import ProjectService
from services.charter_service import CharterService
from services.market_service import MarketService
from services.research_service import ResearchService

def render_product_charter():
    st.markdown("## 📜 Master Product Charter")
    st.markdown("The central design blueprint governing ebook scope, promises, deliverables, and disclaimers.")

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    project = ProjectService.get_project(project_id)
    opps = MarketService.get_project_opportunities(project_id)
    selected_opp = next((o for o in opps if o["id"] == project.get("selected_opportunity_id")), None) if opps else None
    sources = ResearchService.get_sources(project_id)

    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("⚡ Generate Master Charter", use_container_width=True):
            with st.spinner("Synthesizing research into authoritative product blueprint..."):
                c_service = CharterService()
                res_summary = " ".join([f"[{s['title']}]: {s.get('raw_content','')[:300]}" for s in sources[:5]])
                ok, data, msg = c_service.generate_charter(
                    project=project,
                    opportunity=selected_opp,
                    research_summary=res_summary or "Grounded in practical operational frameworks",
                )
                if ok:
                    st.success("Product Charter created successfully!")
                    st.rerun()
                else:
                    st.error(msg)
    with col2:
        st.caption("The Product Charter anchors all downstream chapter generation, quality audits, and sales copy.")

    charter = CharterService.get_charter(project_id)
    if not charter:
        st.info("No Product Charter generated yet. Click 'Generate Master Charter' to build one.")
        return

    st.markdown("---")
    from services.product_format_service import validate_charter as validate_charter_format, contract_text
    fmt = project.get("product_format", charter.get("product_format", "Ebook"))
    st.markdown(
        f"""
        <div class="pss-card">
            <b>Selected Product Format:</b> {fmt} — the charter, outline, chapters, sales copy and exports must all match this format.
        </div>
        """,
        unsafe_allow_html=True,
    )
    fmt_ok, fmt_issues = validate_charter_format(fmt, charter)
    if not fmt_ok:
        st.error("⚠️ The current charter violates its product-format contract:")
        for issue in fmt_issues:
            st.markdown(f"• **[{issue['severity']}]** {issue['message']}")
        with st.expander("Required correction guidance"):
            st.text(contract_text(fmt))

    with st.form("edit_charter_form"):
        st.markdown("### ✏️ Edit & Refine Product Charter")

        c_t1, c_t2 = st.columns([2, 1])
        with c_t1:
            title = st.text_input("Product Title", value=charter.get("product_title", ""))
            subtitle = st.text_input("Subtitle", value=charter.get("subtitle", ""))
        with c_t2:
            cat = st.text_input("Category", value=charter.get("product_category", ""))
            audience = st.text_input("Target Audience", value=charter.get("target_audience", ""))

        c_p1, c_p2 = st.columns(2)
        with c_p1:
            problem = st.text_area("Acute Customer Problem", value=charter.get("customer_problem", ""), height=100)
            promise = st.text_area("Core Promise & Measurable Outcome", value=charter.get("core_promise", ""), height=100)
        with c_p2:
            mechanism = st.text_area("Unique Mechanism", value=charter.get("unique_mechanism", ""), height=100)
            diff = st.text_area("Differentiation Strategy", value=charter.get("differentiation", ""), height=100)

        desc = st.text_area("Comprehensive Product Description", value=charter.get("product_description", ""), height=120)

        deliv_str = "\n".join(charter.get("deliverables", []))
        deliv = st.text_area("Deliverables (one per line)", value=deliv_str, height=100)

        disc_str = "\n".join(charter.get("disclaimer_requirements", []))
        disclaimers = st.text_area("Mandatory Disclaimers (one per line)", value=disc_str, height=100)

        if st.form_submit_button("Save Charter Revisions"):
            charter_updates = {
                "product_title": title.strip(),
                "subtitle": subtitle.strip(),
                "product_category": cat.strip(),
                "target_audience": audience.strip(),
                "customer_problem": problem.strip(),
                "core_promise": promise.strip(),
                "desired_outcome": charter.get("desired_outcome", ""),
                "unique_mechanism": mechanism.strip(),
                "differentiation": diff.strip(),
                "product_description": desc.strip(),
                "deliverables": [d.strip() for d in deliv.split("\n") if d.strip()],
                "evidence_base": charter.get("evidence_base", ""),
                "product_limitations": charter.get("product_limitations", []),
                "scope_exclusions": charter.get("scope_exclusions", []),
                "disclaimer_requirements": [d.strip() for d in disclaimers.split("\n") if d.strip()],
            }
            CharterService.update_charter(project_id, charter_updates)
            st.success("Charter updated successfully!")
            st.rerun()

