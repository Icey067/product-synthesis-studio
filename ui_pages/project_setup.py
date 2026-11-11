import streamlit as st
from services.project_service import ProjectService
from models.schemas import ProjectCreate
from models.enums import ExpertiseMode, ProductFormat
from config.settings import (
    DEFAULT_CHAPTER_TARGET_WORDS,
    DEFAULT_MIN_CHAPTER_WORDS,
    DEFAULT_REQUIRED_CHAPTER_COUNT,
)

def render_project_setup():
    st.markdown("## 🚀 Intelligent Project Setup Wizard")
    st.markdown("Define the operational parameters for your new digital product synthesis pipeline.")

    with st.form("new_project_form"):
        col1, col2 = st.columns(2)

        with col1:
            project_name = st.text_input("Project Name *", placeholder="e.g., Local Business Digital Growth")
            country = st.selectbox(
                "Target Country *",
                ["United States", "India", "United Kingdom", "Canada", "Australia", "Germany", "Singapore", "Other"],
                index=0,
            )
            language = st.selectbox("Product Language *", ["English", "Spanish", "French", "German", "Hindi"], index=0)
            target_customer = st.text_input("Target Customer (if known)", placeholder="e.g., Independent coffee shop owners")
            category = st.selectbox(
                "Product Category *",
                [
                    "Business & Productivity",
                    "Technology & AI",
                    "Health & Wellness",
                    "Personal Finance",
                    "Marketing & Sales",
                    "Career & Education",
                    "Creative & Design",
                ],
                index=0,
            )

        with col2:
            mode_choice = st.radio(
                "Creator Expertise Mode *",
                options=[ExpertiseMode.NON_EXPERT.value, ExpertiseMode.EXPERT.value],
                format_func=lambda x: "🌱 Non-Expert Mode (Audience-problem-first; Accessible proven methods)"
                if x == ExpertiseMode.NON_EXPERT.value
                else "🎓 Expert Mode (Leverage existing creator expertise/audience)",
                index=0,
            )
            product_format = st.selectbox(
                "Product Format *",
                [f.value for f in ProductFormat],
                index=0,
            )
            budget = st.number_input("Optional Project Budget ($)", min_value=0.0, value=0.0, step=50.0)
            interests = st.text_area(
                "Creator Interests / Background Notes",
                placeholder="List any topics, hobbies, or domains you are enthusiastic about investigating...",
                height=100,
            )

        st.markdown("#### 📏 Manuscript Length Settings (this project only)")
        st.caption(
            "These settings are stored per project. Changing them later never affects "
            "another project's targets or completeness report."
        )
        m1, m2, m3 = st.columns(3)
        with m1:
            required_chapters = st.number_input(
                "Required chapters",
                min_value=1,
                max_value=40,
                value=int(DEFAULT_REQUIRED_CHAPTER_COUNT),
                step=1,
            )
        with m2:
            target_chapter_words = st.number_input(
                "Target words per chapter",
                min_value=200,
                max_value=10000,
                value=int(DEFAULT_CHAPTER_TARGET_WORDS),
                step=100,
            )
        with m3:
            min_chapter_words = st.number_input(
                "Minimum acceptable words",
                min_value=100,
                max_value=10000,
                value=int(DEFAULT_MIN_CHAPTER_WORDS),
                step=50,
            )

        submit = st.form_submit_button("Create Project & Launch Factory", use_container_width=True)

        if submit:
            if not project_name.strip():
                st.error("Project Name is mandatory.")
                return
            if int(min_chapter_words) > int(target_chapter_words):
                st.error("Minimum acceptable words cannot exceed the target words per chapter.")
                return

            p_create = ProjectCreate(
                name=project_name.strip(),
                country=country,
                language=language,
                target_customer=target_customer.strip() if target_customer else None,
                expertise_mode=ExpertiseMode(mode_choice),
                product_category=category,
                product_format=ProductFormat(product_format),
                budget=budget,
                creator_interests=interests.strip() if interests else None,
                required_chapter_count=int(required_chapters),
                target_chapter_words=int(target_chapter_words),
                min_chapter_words=int(min_chapter_words),
            )

            new_id = ProjectService.create_project(p_create)
            st.session_state["active_project_id"] = new_id
            st.success(f"Project '{project_name}' created successfully (ID: {new_id})!")
            st.info("Directing you to Market Intelligence...")
            st.session_state["current_page"] = "Market Intelligence"
            st.rerun()

