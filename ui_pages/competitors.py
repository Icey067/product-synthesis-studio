import streamlit as st
from services.project_service import ProjectService
from services.competitor_service import CompetitorService

def render_competitor_research():
    st.markdown("## 🤺 Competitor Intelligence & Gap Analysis")
    st.markdown("Record and analyze competing marketplace products to isolate underserved customer gaps.")

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    with st.expander("➕ Add Competitor Product Record", expanded=False):
        with st.form("add_comp_form"):
            c1, c2 = st.columns(2)
            with c1:
                p_name = st.text_input("Product Title *", placeholder="e.g., Local Marketing Masterclass")
                brand = st.text_input("Brand / Author", placeholder="e.g., Agency X")
                website = st.text_input("Website / Listing URL", placeholder="https://...")
                price = st.text_input("Observed Price", placeholder="e.g., $47")
            with c2:
                format_type = st.selectbox("Product Format", ["Ebook", "Video Course", "Template", "Coaching"], index=0)
                strengths = st.text_area("What they do well (Strengths)", height=80)
                weaknesses = st.text_area("What customers complain about / Gaps", height=80)

            features = st.text_area("Key Features Included", height=70)
            reviews = st.text_area("Representative Customer Reviews / Feedback", height=70)

            submit = st.form_submit_button("Save Competitor Record")
            if submit:
                if not p_name.strip():
                    st.error("Product Title is required.")
                else:
                    CompetitorService.add_competitor(
                        project_id=project_id,
                        product_name=p_name.strip(),
                        brand=brand.strip(),
                        website=website.strip(),
                        product_format=format_type,
                        observed_price=price.strip(),
                        main_features=features.strip(),
                        customer_reviews=reviews.strip(),
                        customer_complaints=weaknesses.strip(),
                        strengths=strengths.strip(),
                        weaknesses=weaknesses.strip(),
                    )
                    st.success(f"Added competitor '{p_name}'!")
                    st.rerun()

    comps = CompetitorService.get_competitors(project_id)
    if not comps:
        st.info("No competitor records added yet. Add known competitor products to generate differentiation strategies.")
        return

    st.markdown("### 📊 Competitor Comparison Table")
    for comp in comps:
        with st.expander(f"{comp['product_name']} by {comp.get('brand') or 'Unknown'} — {comp.get('observed_price') or 'Price Unknown'}"):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**Format:** {comp['product_format']}")
                if comp.get("website"):
                    st.markdown(f"**Website:** [{comp['website']}]({comp['website']})")
                st.markdown(f"**Key Features:**\n{comp.get('main_features') or 'Not recorded'}")
                st.markdown(f"**Customer Reviews:**\n{comp.get('customer_reviews') or 'Not recorded'}")
            with c2:
                st.markdown(f"**Strengths:**\n{comp.get('strengths') or 'None noted'}")
                st.markdown(f"**Weaknesses & Gaps:**\n{comp.get('weaknesses') or 'None noted'}")
                if st.button("Delete Competitor", key=f"del_comp_{comp['id']}"):
                    CompetitorService.delete_competitor(comp["id"])
                    st.rerun()

