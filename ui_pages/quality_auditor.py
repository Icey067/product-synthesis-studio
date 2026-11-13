import streamlit as st
from services.project_service import ProjectService
from services.outline_service import OutlineService
from services.charter_service import CharterService
from services.audit_service import AuditService
from services.editorial_service import EditorialService
from services.manuscript_service import ManuscriptService
from services.claim_service import ClaimService
from services.repetition_service import RepetitionService
from services.book_architecture_service import BookArchitectureService

def render_quality_auditor():
    st.markdown("## 🛡️ Independent Quality & Factual Auditor")
    st.markdown(
        "Deterministic verification of citations, statistics, structure, length and format — "
        "plus a limited model-assisted editorial assessment. The model cannot self-certify facts."
    )

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    charter = CharterService.get_charter(project_id)
    chapters = OutlineService.get_chapters(project_id)

    if not chapters:
        st.info("No chapters available to audit.")
        return

    # Canonical, project-scoped manuscript counts (same service as every screen).
    assessment = ManuscriptService.assess_project(project_id)
    counts = assessment["counts"]
    st.markdown(
        f"""
        <div class="pss-card">
            <b>Canonical manuscript status:</b> {assessment['label']}<br/>
            <b>Required:</b> {counts['required_chapters']} |
            <b>Outlined:</b> {counts['existing_chapters']} |
            <b>Written:</b> {counts['written_chapters']} |
            <b>At minimum length:</b> {counts['min_word_compliant_chapters']} |
            <b>Audit-approved:</b> {counts['audit_approved_chapters']} |
            <b>Human review required:</b> {counts['human_review_required_chapters']} |
            <b>Publication-ready:</b> {counts['publication_ready_chapters']}
        </div>
        """,
        unsafe_allow_html=True,
    )
    if assessment["blocking_reasons"]:
        with st.expander("Publication blockers (canonical)", expanded=False):
            for reason in assessment["blocking_reasons"]:
                st.markdown(f"• {reason}")

    chap_options = [
        f"Chapter {c['chapter_number']}: {c['title']} ({c.get('word_count', 0)} words)"
        for c in chapters
    ]
    sel_idx = st.selectbox("Select Chapter to Audit", range(len(chap_options)), format_func=lambda x: chap_options[x])
    selected_chapter = chapters[sel_idx]

    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("🔍 Execute Quality Audit", use_container_width=True):
            with st.spinner("Running deterministic verification and model-assisted review..."):
                auditor = AuditService()
                ok, rep, msg = auditor.audit_chapter(project_id, selected_chapter, charter or {})
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
    with c2:
        st.caption(
            "Deterministic checks (authoritative): source existence, statistic support, required "
            "sections, target length, citations, format compliance, repetition. Model review is advisory only."
        )

    # Show latest audit report
    report = AuditService.get_latest_audit(selected_chapter["id"])
    if not report:
        st.info("No audit report recorded for this chapter yet. Click 'Execute Quality Audit'.")
        return

    st.markdown("---")
    score = report.get("score", 0)
    status = report.get("status", "NEEDS_IMPROVEMENT")
    human_review = report.get("human_review_required", False)
    checks = report.get("deterministic_checks", {}) or {}
    legacy_unverified = not (isinstance(checks, dict) and checks.get("counts"))
    if legacy_unverified:
        status = "UNVERIFIED — LEGACY MODEL-ONLY REPORT"

    badge_class = "pss-badge-high" if status == "PASS" else ("pss-badge-medium" if status == "NEEDS_IMPROVEMENT" else "pss-badge-low")

    st.markdown(
        f"""
        <div class="pss-card">
            <div class="pss-card-header">
                <span>Audit Verdict: {status}</span>
                <span class="pss-badge {badge_class}">{'REPORTED' if legacy_unverified else 'DETERMINISTIC'} SCORE: {score}/100</span>
            </div>
            <b>Summary:</b> {report.get('summary_verdict', '')}<br/>
            <b>Human review required:</b> {'YES' if human_review else 'No'}
        </div>
        """,
        unsafe_allow_html=True,
    )
    if human_review:
        st.error(
            "🖐️ This chapter **requires human verification** before it can be treated as publication-ready. "
            "The auditor found critical problems or flagged possible fabrication."
        )
    if legacy_unverified:
        st.warning(
            "⚠️ This report predates the independent auditor and contains **no deterministic checks**. "
            "It is treated as unverified and cannot approve a chapter. Click 'Execute Quality Audit' to "
            "run the authoritative checks."
        )

    if checks:
        st.markdown("##### ✅ Deterministic Checks (authoritative)")
        check_labels = {
            "citations_exist": "All cited source IDs exist",
            "citations_present": "Chapter cites sources",
            "required_sections": "All required sections present",
            "word_target": "Meets minimum word target",
            "format_compliant": "Matches selected product format",
            "statistics_supported": "All statistics supported by a source",
            "no_repetition": "No repetitive passages",
        }
        cols = st.columns(2)
        for idx, (key, label) in enumerate(check_labels.items()):
            value = checks.get(key)
            if value is None:
                continue
            marker = "✅" if value else "❌"
            cols[idx % 2].markdown(f"{marker} {label}")

    issues = report.get("issues", [])
    if issues:
        st.markdown("##### 🚩 Detailed Verification Issues")
        for issue in issues:
            severity = str(issue.get("severity", "MEDIUM")).upper()
            prefix = "🟥" if severity == "CRITICAL" else ("🟧" if severity == "HIGH" else "🟨")
            st.markdown(
                f"{prefix} **[{severity}] {issue.get('check', '')}** — {issue.get('message', '')}\n\n"
                f"&nbsp;&nbsp;&nbsp;*Correction:* {issue.get('correction', '') or 'N/A'}"
            )
            if issue.get("claim"):
                st.caption(f"Claim: {issue['claim'][:300]}")

    st.markdown("##### 🔎 Claim Verification Report")
    claims = ClaimService.list_claims(selected_chapter["id"])
    if claims:
        for claim in claims:
            title = (
                f"[{claim['severity']}] {claim['verification_status']} — "
                f"{claim['claim_text'][:110]}"
            )
            with st.expander(title, expanded=False):
                st.write(claim["claim_text"])
                st.caption(
                    f"Type: {claim['claim_type']} · Section: {claim.get('section_key') or 'body'} · "
                    f"Human review: {claim['human_review_status']}"
                )
                for evidence in claim.get("excerpts") or []:
                    st.markdown(
                        f"**[Source-{evidence.get('source_id')}] {evidence.get('title') or 'Source'}**"
                    )
                    st.code(evidence.get("excerpt") or "No matching excerpt stored.")
                st.caption(claim.get("review_note") or "No verification note recorded.")
                if claim.get("human_review_status") in {"REQUIRED", "PENDING"}:
                    h1, h2 = st.columns(2)
                    if h1.button("Mark human-verified", key=f"claim_ok_{claim['id']}", use_container_width=True):
                        ClaimService.set_human_review(
                            claim["id"], "APPROVED", "Explicitly verified by a human reviewer in the Quality Auditor."
                        )
                        st.rerun()
                    if h2.button("Reject claim", key=f"claim_reject_{claim['id']}", use_container_width=True):
                        ClaimService.set_human_review(
                            claim["id"], "REJECTED", "Rejected by a human reviewer in the Quality Auditor."
                        )
                        st.rerun()
    else:
        st.info("No persisted candidate claims yet. Run the audit to extract and verify claims.")

    st.markdown("##### ♻️ Repetition and Chapter Coverage")
    repetition = RepetitionService.chapter_summary(selected_chapter["id"])
    if repetition.get("finding_count"):
        for finding in repetition.get("findings", []):
            st.warning(
                f"{finding.get('match_label', finding.get('match_type'))} "
                f"({finding.get('similarity', 0):.0%}): {finding.get('recommendation', '')}"
            )
            st.caption(f"Earlier: {finding.get('original_text', '')[:280]}")
            st.caption(f"This chapter: {finding.get('duplicate_text', '')[:280]}")
    else:
        st.success("No repetition findings are attached to this chapter's latest manuscript scan.")
    architecture = BookArchitectureService.get(project_id) or BookArchitectureService.build(project_id)
    overlaps = [
        item for item in architecture.get("overlaps", [])
        if selected_chapter["chapter_number"] in {item.get("chapter"), item.get("overlaps_with")}
    ]
    if overlaps:
        for overlap in overlaps:
            st.warning(
                f"Chapter-purpose overlap with chapter {overlap.get('overlaps_with')}: {overlap.get('reason')}"
            )

    st.markdown("##### 📊 Editorial Rubric")
    rubric = report.get("editorial_rubric") or {}
    if rubric:
        from services.editorial_service import RUBRIC_DIMENSIONS

        st.dataframe(
            [
                {
                    "Dimension": RUBRIC_DIMENSIONS.get(key, key),
                    "Deterministic score (authoritative)": entry.get("score"),
                    "Advisory score (not authoritative)": entry.get("advisory_score", "—"),
                }
                for key, entry in rubric.items()
            ],
            use_container_width=True,
            hide_index=True,
        )
        if report.get("editorial_advisory_available"):
            st.caption(
                "Model scores are advisory. Deterministic checks remain authoritative for "
                "citations, statistics, structure and length."
            )
        else:
            st.caption("Model advisory scores were unavailable; deterministic proxies are shown.")
    else:
        st.info("No editorial rubric recorded yet. Run the quality audit to generate one.")

    chapter_id = selected_chapter["id"]
    recommendations = EditorialService.list_recommendations(project_id, chapter_id)
    if recommendations:
        st.markdown("##### 📝 Recommendations for Human Review")
        for rec in recommendations:
            source_label = "DETERMINISTIC" if rec.get("source") == "DETERMINISTIC" else "MODEL ADVISORY"
            st.markdown(
                f"**[{rec.get('severity')}] {rec.get('category')}** ({source_label}, "
                f"status: `{rec.get('status')}`) — {rec.get('message')}"
            )
            if rec.get("status") == "PENDING":
                a, b, c = st.columns(3)
                if a.button("Approve", key=f"rec_approve_{rec['id']}", use_container_width=True):
                    EditorialService.set_recommendation_status(rec["id"], "APPROVED")
                    st.rerun()
                if b.button("Mark applied", key=f"rec_apply_{rec['id']}", use_container_width=True):
                    EditorialService.set_recommendation_status(rec["id"], "APPLIED")
                    st.rerun()
                if c.button("Dismiss", key=f"rec_dismiss_{rec['id']}", use_container_width=True):
                    EditorialService.set_recommendation_status(rec["id"], "DISMISSED")
                    st.rerun()
    else:
        st.caption("No editorial recommendations recorded for this chapter.")

    r1, r2 = st.columns(2)
    with r1:
        st.markdown("##### 🔬 Factual Grounding")
        st.write(report.get("factual_accuracy_assessment", "N/A"))

        st.markdown("##### ⚠️ Unsupported Claims Detected")
        unsupported = report.get("unsupported_statements", [])
        if unsupported:
            for u in unsupported:
                st.warning(f"• {u}")
        else:
            st.success("No unsupported claims detected by deterministic checks.")

        st.markdown("##### ⚖️ Disclaimer & Compliance Flags")
        disc_flags = report.get("disclaimer_flags", [])
        if disc_flags:
            for df in disc_flags:
                st.error(f"• {df}")
        else:
            st.info("No compliance flags raised by the model review.")

    with r2:
        st.markdown("##### 📖 Readability & Practical Value (model-assisted)")
        st.markdown(f"**Readability Assessment:** {report.get('readability_score', 'N/A')}")
        st.write(report.get("practical_value_assessment", "N/A"))

        st.markdown("##### 💡 Recommended Corrections")
        recs = report.get("recommended_corrections", [])
        if recs:
            for r in recs:
                st.markdown(f"• {r}")
        else:
            st.write("No corrections required.")
