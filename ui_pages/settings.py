import streamlit as st
from typing import Dict
from services.ollama_client import OllamaClient
from services.backup_service import (
    create_backup,
    list_backups,
    maybe_run_scheduled_backup,
    restore_backup,
    verify_backup,
)
from database.db import get_connection, transaction

def render_settings():
    st.markdown("## ⚙️ Application & Local AI Configuration")
    st.markdown("Fine-tune Ollama inference settings, select active models, and configure workstation resources.")

    from services.model_registry import ModelRegistry, TASKS
    from services.model_evaluation_service import ModelEvaluationService

    ai = OllamaClient()
    connection = ModelRegistry.connection_status()
    connected = connection.get("connected", False)
    conn_msg = connection.get("message", "")
    installed = ModelRegistry.detect_models(refresh=True)
    available_models = [m["name"] for m in installed]

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM application_settings")
        settings = dict(cur.fetchall())

    st.markdown("### 🔌 Ollama Connection")
    c1, c2 = st.columns([3, 1])
    with c1:
        current_url = settings.get("ollama_url", "http://localhost:11434")
        new_url = st.text_input("Ollama Host URL", value=current_url)
    with c2:
        st.write("")
        st.write("")
        test_btn = st.button("Test Connection", use_container_width=True)

    if connected:
        st.success(f"Connected: {conn_msg}")
    else:
        st.error(conn_msg)

    if test_btn:
        test_client = OllamaClient(base_url=new_url.strip())
        ok, msg = test_client.check_connection()
        if ok:
            st.success(f"Connection verified: {msg}")
        else:
            st.error(f"Failed to connect: {msg}")

    last_metrics = ModelRegistry.last_generation_metrics()
    if last_metrics:
        eval_count = last_metrics.get("eval_count") or 0
        eval_ms = last_metrics.get("eval_duration_ms") or 0
        tokens_per_second = (
            round(eval_count / (eval_ms / 1000), 1) if eval_count and eval_ms else None
        )
        st.caption(
            "Last generation — "
            f"model `{last_metrics.get('model', 'unknown')}` · "
            f"task `{last_metrics.get('task') or 'unspecified'}` · "
            f"{last_metrics.get('wall_time_ms', 0):.0f} ms wall time"
            + (f" · {tokens_per_second} tokens/s" if tokens_per_second else "")
            + f" · context {settings.get('context_window', '8192')} tokens"
        )

    st.markdown("---")
    st.markdown("### 🧠 Installed Local Models")
    st.caption(
        "Models are detected from Ollama. Nothing is assumed to be installed, and "
        "no cloud model is ever used."
    )
    if installed:
        inventory_rows = []
        for model in installed:
            details = ModelRegistry.model_details(model["name"])
            inventory_rows.append(
                {
                    "Model": model["name"],
                    "Available": "Yes" if model.get("available") else "No",
                    "Size": model.get("size_label", "Unknown"),
                    "Parameters": model.get("parameter_size", "Unknown"),
                    "Quantization": model.get("quantization", "Unknown"),
                    "Context length": details.get("context_length", "Unknown"),
                }
            )
        st.dataframe(inventory_rows, use_container_width=True, hide_index=True)
    else:
        st.warning(
            "⚠️ No installed models detected. Pull a local model in PowerShell, for example:\n\n"
            "```powershell\nollama pull <model-name>\n```"
        )

    global_model = settings.get("selected_model", "")
    st.markdown("### 🎯 Global Default Model")
    if available_models:
        global_options = ["(first installed model)"] + available_models
        global_index = (
            global_options.index(global_model)
            if global_model in global_options
            else 0
        )
        selected_global = st.selectbox(
            "Default model for tasks without an override",
            options=global_options,
            index=global_index,
            help="Resolution order: task override → global default → any installed model.",
        )
        if selected_global == "(first installed model)":
            selected_global = available_models[0]
    else:
        selected_global = global_model
        st.text_input("Detected model", value="None — Ollama has no installed models", disabled=True)

    st.markdown("### 🧩 Task-Specific Model Routing")
    routing = ModelRegistry.routing_table()
    routing_choices: Dict[str, str] = {}
    if available_models:
        for row in routing:
            options = ["(global default)"] + available_models
            explicit = row.get("explicit_model") or ""
            index = options.index(explicit) if explicit in options else 0
            choice = st.selectbox(
                f"{row['label']} — {row['description']}",
                options=options,
                index=index,
                key=f"routing_{row['task']}",
            )
            routing_choices[row["task"]] = "" if choice == "(global default)" else choice
            st.caption(
                f"Resolved now: {row.get('resolved_model') or 'none'} "
                f"({row.get('source', 'unavailable')})"
            )
    else:
        st.caption("Routing is unavailable until at least one local model is installed.")

    st.markdown("### 💻 Hardware & Inference Optimization")
    h1, h2 = st.columns(2)
    with h1:
        ctx_val = int(settings.get("context_window", "8192"))
        context_window = st.select_slider(
            "Context Window (Tokens)",
            options=[4096, 8192, 16384, 32768],
            value=ctx_val if ctx_val in [4096, 8192, 16384, 32768] else 8192,
            help="Keep this within available system memory for large local models.",
        )
    with h2:
        temp_val = float(settings.get("temperature", "0.7"))
        temperature = st.slider("Sampling Temperature", min_value=0.0, max_value=1.0, value=temp_val, step=0.05)

    st.markdown("### 📏 Chapter Generation Targets")
    g1, g2 = st.columns(2)
    with g1:
        target_words_val = int(settings.get("chapter_target_words", "2200"))
        chapter_target_words = st.number_input(
            "Target words per substantial chapter",
            min_value=800, max_value=6000, step=100, value=target_words_val,
            help="Recommended: 2,000–2,500 for a full ebook chapter.",
        )
    with g2:
        min_words_val = int(settings.get("min_chapter_words", "1000"))
        min_chapter_words = st.number_input(
            "Minimum words for publication",
            min_value=300, max_value=5000, step=100, value=min_words_val,
            help="Chapters below this length are counted as incomplete for publication gating.",
        )

    if st.button("Save Configuration", use_container_width=True):
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute("INSERT OR REPLACE INTO application_settings (key, value) VALUES ('ollama_url', ?)", (new_url.strip(),))
            cur.execute("INSERT OR REPLACE INTO application_settings (key, value) VALUES ('selected_model', ?)", (selected_global,))
            cur.execute("INSERT OR REPLACE INTO application_settings (key, value) VALUES ('context_window', ?)", (str(context_window),))
            cur.execute("INSERT OR REPLACE INTO application_settings (key, value) VALUES ('temperature', ?)", (str(temperature),))
            cur.execute("INSERT OR REPLACE INTO application_settings (key, value) VALUES ('chapter_target_words', ?)", (str(int(chapter_target_words)),))
            cur.execute("INSERT OR REPLACE INTO application_settings (key, value) VALUES ('min_chapter_words', ?)", (str(int(min_chapter_words)),))
        for task, model in routing_choices.items():
            ModelRegistry.set_task_model(task, model)
        st.success("Settings saved successfully!")
        st.rerun()

    st.markdown("---")
    st.markdown("### 🧪 Model Evaluation (Benchmark)")
    st.caption(
        "Compares installed models on the same JSON and chapter-writing tasks in a "
        "temporary project. Scores are benchmark-specific; they do not prove that "
        "one model is generally superior. Human review is still required."
    )
    if available_models:
        eval_model = st.selectbox(
            "Model to evaluate",
            options=["Evaluate all installed models"] + available_models,
            key="eval_model_select",
        )
        if st.button("Run Benchmark", use_container_width=True):
            service = ModelEvaluationService(ollama_client=ai)
            with st.spinner("Running local benchmark — this can take several minutes..."):
                if eval_model == "Evaluate all installed models":
                    benchmark = service.evaluate_installed()
                else:
                    benchmark = {
                        "ok": True,
                        "results": [service.evaluate_model(eval_model)],
                        "note": "Single-model benchmark result.",
                    }
            st.session_state["benchmark_results"] = benchmark
        benchmark = st.session_state.get("benchmark_results")
        if benchmark:
            for result in benchmark.get("results", []):
                if result.get("status") == "FAILED" and result.get("error"):
                    st.error(f"{result.get('model')}: {result['error']}")
                    continue
                scores = result.get("scores", {})
                st.markdown(
                    f"**{result.get('model')}** — JSON {scores.get('json_valid', 0):.0f}/1 · "
                    f"citation {scores.get('citation_compliance', 0):.2f} · "
                    f"structure {scores.get('structure', 0):.2f} · "
                    f"relevance {scores.get('topic_relevance', 0):.2f} · "
                    f"instruction {scores.get('instruction_compliance', 0):.2f} · "
                    f"{result.get('generation_ms', 0):.0f} ms"
                )
            st.info(benchmark.get("note", ""))

        evaluations = ModelEvaluationService.list_evaluations(limit=10)
        if evaluations:
            st.markdown("**Recent evaluations**")
            st.dataframe(
                [
                    {
                        "Model": e["model"],
                        "Status": e["status"],
                        "JSON valid": bool(e.get("json_valid")),
                        "Citation": e.get("citation_compliance"),
                        "Structure": e.get("structure_score"),
                        "Words": e.get("word_count"),
                        "Relevance": e.get("topic_relevance"),
                        "Instruction": e.get("instruction_compliance"),
                        "Generation ms": e.get("generation_ms"),
                        "Human score": e.get("human_score"),
                    }
                    for e in evaluations
                ],
                use_container_width=True,
                hide_index=True,
            )
            latest = evaluations[0]
            h1, h2 = st.columns([1, 2])
            with h1:
                human_score = st.number_input(
                    "Human rubric score (0–5)",
                    min_value=0.0,
                    max_value=5.0,
                    value=float(latest.get("human_score") or 0.0),
                    step=0.5,
                    key="human_eval_score",
                )
            with h2:
                human_notes = st.text_input(
                    "Human review notes",
                    value=str(latest.get("rubric", {}).get("human_notes") or ""),
                    key="human_eval_notes",
                )
            if st.button("Save Human Rubric", use_container_width=True):
                if ModelEvaluationService.set_human_score(latest["id"], human_score, human_notes):
                    st.success("Human rubric recorded.")
                else:
                    st.error("Could not save the human rubric score.")
    else:
        st.caption("Install at least one local model to run evaluations.")

    st.markdown("---")
    st.markdown("### 💾 Backup & Recovery")
    st.caption(
        "Snapshots use SQLite's online backup API (transactionally consistent) and are "
        "integrity-checked. Restoring requires explicit confirmation and always creates a "
        "pre-restore backup first."
    )

    # Interval-based scheduled backups: at most one per 24 hours.
    try:
        scheduled = maybe_run_scheduled_backup(interval_hours=24.0)
        if scheduled:
            st.toast(f"Scheduled backup created: {scheduled['path'].name}")
    except Exception as exc:  # pragma: no cover - defensive
        st.caption(f"Scheduled backup check unavailable: {exc}")

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Run Backup Now", use_container_width=True):
            info = create_backup("manual")
            if info and info.get("integrity_ok"):
                st.success(f"Backup created and verified: {info['path'].name}")
            elif info:
                st.error(f"Backup created but failed verification: {info.get('detail')}")
            else:
                st.warning("No database file available to back up yet.")
    with bcol2:
        if st.button("Check Scheduled Backup (24h interval)", use_container_width=True):
            info = maybe_run_scheduled_backup(interval_hours=24.0)
            if info and info.get("integrity_ok"):
                st.success(f"Scheduled backup created and verified: {info['path'].name}")
            elif info:
                st.error(f"Scheduled backup failed verification: {info.get('detail')}")
            else:
                st.info("A scheduled backup is not due yet (24-hour interval).")

    backups = list_backups()
    if not backups:
        st.info("No backups found yet. Use 'Run Backup Now' to create the first one.")
    else:
        st.markdown(f"**{len(backups)} backup(s) available**")
        labels = [
            f"{b['name']} — {b['size_bytes'] / 1024:.0f} KB — {b['modified_at']}"
            for b in backups
        ]
        selected_index = st.selectbox(
            "Select a backup",
            range(len(backups)),
            format_func=lambda i: labels[i],
            key="backup_select",
        )
        selected_backup = backups[selected_index]

        vcol1, vcol2 = st.columns(2)
        with vcol1:
            if st.button("Verify Selected Backup", use_container_width=True):
                ok, detail = verify_backup(selected_backup["path"])
                if ok:
                    st.success(f"Integrity verified: {detail}")
                else:
                    st.error(f"Verification failed: {detail}")
        with vcol2:
            confirm_restore = st.checkbox(
                "I understand restoring replaces the live database",
                key="confirm_restore",
            )
            if st.button(
                "Restore Selected Backup",
                disabled=not confirm_restore,
                use_container_width=True,
            ):
                ok, message = restore_backup(selected_backup["path"], confirm=True)
                if ok:
                    st.success(message)
                    st.info("Restart the Streamlit app to reload the restored data.")
                else:
                    st.error(message)

