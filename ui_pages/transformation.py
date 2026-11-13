import streamlit as st
from services.project_service import ProjectService
from services.transformation_service import TransformationService
from services.market_service import MarketService
from services.target_integrity_service import TargetIntegrityService

def render_transformation():
    st.markdown("## 🔄 Customer Transformation Map")
    st.markdown("Map the journey from acute frustration (Before) to measurable capability (After).")

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    project = ProjectService.get_project(project_id)
    opps = MarketService.get_project_opportunities(project_id)
    selected_opp = next((o for o in opps if o["id"] == project.get("selected_opportunity_id")), None) if opps else None

    # Top Action Bar
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("⚡ Generate Transformation Map", use_container_width=True):
            with st.spinner("Mapping customer journey and milestone roadmap..."):
                t_service = TransformationService()
                customer = selected_opp["exact_customer"] if selected_opp else project.get("target_customer", "Target Audience")
                problem = selected_opp["customer_problem"] if selected_opp else "Operational stagnation"
                outcome = selected_opp["desired_outcome"] if selected_opp else "Systematic growth"
                ok, data, msg = t_service.generate_transformation_map(
                    project_id=project_id,
                    product_title=project["name"],
                    customer=customer,
                    problem=problem,
                    target_outcome=outcome,
                )
                if ok:
                    st.success("Transformation Map created!")
                    st.rerun()
                else:
                    st.error(msg)
    with c2:
        if selected_opp:
            st.caption(f"Using thesis: **{selected_opp['name']}**")
        else:
            st.caption("Tip: Select an Opportunity in Market Intelligence first for higher specificity.")

    # Display Map
    current_map = TransformationService.get_map(project_id)
    if not current_map:
        st.info("No transformation map generated yet. Click 'Generate Transformation Map' above.")
        return

    before = current_map.get("before_situation", {})
    after = current_map.get("after_situation", {})
    milestones = current_map.get("milestones", [])

    st.markdown("---")
    col_b, col_a = st.columns(2)

    with col_b:
        st.markdown(
            """
            <div class="pss-card" style="border-left: 4px solid #EF4444;">
                <div class="pss-card-header" style="color: #F87171;">🔴 The 'BEFORE' State (Current Pain)</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(f"**Current Situation:** {before.get('current_situation', 'Stuck with unoptimized operations')}")

        st.markdown("**Core Frustrations:**")
        for f in before.get("frustrations", []):
            st.markdown(f"• {f}")

        st.markdown("**Common Mistakes & Obstacles:**")
        for m in before.get("common_mistakes", []) + before.get("obstacles", []):
            st.markdown(f"• {m}")

    with col_a:
        st.markdown(
            """
            <div class="pss-card" style="border-left: 4px solid #10B981;">
                <div class="pss-card-header" style="color: #34D399;">🟢 The 'AFTER' State (Empowered Result)</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(f"**Desired Situation:** {after.get('desired_situation', 'Clear repeatable systems operating efficiently')}")

        st.markdown("**New Capabilities:**")
        for cap in after.get("new_capabilities", []):
            st.markdown(f"• {cap}")

        st.markdown("**Measurable Results:**")
        for res in after.get("measurable_results", []):
            st.markdown(f"• {res}")

    targets = TargetIntegrityService.assessment(project_id)
    if targets["targets"]:
        st.markdown("### 🎯 Target Integrity")
        st.caption("Targets are planning aids. Only targets with explicit evidence may be presented as evidence-supported outcomes.")
        for target in targets["targets"]:
            if target["evidence_status"] == "SUPPORTED":
                st.success(f"{target['target_text']} — evidence-supported outcome")
            elif target["evidence_status"] == "REJECTED":
                st.error(f"{target['target_text']} — {target['display_label']}")
            else:
                st.warning(f"{target['target_text']} — {target['display_label']}")

    st.markdown("---")
    st.markdown("### 🗺️ Step-by-Step Implementation Milestones")
    for m in milestones:
        with st.expander(f"Phase {m.get('phase_number', 1)}: {m.get('title', 'Milestone')}", expanded=True):
            st.markdown(f"**Starting Point:** {m.get('starting_point', '')}")
            st.markdown("**Key Actions:**")
            for act in m.get("key_actions", []):
                st.markdown(f"- [ ] {act}")
            st.markdown(f"**Obstacles & Remedies:** {m.get('obstacles_and_remedies', '')}")
            st.markdown(f"**Measurable Progress:** `{m.get('measurable_progress', '')}`")

