import os
from pathlib import Path
import streamlit as st

from config.settings import APP_NAME, APP_TAGLINE, ASSETS_DIR
from database.db import init_db
from services.project_service import ProjectService
from services.ollama_client import OllamaClient

# Import UI pages.
# They live in ``ui_pages/`` rather than ``pages/`` so Streamlit's automatic
# multipage discovery does not add a second, duplicate navigation menu.  The
# custom sidebar router below is the only navigation.
from ui_pages.dashboard import render_dashboard
from ui_pages.project_setup import render_project_setup
from ui_pages.market_intelligence import render_market_intelligence
from ui_pages.competitors import render_competitor_research
from ui_pages.research_library import render_research_library
from ui_pages.transformation import render_transformation
from ui_pages.product_charter import render_product_charter
from ui_pages.product_builder import render_product_builder
from ui_pages.quality_auditor import render_quality_auditor
from ui_pages.brand_studio import render_brand_studio
from ui_pages.exports import render_exports
from ui_pages.distribution import render_distribution
from ui_pages.analytics import render_analytics
from ui_pages.settings import render_settings

# Page config
st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize database schema automatically on boot
init_db()

# Load custom styling
css_path = ASSETS_DIR / "styles.css"
if css_path.exists():
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# Session state initialization
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "Dashboard"
if "active_project_id" not in st.session_state:
    # Auto-select the most recent project if available
    projects = ProjectService.list_projects()
    st.session_state["active_project_id"] = projects[0]["id"] if projects else None

# Sidebar Navigation
with st.sidebar:
    st.markdown(f"### ⚡ {APP_NAME}")
    st.caption(APP_TAGLINE)

    # Active Project Switcher
    projects = ProjectService.list_projects()
    if projects:
        proj_dict = {p["id"]: p["name"] for p in projects}
        current_id = st.session_state.get("active_project_id")
        selected_id = st.selectbox(
            "Active Project",
            options=list(proj_dict.keys()),
            index=list(proj_dict.keys()).index(current_id) if current_id in proj_dict else 0,
            format_func=lambda x: f"📁 {proj_dict[x]}",
        )
        st.session_state["active_project_id"] = selected_id
    else:
        st.info("No active project. Create one to begin.")

    st.markdown("---")

    nav_pages = [
        "Dashboard",
        "New Project",
        "Market Intelligence",
        "Competitor Research",
        "Research Library",
        "Transformation Map",
        "Product Charter",
        "Product Builder",
        "Quality Auditor",
        "Brand Studio",
        "Product Exports",
        "Sales & Distribution",
        "Analytics",
        "Settings",
    ]

    selected_nav = st.radio(
        "Navigation",
        options=nav_pages,
        index=nav_pages.index(st.session_state["current_page"]) if st.session_state["current_page"] in nav_pages else 0,
        label_visibility="collapsed",
    )
    st.session_state["current_page"] = selected_nav

    st.markdown("---")
    # Quick health indicator
    ai = OllamaClient()
    connected, _ = ai.check_connection()
    if connected:
        st.caption("🟢 Local Ollama Online")
    else:
        st.caption("🔴 Local Ollama Offline")

# Page Routing
page = st.session_state["current_page"]

if page == "Dashboard":
    render_dashboard()
elif page == "New Project":
    render_project_setup()
elif page == "Market Intelligence":
    render_market_intelligence()
elif page == "Competitor Research":
    render_competitor_research()
elif page == "Research Library":
    render_research_library()
elif page == "Transformation Map":
    render_transformation()
elif page == "Product Charter":
    render_product_charter()
elif page == "Product Builder":
    render_product_builder()
elif page == "Quality Auditor":
    render_quality_auditor()
elif page == "Brand Studio":
    render_brand_studio()
elif page == "Product Exports":
    render_exports()
elif page == "Sales & Distribution":
    render_distribution()
elif page == "Analytics":
    render_analytics()
elif page == "Settings":
    render_settings()

