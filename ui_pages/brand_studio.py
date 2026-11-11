import streamlit as st
from services.project_service import ProjectService
from services.charter_service import CharterService
from services.branding_service import BrandingService

def render_brand_studio():
    st.markdown("## 🎨 Brand Studio & Cover Designer")
    st.markdown("Generate original brand identities, palettes, typography pairings, and cover concepts.")

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    charter = CharterService.get_charter(project_id)
    if not charter:
        st.warning("Please create a Product Charter first.")
        return

    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("✨ Generate Brand Identity Suite", use_container_width=True):
            with st.spinner("Generating distinctive palettes, title alternatives, and design specs..."):
                b_service = BrandingService()
                ok, data, msg = b_service.generate_branding(
                    project_id=project_id,
                    product_title=charter.get("product_title", "Digital Product"),
                    target_audience=charter.get("target_audience", "General"),
                    category=charter.get("product_category", "Business"),
                )
                if ok:
                    st.success("Brand Identity generated!")
                    st.rerun()
                else:
                    st.error(msg)
    with c2:
        st.caption("Creates cohesive visual identities avoiding generic or copied designs.")

    brand = BrandingService.get_brand(project_id)
    if not brand:
        st.info("No branding identity generated yet. Click 'Generate Brand Identity Suite' above.")
        return

    st.markdown("---")
    st.markdown("### 🏷️ Brand Story & Cover Concept")
    st.markdown(f"**Core Brand Story:**\n> {brand.get('brand_story', '')}")
    st.markdown(f"**Visual Cover Concept:**\n{brand.get('cover_concept', '')}")
    st.markdown(f"**Interior Design Guidelines:**\n{brand.get('interior_guidelines', '')}")

    st.markdown("### 🎨 Curated Color Palettes")
    palettes = brand.get("color_palettes", [])
    if palettes:
        cols = st.columns(len(palettes))
        for idx, p in enumerate(palettes):
            with cols[idx]:
                st.markdown(f"**{p.get('palette_name', 'Palette')}**")
                st.markdown(
                    f"""
                    <div style="background-color: {p.get('primary','#0F172A')}; height: 40px; border-radius: 6px; margin-bottom: 4px; display:flex; align-items:center; justify-content:center; color:white; font-size:11px;">Primary: {p.get('primary')}</div>
                    <div style="background-color: {p.get('secondary','#2563EB')}; height: 30px; border-radius: 6px; margin-bottom: 4px; display:flex; align-items:center; justify-content:center; color:white; font-size:11px;">Secondary: {p.get('secondary')}</div>
                    <div style="background-color: {p.get('accent','#F59E0B')}; height: 25px; border-radius: 6px; margin-bottom: 4px; display:flex; align-items:center; justify-content:center; color:black; font-size:11px;">Accent: {p.get('accent')}</div>
                    """,
                    unsafe_allow_html=True,
                )

    st.markdown("### ✍️ Professional Typography Pairings")
    pairings = brand.get("font_pairings", [])
    for pair in pairings:
        st.markdown(f"• **{pair.get('heading_font', '')}** (Headings) + **{pair.get('body_font', '')}** (Body) — *{pair.get('style_desc', '')}*")

