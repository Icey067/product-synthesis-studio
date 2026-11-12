import streamlit as st
import time
from services.project_service import ProjectService
from services.charter_service import CharterService
from services.outline_service import OutlineService
from services.generation_service import GenerationService
from services.research_service import ResearchService
from services.transformation_service import TransformationService
from services.audit_service import AuditService
from services.citation_service import CitationService
from services.autosave_service import (
    AutosaveService,
    FAILED as AUTOSAVE_FAILED,
    SAVED as AUTOSAVE_SAVED,
)
from services.manuscript_service import ManuscriptService
from services.chapter_planning_service import ChapterPlanningService
from services.batch_service import BatchService
from services.retrieval_service import RetrievalService
from services.editorial_service import EditorialService
from services.chapter_contract import (
    analyze_chapter,
    count_words,
    target_words,
    min_publication_words,
)

def render_product_builder():
    st.markdown("## 📖 Product Builder & Chapter Studio")
    st.markdown("Architect outline structure, generate in-depth chapters, and manage autosaved revisions.")

    project_id = st.session_state.get("active_project_id")
    if not project_id:
        st.warning("Please select or create an active project first.")
        return

    charter = CharterService.get_charter(project_id)
    if not charter:
        st.warning("Please generate a Product Charter first to serve as the foundation for chapters.")
        return

    chapters = OutlineService.get_chapters(project_id)
    assessment = ManuscriptService.assess_project(project_id)
    counts = assessment["counts"]
    settings = ProjectService.get_chapter_settings(project_id)

    col_out1, col_out2 = st.columns([1, 2])
    with col_out1:
        if st.button("🏗️ Generate Niche-Specific Outline", use_container_width=True):
            with st.spinner("Designing a niche-specific chapter sequence from your charter, transformation map and research..."):
                t_map = TransformationService.get_map(project_id)
                o_service = OutlineService()
                ok, chaps, msg = o_service.generate_outline(project_id, charter, t_map)
                if ok:
                    st.success(f"Generated {len(chaps)} chapters!")
                    st.rerun()
                else:
                    st.error(msg)
    with col_out2:
        st.markdown(
            f"""
            <div class="pss-card">
                <b>Canonical manuscript status:</b> {assessment['label']}<br/>
                <b>Required:</b> {counts['required_chapters']} ·
                <b>Outlined:</b> {counts['existing_chapters']} ·
                <b>Written:</b> {counts['written_chapters']} ·
                <b>At minimum length:</b> {counts['min_word_compliant_chapters']} ·
                <b>Audit-approved:</b> {counts['audit_approved_chapters']} ·
                <b>Human review:</b> {counts['human_review_required_chapters']} ·
                <b>Publication-ready:</b> {counts['publication_ready_chapters']}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if counts["required_chapters"]:
            st.progress(
                min(1.0, counts["publication_ready_chapters"] / counts["required_chapters"])
            )

    with st.expander("📏 Manuscript settings for this project (isolated)", expanded=False):
        st.caption(
            "These values apply only to this project. Other projects keep their own "
            "target, minimum and required-chapter settings."
        )
        s1, s2, s3 = st.columns(3)
        with s1:
            new_target = st.number_input(
                "Target words per chapter",
                min_value=200,
                max_value=10000,
                step=100,
                value=int(settings["target_words"]),
                key=f"ms_target_{project_id}",
            )
        with s2:
            new_min = st.number_input(
                "Minimum acceptable words",
                min_value=100,
                max_value=10000,
                step=50,
                value=int(settings["min_words"]),
                key=f"ms_min_{project_id}",
            )
        with s3:
            new_required = st.number_input(
                "Required chapter count",
                min_value=1,
                max_value=40,
                step=1,
                value=int(settings["required_chapters"]),
                key=f"ms_required_{project_id}",
            )
        if st.button("Save manuscript settings", key=f"ms_save_{project_id}", use_container_width=True):
            try:
                saved = ProjectService.update_chapter_settings(
                    project_id,
                    target_words=int(new_target),
                    min_words=int(new_min),
                    required_chapters=int(new_required),
                )
                st.success(
                    f"Saved: target {saved['target_words']} words, minimum "
                    f"{saved['min_words']} words, {saved['required_chapters']} chapters required."
                )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    if chapters:
        with st.expander("🚀 Complete Book Batch Generation", expanded=True):
            st.caption(
                "Structured generation saves each section as it is written. Approved or "
                "locked chapters are skipped unless you explicitly confirm an overwrite."
            )
            recovered = BatchService.recover_interrupted(project_id)
            if recovered:
                st.info(
                    f"Recovered {len(recovered)} interrupted batch session(s). They are paused "
                    "and can be resumed without losing completed sections."
                )

            batch = BatchService()
            batch_progress = BatchService.progress(project_id)
            latest_session = batch_progress.get("session")
            if latest_session:
                st.caption(
                    f"Latest session #{latest_session['id']}: {latest_session['status']} · "
                    f"{latest_session.get('processed_chapters', 0)} processed · "
                    f"{latest_session.get('failed_chapters', 0)} failed"
                )

            b1, b2, b3 = st.columns(3)
            with b1:
                structured_mode = st.toggle(
                    "Section-by-section (recommended)",
                    value=True,
                    key=f"structured_mode_{project_id}",
                )
            with b2:
                confirm_overwrite = st.checkbox(
                    "Allow overwriting approved chapters",
                    value=False,
                    key=f"batch_overwrite_{project_id}",
                )
            with b3:
                st.metric(
                    "Research coverage",
                    f"{batch_progress['research_covered_chapters']}/{batch_progress['total_chapters']}",
                    help="Chapters with stored research source traceability.",
                )

            run_cb_progress = st.progress(0.0)
            run_cb_status = st.empty()

            def _batch_cb(done, total, chapter, message):
                run_cb_progress.progress(min(1.0, done / max(total, 1)))
                run_cb_status.write(
                    f"Chapter {chapter.get('chapter_number')}: {chapter.get('title')} — {message}"
                )

            btn1, btn2, btn3, btn4 = st.columns(4)
            with btn1:
                if st.button("▶️ Generate Next Chapter", use_container_width=True):
                    with st.spinner("Generating the next incomplete chapter..."):
                        outcome = batch.run(
                            project_id,
                            mode="NEXT",
                            structured=structured_mode,
                            force=confirm_overwrite,
                            max_chapters=1,
                            progress_cb=_batch_cb,
                        )
                    if outcome.get("ok"):
                        st.success(outcome.get("message"))
                        st.rerun()
                    else:
                        st.error(outcome.get("message"))
            with btn2:
                if st.button("📚 Generate All Remaining", use_container_width=True):
                    with st.spinner("Generating remaining chapters. Section drafts are saved after each section..."):
                        outcome = batch.run(
                            project_id,
                            mode="ALL_REMAINING",
                            structured=structured_mode,
                            force=confirm_overwrite,
                            progress_cb=_batch_cb,
                        )
                    if outcome.get("ok"):
                        st.success(outcome.get("message"))
                        st.rerun()
                    else:
                        st.error(outcome.get("message"))
            with btn3:
                if st.button("⏸️ Pause Session", use_container_width=True):
                    if latest_session:
                        batch.pause(int(latest_session["id"]))
                        st.success("Pause recorded. Generation stops between chapters.")
                    else:
                        st.info("There is no batch session to pause yet.")
            with btn4:
                if st.button("⏵️ Resume Session", use_container_width=True):
                    with st.spinner("Resuming the last batch session..."):
                        outcome = batch.resume(
                            project_id,
                            structured=structured_mode,
                            force=confirm_overwrite,
                            progress_cb=_batch_cb,
                        )
                    if outcome.get("ok"):
                        st.success(outcome.get("message"))
                        st.rerun()
                    else:
                        st.error(outcome.get("message"))

            retry_rows = [
                row
                for row in batch_progress["chapters"]
                if row["status"] not in ("COMPLETE",)
            ]
            if retry_rows:
                rc1, rc2 = st.columns([3, 1])
                with rc1:
                    retry_choice = st.selectbox(
                        "Retry a chapter that is incomplete or failed",
                        options=[
                            f"Chapter {row['chapter']}: {row['title']} "
                            f"({row['status']}, {row['words']} words)"
                            for row in retry_rows
                        ],
                        key=f"retry_chapter_choice_{project_id}",
                    )
                with rc2:
                    st.write("")
                    st.write("")
                    if st.button("🔁 Retry Chapter", use_container_width=True):
                        index = [
                            f"Chapter {row['chapter']}: {row['title']} "
                            f"({row['status']}, {row['words']} words)"
                            for row in retry_rows
                        ].index(retry_choice)
                        target_row = retry_rows[index]
                        with st.spinner(f"Retrying Chapter {target_row['chapter']}..."):
                            outcome = batch.retry_chapter(
                                project_id,
                                int(target_row["chapter_id"]),
                                structured=structured_mode,
                                force=confirm_overwrite,
                            )
                        if outcome.get("ok"):
                            st.success(outcome.get("message"))
                            st.rerun()
                        else:
                            st.error(outcome.get("message"))

            st.markdown("##### 📊 Batch progress and chapter word counts")
            st.dataframe(
                [
                    {
                        "Chapter": row["chapter"],
                        "Title": row["title"],
                        "Words": row["words"],
                        "Target": row["target_words"],
                        "Status": row["status"],
                        "Sections": f"{row['sections']['drafted']}/{row['sections']['total']}",
                        "Sources": row["source_count"],
                        "Locked": row["locked"],
                        "Approved": row["approved"],
                    }
                    for row in batch_progress["chapters"]
                ],
                use_container_width=True,
                hide_index=True,
            )

    if not chapters:
        st.info("No chapter outline generated yet. Click 'Generate 11-Chapter Outline' above.")
        return

    st.markdown("---")
    # Chapter Selector
    chap_options = [
        f"Chapter {c['chapter_number']}: {c['title']} ({count_words(c.get('content', ''))} words)"
        + (" 🔒" if c.get("is_locked") else "")
        for c in chapters
    ]
    selected_idx = st.selectbox(
        "Select Chapter to Read / Edit / Generate",
        range(len(chap_options)),
        format_func=lambda x: chap_options[x],
    )
    current_chap = chapters[selected_idx]

    locked = bool(current_chap.get("is_locked"))
    chapter_id = current_chap["id"]
    actual_words = count_words(current_chap.get("content", ""))
    target = int(settings["target_words"])
    minimum = int(settings["min_words"])
    length_ok = actual_words >= minimum
    validation = current_chap.get("validation") or {}
    v_status = current_chap.get("validation_status") or validation.get("status") or "PENDING"
    missing_sections = validation.get("missing_sections", []) if validation else []
    chapter_approved = ManuscriptService.chapter_is_approved(chapter_id)
    approve_regen = True
    if chapter_approved and not locked:
        approve_regen = st.checkbox(
            "I understand regenerating replaces a chapter that passed an independent audit.",
            key=f"regen_approved_{chapter_id}",
        )

    st.markdown(
        f"""
        <div class="pss-card">
            <div class="pss-card-header">
                <span>Chapter {current_chap['chapter_number']}: {current_chap['title']}</span>
                <span class="pss-badge {'pss-badge-high' if locked else 'pss-badge-medium'}">
                    {'🔒 LOCKED (Autosave Protected)' if locked else '🔓 EDITABLE'}
                </span>
            </div>
            <b>Pedagogical Purpose:</b> {current_chap.get('purpose', 'N/A')}<br/>
            <b>Key Topics:</b> {', '.join(current_chap.get('key_topics', []))}<br/>
            <b>Actual words:</b> {actual_words} / target {target} &nbsp;|&nbsp;
            <b>Minimum acceptable:</b> {minimum} &nbsp;|&nbsp;
            <b>Length:</b> {'✅ meets minimum' if length_ok else '❌ below minimum'}<br/>
            <b>Structure validation:</b> {v_status} &nbsp;|&nbsp;
            <b>Audit-approved:</b> {'✅ yes' if chapter_approved else 'no'}
        </div>
        """,
        unsafe_allow_html=True,
    )
    if v_status == "INCOMPLETE" and missing_sections:
        st.warning("Missing required sections: " + ", ".join(s.replace("_", " ") for s in missing_sections))

    # ------------------------------------------------------------------
    # Structured plan, section progress and evidence traceability
    # ------------------------------------------------------------------
    planner = ChapterPlanningService()
    plan = planner.get_plan(chapter_id)
    sections = planner.get_sections(chapter_id)
    section_progress = planner.section_progress(chapter_id)
    used_sources = RetrievalService.get_chapter_sources(chapter_id)

    with st.expander("🧩 Structured Generation, Section Budgets & Evidence", expanded=True):
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Planned words", section_progress["planned_words"] or "—")
        sc2.metric("Section words written", section_progress["actual_words"])
        sc3.metric("Sections drafted", f"{section_progress['drafted']}/{section_progress['total']}")
        if section_progress["total"]:
            st.progress(section_progress["percent"] / 100.0)

        p1, p2, p3 = st.columns(3)
        with p1:
            if st.button("🗺️ Build / Refresh Plan", key=f"plan_{chapter_id}", use_container_width=True):
                with st.spinner("Planning chapter sections and retrieving evidence..."):
                    planner.plan_chapter(project_id, current_chap, refresh=True)
                st.success("Chapter plan and section budgets created.")
                st.rerun()
        with p2:
            if st.button("➡️ Generate Next Section", key=f"next_section_{chapter_id}", use_container_width=True):
                if not sections:
                    st.warning("Build the chapter plan first.")
                else:
                    with st.spinner("Writing the next section..."):
                        result = planner.generate_next_section(project_id, chapter_id)
                    if result.get("ok"):
                        st.success(
                            f"Section '{result.get('section_key', '')}' drafted "
                            f"({result.get('word_count', 0)} words)."
                        )
                        st.rerun()
                    else:
                        st.error(result.get("error", "Section generation failed."))
        with p3:
            if st.button("📝 Generate Whole Chapter (structured)", key=f"structured_{chapter_id}", use_container_width=True):
                if chapter_approved and not approve_regen:
                    st.error(
                        "This chapter passed an independent audit. Confirm the overwrite "
                        "checkbox below before regenerating it."
                    )
                else:
                    with st.spinner("Planning, writing every section, assembling and validating..."):
                        result = planner.generate_chapter_structured(
                            project_id,
                            current_chap,
                            allow_approved_overwrite=chapter_approved and approve_regen,
                        )
                    if result.get("saved"):
                        st.success(result.get("message"))
                        st.rerun()
                    else:
                        st.warning(result.get("message"))
                        if result.get("consistency", {}).get("issues"):
                            st.caption(
                                f"Consistency flags: {len(result['consistency']['issues'])} "
                                "(see details below)."
                            )

        if plan:
            evidence = plan.get("evidence", {})
            st.caption(
                f"Retrieved evidence: {evidence.get('source_count', 0)} source(s), "
                f"{evidence.get('excerpt_count', 0)} excerpt(s). "
                f"Contradiction flags: {len(evidence.get('contradictions', []))} (advisory)."
            )
            for flag in evidence.get("contradictions", []):
                st.warning(flag.get("message", "Potential contradiction. Verify before citing."))
        else:
            st.info("No persisted plan yet. Build the plan to assign section word budgets.")

        if sections:
            st.dataframe(
                [
                    {
                        "Section": s["sequence"],
                        "Heading": s["heading"],
                        "Budget": s["word_budget"],
                        "Actual": s["word_count"],
                        "Status": s["status"],
                        "Model": s.get("model") or "—",
                        "Time ms": s.get("generation_time_ms") or "—",
                    }
                    for s in sections
                ],
                use_container_width=True,
                hide_index=True,
            )
            section_options = {s["heading"]: s["section_key"] for s in sections}
            chosen_heading = st.selectbox(
                "Regenerate a single section",
                options=list(section_options.keys()),
                key=f"regen_section_pick_{chapter_id}",
            )
            if st.button("♻️ Regenerate Selected Section", key=f"regen_section_{chapter_id}", use_container_width=True):
                with st.spinner(f"Regenerating '{chosen_heading}'..."):
                    result = planner.generate_section(
                        project_id,
                        chapter_id,
                        section_options[chosen_heading],
                        force=True,
                        allow_approved_overwrite=approve_regen,
                    )
                if result.get("ok"):
                    st.success(f"Section regenerated ({result.get('word_count', 0)} words).")
                    st.rerun()
                else:
                    st.error(result.get("error", "Section regeneration failed."))

        if used_sources:
            st.markdown("**Sources stored for this chapter**")
            st.dataframe(
                [
                    {
                        "Citation": row.get("citation_label"),
                        "Source": row.get("title"),
                        "Publisher": row.get("publisher"),
                        "Reliability": row.get("assessed_reliability"),
                        "Relevance": row.get("relevance_score"),
                        "Chunk": row.get("chunk_index"),
                    }
                    for row in used_sources
                ],
                use_container_width=True,
                hide_index=True,
            )
        elif not plan:
            st.caption("No research trace stored for this chapter yet.")

    # Actions row
    c_gen, c_lock, c_audit = st.columns([2, 1, 1])
    with c_gen:
        custom_inst = st.text_input("Author Instructions / Special Focus", placeholder="e.g., Emphasize practical worksheets and calculation formulas...")
    with c_lock:
        lock_btn_text = "Unlock Chapter" if locked else "Lock Chapter"
        if st.button(lock_btn_text, key="btn_lock", use_container_width=True):
            GenerationService.set_lock_status(current_chap["id"], not locked)
            st.rerun()
    with c_audit:
        if st.button("Run Audit & Citations", key="btn_quick_audit", use_container_width=True):
            with st.spinner("Auditing factual claims and verifying citations..."):
                auditor = AuditService()
                ok, rep, msg = auditor.audit_chapter(project_id, current_chap, charter)
                if ok:
                    st.success(f"Audit Complete! Score: {rep['score']}/100 ({rep['status']})")
                    st.rerun()
                else:
                    st.warning(msg)

    # Legacy one-shot generation remains available as an explicit fallback.
    if st.button(
        "⚡ Legacy One-Shot Generation (fallback)",
        disabled=locked or (chapter_approved and not approve_regen),
        use_container_width=True,
    ):
        with st.spinner(f"Writing Chapter {current_chap['chapter_number']} (validating word count, sections & citations)..."):
            sources = ResearchService.get_sources(project_id)
            gen_service = GenerationService()
            ok, text, report, msg = gen_service.generate_chapter_validated(
                chapter=current_chap,
                charter=charter,
                evidence_sources=sources,
                custom_instructions=custom_inst,
            )
            if ok:
                saved = GenerationService.save_chapter_content(
                    current_chap["id"], text,
                    note="Ollama Generation (validated)",
                    validation=report,
                    allow_approved_overwrite=approve_regen,
                )
                if not saved:
                    st.error(
                        "Generated text was not saved: this chapter passed an independent audit. "
                        "Confirm the overwrite checkbox to replace an approved chapter."
                    )
                else:
                    st.success(f"Chapter {current_chap['chapter_number']} generated. {msg}")
                    st.rerun()
            else:
                st.error(msg)

    # Citation Verification Summary Pill (Priority 2)
    chap_text = current_chap.get("content", "")
    if chap_text:
        cit_report = CitationService.verify_chapter_citations(project_id, chap_text)
        with st.expander("🔬 Live Citation & Source Grounding Verification", expanded=(not cit_report["is_clean"])):
            if cit_report["is_clean"] and cit_report["valid_citations"]:
                st.success(f"✅ All {len(cit_report['valid_citations'])} citations verified against SQLite research sources.")
            elif cit_report["invalid_citations"]:
                st.error(f"❌ Found unregistered Source IDs: {cit_report['invalid_citations']}. Please replace with real library sources.")
            else:
                st.info("ℹ️ No [Source-X] citations found in this chapter text.")

            if cit_report["valid_citations"]:
                st.markdown("**Cited Sources in this Chapter:**")
                for vc in cit_report["valid_citations"]:
                    st.markdown(f"• **[Source-{vc['source_id']}]** *{vc['title']}* ({vc['publisher']}) — Reliability: `{vc['reliability']}`")

    # Content Editor Area
    st.markdown("### 📝 Chapter Content Editor")
    content_val = current_chap.get("content", "")
    chapter_id = current_chap["id"]
    editor_key = f"editor_chapter_{chapter_id}"
    autosave_key = f"autosave_state_{chapter_id}"
    published_key = f"published_chapter_{chapter_id}"

    last_published = st.session_state.get(published_key)
    external_change = last_published is not None and last_published != content_val
    if last_published != content_val:
        st.session_state[published_key] = content_val

    if editor_key not in st.session_state:
        st.session_state[editor_key] = content_val
        st.session_state[autosave_key] = {
            "last_saved": content_val,
            "last_attempt": None,
            "status": AutosaveService.draft_status(chapter_id),
            "message": "",
            "base_content": content_val,
            "conflict": False,
        }
    elif external_change:
        if st.session_state[editor_key] == last_published:
            # The published chapter changed underneath us (generation, restore or
            # another edit) and the editor held no local edits. Refresh it.
            st.session_state[editor_key] = content_val
            st.session_state[autosave_key] = {
                "last_saved": content_val,
                "last_attempt": None,
                "status": AutosaveService.draft_status(chapter_id),
                "message": "",
                "base_content": content_val,
                "conflict": False,
            }
        else:
            # The editor holds local work while the published chapter changed
            # elsewhere. Never overwrite either side silently.
            existing_state = st.session_state.setdefault(autosave_key, {})
            existing_state["conflict"] = True
            existing_state["conflict_published"] = content_val
            existing_state.setdefault("base_content", last_published)
            existing_state.setdefault("last_saved", last_published)
            existing_state.setdefault("last_attempt", None)
            existing_state.setdefault("status", AutosaveService.draft_status(chapter_id))
            existing_state.setdefault("message", "")

    # Recoverable draft: never silently overwrite the published chapter.
    draft = AutosaveService.recoverable_draft(chapter_id, content_val)
    if draft:
        st.warning(
            f"An unsaved draft from {draft.get('updated_at', 'an earlier session')} "
            f"was found (status: {draft.get('status')})."
        )
        d1, d2 = st.columns(2)
        with d1:
            if st.button(
                "↩️ Restore recovered draft",
                key=f"restore_draft_{chapter_id}",
                use_container_width=True,
            ):
                st.session_state[editor_key] = draft.get("draft_content", "")
                st.rerun()
        with d2:
            if st.button(
                "🗑️ Discard draft",
                key=f"discard_draft_{chapter_id}",
                use_container_width=True,
            ):
                AutosaveService.clear_draft(chapter_id)
                st.session_state[editor_key] = content_val
                st.session_state[autosave_key] = {
                    "last_saved": content_val,
                    "last_attempt": None,
                    "status": "IDLE",
                    "message": "",
                    "base_content": content_val,
                    "conflict": False,
                }
                st.rerun()

    new_content = st.text_area(
        "Chapter Text (Markdown format supported)",
        key=editor_key,
        height=450,
        disabled=locked,
        help="Edit text freely. Drafts autosave after you pause typing and survive navigation.",
    )

    state = st.session_state.setdefault(
        autosave_key,
        {
            "last_saved": content_val,
            "last_attempt": None,
            "status": AutosaveService.draft_status(chapter_id),
            "message": "",
            "base_content": content_val,
            "conflict": False,
        },
    )

    # Conflict resolution: the published chapter changed while local edits were
    # in progress. Neither side is overwritten until the user chooses.
    if state.get("conflict"):
        st.error(
            "This chapter changed elsewhere while you were editing. Your local text "
            "is preserved and was not written over the published chapter."
        )
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button(
                "Keep my local draft",
                key=f"conflict_keep_{chapter_id}",
                use_container_width=True,
            ):
                state["conflict"] = False
                state["base_content"] = content_val
                st.rerun()
        with cc2:
            if st.button(
                "Reload published version (save my draft first)",
                key=f"conflict_reload_{chapter_id}",
                use_container_width=True,
            ):
                AutosaveService.save_draft(chapter_id, project_id, new_content)
                st.session_state[editor_key] = content_val
                state.update(
                    {
                        "conflict": False,
                        "base_content": content_val,
                        "last_saved": content_val,
                        "last_attempt": None,
                        "status": AUTOSAVE_SAVED,
                        "message": "External version loaded; your draft was retained.",
                    }
                )
                st.rerun()

    if not locked:
        now_value = time.monotonic()
        result = AutosaveService.autosave_if_changed(
            chapter_id,
            project_id,
            new_content,
            state.get("last_saved", content_val),
            last_attempt_at=state.get("last_attempt"),
            now=now_value,
            min_interval_seconds=1.5,
        )
        if result.get("attempted"):
            state["last_attempt"] = result.get("next_attempt_at", now_value)
            state["status"] = result.get("status")
            state["message"] = result.get("message", "")
            if result.get("success"):
                state["last_saved"] = result.get("saved_content", new_content)

        status_col, info_col = st.columns([2, 3])
        with status_col:
            st.caption(AutosaveService.status_label(state.get("status")))
        with info_col:
            if state.get("status") == AUTOSAVE_FAILED:
                st.error(state.get("message") or "Autosave failed.")
                failed = AutosaveService.failed_recovery(chapter_id)
                if failed:
                    st.caption(
                        f"Recoverable draft retained ({len(failed.get('draft_content') or '')} characters)."
                    )
            elif new_content != content_val:
                st.caption("Unsaved changes are kept as a recoverable draft.")
            else:
                st.caption("Drafts autosave after you pause typing and survive navigation.")

    allow_approved_overwrite = True
    if chapter_approved and new_content != content_val:
        allow_approved_overwrite = st.checkbox(
            "I understand this manual save replaces a chapter that passed an independent audit.",
            key=f"overwrite_approved_{chapter_id}",
        )
    save_disabled = locked or (
        chapter_approved and new_content != content_val and not allow_approved_overwrite
    )
    if not locked and st.button(
        "💾 Save Manual Revisions",
        key="save_manual_edit",
        disabled=save_disabled,
    ):
        if new_content != content_val:
            saved = GenerationService.save_chapter_content(
                chapter_id,
                new_content,
                note="Manual revision",
                allow_approved_overwrite=allow_approved_overwrite,
            )
            if not saved:
                st.error(
                    "Save refused: this chapter passed an independent audit. "
                    "Confirm the overwrite checkbox to replace an approved chapter."
                )
            else:
                AutosaveService.clear_draft(chapter_id)
                st.session_state[autosave_key] = {
                    "last_saved": new_content,
                    "last_attempt": None,
                    "status": AUTOSAVE_SAVED,
                    "message": "Published revision saved.",
                    "base_content": new_content,
                    "conflict": False,
                }
                st.success("Revisions saved and archived in revision history.")
                st.rerun()
        else:
            st.info("No changes to save.")
