# P2 Intelligence & Content-Quality Report

Date: 2026-11-11
Scope: task-specific local model routing, structured chapter generation, context-aware retrieval, editorial auditing, batch generation, model benchmarking.

## 1. What changed (additive only)

| Area | Implementation |
| --- | --- |
| Model routing | `services/model_registry.py` detects installed Ollama models, exposes size/parameter/quantization/context metadata, and routes per task (`market_research`, `evidence_synthesis`, `customer_transformation`, `product_charter`, `outline_generation`, `long_form_writing`, `content_editing`, `quality_review`). Resolution: task override → global default → any installed model → explicit failure. Uninstalled choices are skipped. |
| Generation client | `services/ollama_client.py` rejects generation when no installed model exists, honours the configured context window, and records wall time, model eval counts and tokens/s. The hard-coded `qwen3.5:9b` default was removed from code and from fresh-database seeding. |
| Schema | Migration 6 adds `chapter_plans`, `chapter_sections`, `chapter_sources`, `batch_sessions`, `model_evaluations`, `editorial_recommendations`, plus chapter completeness/editorial columns and quality-report editorial columns. |
| Retrieval | `services/retrieval_service.py` performs deterministic hybrid ranking (BM25-style lexical + source quality + recency + dedupe), advisory contradiction flags, and persists the exact chunks/sources used per chapter. It is described as hybrid lexical ranking — not as semantic/vector search. |
| Structured chapters | `services/chapter_planning_service.py` plans 8 sections with budgets that sum to the chapter target, writes section by section with continuation, strips invented citation labels, assembles, runs deterministic consistency checks, validates the contract, and saves an editable revision. Section drafts persist immediately. |
| Editorial rubric | `services/editorial_service.py` computes authoritative deterministic proxies for 10 dimensions plus an advisory model opinion. Recommendations are stored and require user approval/dismissal. |
| Batch | `services/batch_service.py` supports next/all/pause/resume/retry, per-section regeneration, locking, progress, research coverage, interruption recovery, and approved-content protection. |
| Benchmark tool | `services/model_evaluation_service.py` evaluates installed models on identical JSON and chapter tasks in a temporary project with a clearly labelled synthetic citation fixture. |
| Project duplication | `services/project_service.py` now copies and remaps P2 tables (plans, sections, sources, sessions, evaluations, recommendations). |
| Benchmark project | `services/benchmark_project.py` creates the real India / non-expert / English / ebook project and prints a deterministic assessment without seeding research. CLI: `python -m services.benchmark_project --report|--live|--delete`. |

## 2. Test results

```
124 passed, 5 warnings
```

- 111 pre-existing tests still pass unchanged.
- 13 P2 tests in `tests/test_p2_intelligence.py` cover: model detection and task selection, unavailable-model skipping, retrieval ranking/dedupe/traceability, section budgets summing to target, valid-citation preservation and invented-citation stripping, section continuation, structured chapter assembly, batch pause/resume, approved-chapter protection, duplicate remapping of P2 tables, editorial deterministic/advisory separation with human review, model-evaluation metrics/human rubric, and benchmark-project honesty.

`compileall` is clean across `services`, `database`, `ui_pages`, `tests`, `config`, `models`, `utils`.

## 3. Live results (local Ollama)

Installed model: `qwen2.5-coder:7b` (7.6B, Q4_K_M, 32,768-token context). No cloud model was used.

### Model evaluation (isolated database)

- JSON validity: **1.0**, JSON quality **1.0**, JSON time 5.4 s
- Chapter structure: **6/6 headings** (score 1.0)
- Topic relevance: **1.0**
- Instruction compliance: **1.0**, 915 actual words
- Citation compliance: **0.5** — the model emitted no citation at all (`found: []`). Not citing is treated as honest-but-not-demonstrated, not as compliance.
- Chapter generation time: **33.0 s**
- Human rubric: **pending** (no score auto-filled)

### Full benchmark project (isolated database, 1 of 11 chapters generated)

- Project metadata: India, English, `NON_EXPERT`, `Ebook`
- Charter, transformation map and 11-chapter outline generated successfully
- Chapter 1: **2,833 words**, **8/8 sections**, status `COMPLETE`, `structural_complete=true`
- Research sources: **0** (deliberately not seeded)
- Evidence completeness: **0/11 chapters**; citations: **none**
- Exports: none; `commercial_readiness_claim: false`

The report therefore shows a structurally complete, evidence-free chapter instead of presenting synthetic research as proof.

### Live audit + editorial rubric

- Audit completed; score **64/100**, status `NEEDS_IMPROVEMENT`
- Editorial status `NEEDS_REVISION`; advisory model review **available**
- 8 recommendations stored as `PENDING` (deterministic `HIGH`/`MEDIUM` plus model-advisory `HIGH`/`MEDIUM`)

## 4. Production data preservation

Verified after migration to v6:

- Migrations: `[1, 2, 3, 4, 5, 6]`
- Project 59 intact: 11 chapters, 3,666 words, 4 research sources, 3 opportunities, 2 exports
- New tables and chapter columns present
- `PRAGMA foreign_key_check`: no violations
- `PRAGMA integrity_check`: `ok`
- Tests run against a temporary database; the production fingerprint guard in `tests/conftest.py` passed.

## 5. Limitations and honest gaps

1. Only one model is installed, so the benchmark cannot compare models. No superiority claim is made.
2. Only chapter 1 of the 11-chapter benchmark was generated live (runtime constraint). The remaining ten are correctly reported as incomplete, not assumed.
3. With no research sources, the generated chapter cites nothing and is evidence-incomplete even though its structure is complete. This is the intended behaviour.
4. The evaluated model produced generic phrasing ("In today's digital age") and no citation; automated metrics cannot judge prose quality, which is why the human rubric remains pending.
5. Consistency/transition flags are advisory heuristics; they are surfaced but do not block saving.
6. The Streamlit screens were wired and import-checked, but not exercised in a live browser session during this run.
7. Deterministic checks validate structure, citations, statistics present in sources, length and format. They do not prove factual accuracy, market demand or commercial viability, and no export is treated as readiness evidence.

## 6. Reproduce

```powershell
# Tests
.\.venv\Scripts\python.exe -m pytest -q

# Benchmark project (isolated DB recommended via PSS_DATABASE_PATH)
.\.venv\Scripts\python.exe -m services.benchmark_project --report
.\.venv\Scripts\python.exe -m services.benchmark_project --live --max-chapters 1
```

Live model runs require `ollama serve` and at least one installed model; the app reports this explicitly instead of guessing one.
