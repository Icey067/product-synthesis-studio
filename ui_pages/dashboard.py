import streamlit as st
from services.project_service import ProjectService
from services.ollama_client import OllamaClient
from services.manuscript_service import ManuscriptService

def render_dashboard():
    st.markdown("## 📊 Executive Studio Dashboard")
    st.markdown("Monitor digital-product pipelines, research assets, and AI inference capacity.")

    metrics = ProjectService.get_dashboard_metrics()
    ai = OllamaClient()
    connected, conn_msg = ai.check_connection()
    models = ai.list_models()

    # Top KPI Metrics Row
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f"""
            <div class="pss-card">
                <div class="pss-stat-number">{metrics['total_projects']}</div>
                <div class="pss-stat-label">Total Projects</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div class="pss-card">
                <div class="pss-stat-number">{metrics['total_sources']}</div>
                <div class="pss-stat-label">Research Sources</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""
            <div class="pss-card">
                <div class="pss-stat-number">{metrics['total_opportunities']}</div>
                <div class="pss-stat-label">Scored Opportunities</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f"""
            <div class="pss-card">
                <div class="pss-stat-number">{metrics['total_exports']}</div>
                <div class="pss-stat-label">Exported Products</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Status & System Indicator
    st.markdown("---")
    st.markdown("### ⚙️ Local AI Engine Status")
    sc1, sc2 = st.columns([2, 2])
    with sc1:
        if connected:
            st.success(f"🟢 **Ollama Local Engine:** Connected (`{ai.base_url}`)")
        else:
            st.error(f"🔴 **Ollama Offline:** {conn_msg}")
            st.info("Tip: Start Ollama in PowerShell by typing `ollama serve`")
    with sc2:
        curr_model = ai._get_selected_model()
        if curr_model:
            st.info(
                f"🧠 **Active Model:** `{curr_model}` "
                f"({len(models)} installed model{'s' if len(models) != 1 else ''} detected)"
            )
        else:
            st.warning(
                "🧠 **No installed Ollama model detected.** Pull one locally, then "
                "choose a default and per-task overrides in Settings."
            )

    st.markdown("---")
    st.markdown("### 📁 Recent Projects")

    projects = ProjectService.list_projects()
    if not projects:
        st.info("No projects created yet. Click **'New Project'** in the navigation menu to launch your first digital product factory.")
        return

    for p in projects:
        with st.container():
            assessment = ManuscriptService.assess_project(p["id"])
            counts = assessment["counts"]
            col_info, col_prog, col_actions = st.columns([3, 2, 2])
            with col_info:
                st.markdown(f"#### {p['name']}")
                st.caption(
                    f"**Format:** {p['product_format']} | **Country:** {p['country']} | **Mode:** {p['expertise_mode']}"
                )
                st.caption(
                    f"**Manuscript:** {counts['written_chapters']}/{counts['required_chapters']} written · "
                    f"{counts['min_word_compliant_chapters']} at minimum length · "
                    f"{counts['publication_ready_chapters']} publication-ready · "
                    f"{counts.get('gate_blocked_chapters', 0)} gate-blocked · "
                    f"lifecycle {assessment.get('lifecycle_status', 'UNKNOWN')}"
                )
            with col_prog:
                st.write(f"Research Completion: **{p['research_progress']}%**")
                st.progress(p['research_progress'] / 100.0)
                st.caption(f"Last updated: {p['updated_at']}")
            with col_actions:
                st.write("")
                b1, b2, b3 = st.columns(3)
                with b1:
                    if st.button("Open", key=f"open_{p['id']}"):
                        st.session_state["active_project_id"] = p["id"]
                        st.session_state["current_page"] = "Market Intelligence"
                        st.rerun()
                with b2:
                    if st.button("Copy", key=f"dup_{p['id']}"):
                        new_id = ProjectService.duplicate_project(p["id"])
                        st.success(f"Duplicated as Project #{new_id}")
                        st.rerun()
                with b3:
                    if st.button("Delete", key=f"del_{p['id']}"):
                        ProjectService.delete_project(p["id"])
                        if st.session_state.get("active_project_id") == p["id"]:
                            st.session_state["active_project_id"] = None
                        st.rerun()
            st.markdown("<hr style='margin: 0.5rem 0; border-color: #1F2937;'/>", unsafe_allow_html=True)

    # Activity Timeline
    if metrics["recent_activities"]:
        st.markdown("### 🕒 Recent Activity Log")
        for act in metrics["recent_activities"]:
            st.markdown(f"• `{act['created_at']}` — **{act['action']}**: {act.get('details', '')}")

