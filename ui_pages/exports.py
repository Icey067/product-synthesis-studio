import os
import streamlit as st
from services.project_service import ProjectService
from services.charter_service import CharterService
from services.outline_service import OutlineService
from services.research_service import ResearchService
from services.export_service import ExportService
from services.manuscript_service import ManuscriptService

def render_exports():
    st.markdown("## 📦 Professional Document & Product Exporter")
    st.markdown("Compile validated chapters into a publication-ready PDF or DOCX — or export a clearly labelled draft.")

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    charter = CharterService.get_charter(project_id)
    if not charter:
        st.warning("Please generate a Product Charter first.")
        return

    chapters = OutlineService.get_chapters(project_id)
    sources = ResearchService.get_sources(project_id)

    written_chapters = [c for c in chapters if c.get("content") and len(c.get("content", "").strip()) > 30]
    assessment = ManuscriptService.assess_project(project_id)
    counts = assessment["counts"]

    st.markdown(
        f"""
        <div class="pss-card">
            <b>Ready to Compile:</b> "{charter.get('product_title')}"<br/>
            <b>Required chapters:</b> {counts['required_chapters']} |
            <b>Outlined:</b> {counts['existing_chapters']} |
            <b>Written:</b> {counts['written_chapters']} |
            <b>At minimum length:</b> {counts['min_word_compliant_chapters']} |
            <b>Audit-approved:</b> {counts['audit_approved_chapters']} |
            <b>Human review:</b> {counts['human_review_required_chapters']} |
            <b>Publication-ready:</b> {counts['publication_ready_chapters']} |
            <b>Empirical sources:</b> {len(sources)}<br/>
            <b>Manuscript Status:</b> {assessment['label']}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---- Publication completeness gate ----
    if assessment["can_publish"]:
        st.success(
            f"✅ Publication ready: {assessment['publication_ready_count']}/"
            f"{assessment['required_chapters']} required chapters complete, approved, cited and correctly ordered."
        )
    else:
        st.warning(f"⚠️ {assessment['label']}")
        if assessment["blocking_reasons"]:
            with st.expander("Why full publication is blocked", expanded=True):
                for reason in assessment["blocking_reasons"]:
                    st.markdown(f"• {reason}")
        st.caption(
            "A full-book export will be blocked unless you explicitly export a clearly marked partial draft."
        )

    if not written_chapters:
        st.info("No chapters with written content yet. Write or generate at least one chapter in the Product Builder.")
        return

    allow_partial = st.checkbox(
        "Export a clearly marked PARTIAL DRAFT (not for publication)",
        value=not assessment["can_publish"],
        help="When full-publication requirements are unmet, this produces a watermarked draft that cannot be mistaken for a finished book.",
    )

    # Export Scope Selection
    st.markdown("### 🎯 Select Export Scope")
    scope_options = ["Full Publication (all chapters)"] + [
        f"Chapter {c['chapter_number']}: {c['title']} ({c.get('word_count', 0)} words)" for c in written_chapters
    ]
    selected_scope = st.selectbox("Export Target", options=scope_options, index=0)

    target_chap_num = None
    if selected_scope != "Full Publication (all chapters)":
        target_chap_num = int(selected_scope.split(":")[0].replace("Chapter", "").strip())

    require_complete = target_chap_num is None  # full-book export is gated; chapter exports are not

    st.markdown("---")
    c1, c2 = st.columns(2)

    with c1:
        st.markdown("### 📄 Export Publication PDF")
        st.caption("A4, Unicode fonts (₹ and accented text), customer-branded headers/footers, Markdown tables and checkboxes.")
        if st.button("Generate & Download PDF", use_container_width=True, key="btn_pdf_gen"):
            with st.spinner("Compiling ReportLab PDF..."):
                ok, path, msg = ExportService.generate_pdf(
                    project_id=project_id,
                    charter=charter,
                    chapters=chapters,
                    sources=sources,
                    chapter_number=target_chap_num,
                    require_complete=require_complete,
                    allow_partial=allow_partial,
                    assessment=assessment,
                )
                if ok and os.path.exists(path):
                    st.success(msg)
                    with open(path, "rb") as f:
                        st.download_button(
                            label=f"⬇️ Download {'Single Chapter' if target_chap_num else 'Full Book'} PDF",
                            data=f.read(),
                            file_name=os.path.basename(path),
                            mime="application/pdf",
                            use_container_width=True,
                            key="dl_pdf"
                        )
                else:
                    st.error(msg)

    with c2:
        st.markdown("### 📝 Export DOCX")
        st.caption("Editable Word document with heading styles, bullet lists, tables and source references.")
        if st.button("Generate & Download DOCX", use_container_width=True, key="btn_docx_gen"):
            with st.spinner("Compiling python-docx Word document..."):
                ok, path, msg = ExportService.generate_docx(
                    project_id=project_id,
                    charter=charter,
                    chapters=chapters,
                    sources=sources,
                    chapter_number=target_chap_num,
                    require_complete=require_complete,
                    allow_partial=allow_partial,
                    assessment=assessment,
                )
                if ok and os.path.exists(path):
                    st.success(msg)
                    with open(path, "rb") as f:
                        st.download_button(
                            label=f"⬇️ Download {'Single Chapter' if target_chap_num else 'Full Book'} DOCX",
                            data=f.read(),
                            file_name=os.path.basename(path),
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            use_container_width=True,
                            key="dl_docx"
                        )
                else:
                    st.error(msg)
