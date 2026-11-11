import streamlit as st
from services.project_service import ProjectService
from services.research_service import ResearchService
from services.synthesis_service import SynthesisService
from services.source_trust_service import SourceTrustService
from utils.file_handler import extract_text_from_file
from utils.url_handler import fetch_url_content
from models.enums import ReliabilityCategory

RELIABILITY_OPTIONS = [r.value for r in ReliabilityCategory]
UNASSESSED_INDEX = RELIABILITY_OPTIONS.index(ReliabilityCategory.UNASSESSED.value)


def _reliability_selectbox(label: str, key: str) -> str:
    st.caption(
        "Reliability here is only your **declaration**. Credibility is assessed "
        "independently from publisher, provenance, methodology and content signals."
    )
    return st.selectbox(
        label,
        RELIABILITY_OPTIONS,
        index=UNASSESSED_INDEX,
        key=key,
        help=(
            "UNASSESSED: let the app assess it. HIGH/MEDIUM/LOW here are self-reported "
            "and never create credibility or confidence on their own."
        ),
    )

def render_research_library():
    st.markdown("## 📚 Deep Research Library & Evidence Engine")
    st.markdown("Collect, verify, and catalog empirical research sources without hallucination.")

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    # Add Source Tabs
    tab_upload, tab_url, tab_manual, tab_synth = st.tabs([
        "📄 Upload Document (PDF/TXT/MD/CSV)",
        "🌐 Fetch Public URL",
        "✍️ Manual Research Notes",
        "🔬 Evidence Synthesis",
    ])

    with tab_upload:
        st.markdown("##### Upload Research Document")
        uploaded_file = st.file_uploader("Upload PDF, TXT, Markdown or CSV (Max 25MB)", type=["pdf", "txt", "md", "csv"])
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            doc_publisher = st.text_input("Publisher / Organization", placeholder="e.g., McKinsey & Co, Census Bureau")
            doc_author = st.text_input("Author(s)", placeholder="e.g., Jane Doe")
        with col_m2:
            doc_reliability = _reliability_selectbox(
                "Declared Source Reliability (self-reported)", "doc_rel"
            )
            doc_date = st.text_input("Publication Date / Year", placeholder="e.g., 2024")

        if st.button("Extract and Ingest Document", disabled=uploaded_file is None):
            with st.spinner("Extracting and hashing content..."):
                file_bytes = uploaded_file.read()
                ok, extracted_text, err = extract_text_from_file(uploaded_file.name, file_bytes)
                if not ok:
                    st.error(err)
                else:
                    success, src_id, save_msg = ResearchService.add_source(
                        project_id=project_id,
                        title=uploaded_file.name,
                        source_category="FILE_" + uploaded_file.name.split(".")[-1].upper(),
                        content_text=extracted_text,
                        publisher=doc_publisher.strip() or "Unknown",
                        author=doc_author.strip() or "Unknown",
                        publication_date=doc_date.strip() or "Unknown",
                        reliability_assessment=doc_reliability,
                    )
                    if success:
                        st.success(f"Source ingested successfully (Source ID: [Source-{src_id}])!")
                        st.rerun()
                    else:
                        st.warning(save_msg)

    with tab_url:
        st.markdown("##### Safe Public Webpage Ingestion")
        st.caption("Protected with SSRF guards. Blocks internal networks, localhosts, and scripts.")
        url_input = st.text_input("Public Webpage URL (HTTP/HTTPS)", placeholder="https://example.com/research-report")
        url_reliability = _reliability_selectbox(
            "Declared Reliability (self-reported)", "url_rel"
        )

        if st.button("Fetch and Extract Webpage", disabled=not url_input):
            with st.spinner("Safely fetching webpage and stripping unsafe elements..."):
                ok, content, page_title = fetch_url_content(url_input)
                if not ok:
                    st.error(content)
                else:
                    success, src_id, save_msg = ResearchService.add_source(
                        project_id=project_id,
                        title=page_title or url_input,
                        source_category="PUBLIC_URL",
                        content_text=content,
                        url=url_input,
                        reliability_assessment=url_reliability,
                    )
                    if success:
                        st.success(f"Webpage extracted and saved as [Source-{src_id}]!")
                        st.rerun()
                    else:
                        st.warning(save_msg)

    with tab_manual:
        st.markdown("##### Add Observation or Field Research")
        manual_title = st.text_input("Observation / Note Title", placeholder="e.g., Survey of 25 local shop owners")
        manual_content = st.text_area("Detailed Findings / Observations", height=150)
        manual_rel = _reliability_selectbox(
            "Declared Reliability (self-reported)", "man_rel"
        )

        if st.button("Save Manual Research Note", disabled=not manual_content.strip()):
            success, src_id, save_msg = ResearchService.add_source(
                project_id=project_id,
                title=manual_title.strip() or "Manual Field Observation",
                source_category="MANUAL_NOTE",
                content_text=manual_content.strip(),
                reliability_assessment=manual_rel,
            )
            if success:
                st.success(f"Observation saved as [Source-{src_id}]!")
                st.rerun()
            else:
                st.warning(save_msg)

    with tab_synth:
        st.markdown("##### Multi-Source Evidence Synthesis")
        st.caption("Combines multiple sources into unified consensus findings, conflicts, and actionable methods.")
        sources = ResearchService.get_sources(project_id)
        if st.button("Run Evidence Synthesis Report", disabled=len(sources) == 0):
            with st.spinner("Synthesizing citations and detecting patterns..."):
                synth = SynthesisService()
                success, rep, msg = synth.synthesize_research(sources)
                if success:
                    st.success(msg or "Synthesis report generated!")
                    if rep.get("batch_count", 1) > 1:
                        st.info(
                            f"Consolidated across {rep.get('batch_count')} bounded batches "
                            f"({len(sources)} sources) — no prompt contained the entire library."
                        )
                    st.json(rep)
                else:
                    st.error(msg)

    st.markdown("---")
    st.markdown("### 📋 Cataloged Project Sources")
    sources = ResearchService.get_sources(project_id)
    if not sources:
        st.info("No sources cataloged in this project yet.")
        return

    if st.button("🔎 Re-assess All Sources Independently", use_container_width=True):
        count = SourceTrustService.assess_project_sources(project_id)
        st.success(f"Independently re-assessed {count} source(s).")
        st.rerun()

    for s in sources:
        declared = s.get("declared_reliability") or "UNASSESSED"
        assessed = s.get("assessed_reliability") or "UNASSESSED"
        assessment_score = s.get("assessment_score") or 0
        with st.expander(
            f"[Source-{s['id']}] {s['title']} ({s['source_category']}) — "
            f"Assessed: {assessed} | Declared: {declared}"
        ):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"**Publisher:** {s['publisher']} | **Author:** {s['author']} | **Date:** {s['publication_date']}")
                if s.get("url"):
                    st.markdown(f"**URL:** [{s['url']}]({s['url']})")
                st.markdown(
                    f"**Declared reliability (self-reported):** `{declared}` — "
                    "recorded for transparency, never used to establish credibility."
                )
                st.markdown(
                    f"**Independently assessed reliability:** `{assessed}` "
                    f"(assessment score {assessment_score}/100)"
                )
                if s.get("reliability_rationale"):
                    st.caption(s["reliability_rationale"])
                st.markdown(f"**Content Preview:**\n> {s['raw_content'][:400]}...")
            with c2:
                st.markdown(f"**Hash:** `{s['content_hash'][:12]}...`")
                if st.button("Re-assess Source", key=f"assess_src_{s['id']}", use_container_width=True):
                    SourceTrustService.assess_and_store(s["id"])
                    st.rerun()
                if st.button("Delete Source", key=f"del_src_{s['id']}", use_container_width=True):
                    ResearchService.delete_source(s["id"])
                    st.rerun()

