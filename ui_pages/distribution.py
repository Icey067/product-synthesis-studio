import streamlit as st
from services.project_service import ProjectService
from services.charter_service import CharterService
from services.outline_service import OutlineService
from services.sales_service import SalesService

def render_distribution():
    st.markdown("## 📢 Sales Offer & Launch Distribution")
    st.markdown("Craft high-converting, ethical sales copy, launch announcements, and distribution checklists.")

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    charter = CharterService.get_charter(project_id)
    if not charter:
        st.warning("Please generate a Product Charter first.")
        return

    chapters = OutlineService.get_chapters(project_id)

    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("🚀 Generate Sales & Launch Package", use_container_width=True):
            with st.spinner("Writing ethical sales copy, teasers, and launch plan..."):
                s_service = SalesService()
                ok, data, msg = s_service.generate_sales_package(project_id, charter, chapters)
                if ok:
                    st.success("Sales package generated!")
                    st.rerun()
                else:
                    st.error(msg)
    with c2:
        st.caption("Never manufactures fake reviews or deceptive guarantees.")

    sales = SalesService.get_sales_page(project_id)
    if not sales:
        st.info("No sales package generated yet. Click 'Generate Sales & Launch Package' above.")
        return

    st.markdown("---")
    st.markdown("### 🎯 Sales Page Core Copy")
    st.markdown(f"#### {sales.get('headline', '')}")
    st.markdown(f"*{sales.get('subheadline', '')}*")
    st.write(sales.get("problem_statement", ""))
    st.write(sales.get("solution_introduction", ""))

    c_b1, c_b2 = st.columns(2)
    with c_b1:
        st.markdown("##### Core Pain Points Solved")
        for p in sales.get("pain_points", []):
            st.markdown(f"• {p}")

        st.markdown("##### What's Included in the Box")
        for inc in sales.get("whats_included", []):
            st.markdown(f"✓ {inc}")

    with c_b2:
        st.markdown("##### Core Benefits")
        for b in sales.get("core_benefits", []):
            st.markdown(f"★ {b}")

        st.markdown("##### Ethical Refund Policy")
        st.info(sales.get("ethical_refund_policy", ""))

    st.markdown("### ❓ Frequently Asked Questions")
    for faq in sales.get("faqs", []):
        with st.expander(faq.get("question", "Question")):
            st.write(faq.get("answer", ""))

    st.markdown("### 📣 Marketing & Launch Assets")
    tab_post, tab_email, tab_market = st.tabs(["Social Posts", "Email Announcement", "Marketplace Description"])
    with tab_post:
        for idx, post in enumerate(sales.get("promotional_social_posts", [])):
            st.text_area(f"Promotional Post {idx+1}", value=post, height=80)
    with tab_email:
        st.text_area("Launch Email Broadcast", value=sales.get("email_announcement_draft", ""), height=150)
    with tab_market:
        st.text_area("Marketplace Short Blurb", value=sales.get("marketplace_short_description", ""), height=100)

