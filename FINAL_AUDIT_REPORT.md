# Final Consistency & Data-Safety Audit Report

**Project:** Product Synthesis Studio
**Date:** 2026-11-11
**Scope:** Canonical manuscript counts, per-project word settings, backup/restore,
duplication integrity, test isolation, autosave safety, UI consistency, live
Ollama acceptance, and production project 59 integrity.

---

## 1. Automated test suite

| Metric | Result |
|---|---|
| Total collected | 111 |
| Passed | 111 |
| Failed | 0 |
| Skipped | 0 |
| Warnings | 5 (cosmetic SWIG/reportlab `DeprecationWarning`s) |
| Runtime | 16.67 s |

Command:

```
.\.venv\Scripts\python.exe -m pytest -q
```

New audit coverage lives in `tests/test_final_audit.py` (16 tests) and covers:

- identical canonical counts from `assess_project()` and `assess(chapters)`;
- project-scoped counting (global chapter totals never leak in);
- per-project target/minimum/required settings persistence and isolation;
- short, exactly-at-target, and over-target chapters against project thresholds;
- WAL-safe online backup, integrity/FK verification, and verified restore;
- FK-orphan rejection, corrupt-backup refusal, failed-temp cleanup, retention;
- duplication with full FK remapping and original-untouched checks;
- test-environment isolation assertions;
- autosave FAILED recovery, conflict detection, approved-chapter overwrite
  protection, and batch-skip of approved chapters;
- hallucinated evidence source IDs stored safely as NULL with no FK failure;
- outline validation honoring each project's configured chapter count.

`tests/conftest.py` now forces every run onto a temporary database/output/backup
directory and fingerprints the production database, output folder, and backup
folder before and after the session, failing the run if any changed. This guard
passed in the final run.

---

## 2. Live Ollama acceptance

Model: `qwen2.5-coder:7b` (the only locally installed model; configured default
`qwen3.5:9b` is absent). Ran against an isolated clone of the production
database, never the live database.

**Result: 21 passed, 0 failed.**

| # | Step | Result |
|---|---|---|
| 1 | Project creation (2 chapters / 600 target / 300 min) | PASS |
| 2 | Research ingestion (1 source, 5 chunks) | PASS |
| 3 | Opportunity discovery | PASS |
| 4 | Evidence scoring | PASS |
| 5 | Transformation map | PASS |
| 6 | Product charter | PASS |
| 7 | Niche-specific outline | PASS |
| 8 | Two chapter generations | PASS |
| 9 | Editing + autosave | PASS |
| 10 | Project duplication | PASS |
| 11 | Quality audit | PASS (audit verdict NEEDS_IMPROVEMENT, score 0, human review required) |
| 12 | Partial-publication blocking | PASS (correctly blocked) |
| 13 | Full-publication verification (synthetic approved project) | PASS (PUBLICATION_READY, PDF exported) |
| 14 | PDF + DOCX draft exports | PASS (PDF 122,259 B; DOCX 42,171 B; labelled DRAFT 1/2) |
| 15 | Database backup | PASS (integrity + FKs verified) |
| 16 | Database restore | PASS (mutation reverted, pre-restore backup taken, FK 0) |
| 17 | Project reopening | PASS |
| — | Clone integrity (FK/integrity) | PASS |
| — | Original project 59 unchanged in clone | PASS |
| — | Production project 59 rows (11 chapters, 3,666 words) | PASS |
| — | Production database integrity | PASS |

Generated chapters were 886 words (INCOMPLETE) and 1,086 words (COMPLETE). The
partial manuscript was correctly refused full publication without a draft label;
a separate synthetic project satisfying every gate reached `PUBLICATION_READY`
and exported successfully.

Live run temp dir: `C:\Users\flame\AppData\Local\Temp\pss_live_qpuapogy`

---

## 3. Canonical count consistency

`ManuscriptService` is the single source of truth. For production project 59 it
reports:

```
required 11 | existing 11 | written 3 | minimum-compliant 2
audit-approved 0 | human-review 0 | publication-ready 0
status WORKING_DRAFT | can_publish False | total 3,666 words
```

Verified on an isolated production clone with Streamlit `AppTest`:

- **Dashboard** — 3/11 written, 2 at minimum length, 0 publication-ready ✅
- **Product Builder** — all 7 canonical values ✅
- **Quality Auditor** — all 7 canonical values ✅
- **Product Exports** — all 7 canonical values ✅

All 14 routes rendered with zero exceptions (Market Intelligence and Product
Charter surfaced legitimate `st.error` notices, not crashes). This confirms the
original `"3/12"` / `"2/11"` discrepancies were global-vs-project mixing and are
now eliminated.

---

## 4. Per-project word-count settings

- Stored on `projects`: `required_chapter_count`, `target_chapter_words`,
  `min_chapter_words`.
- Exposed at creation (`ui_pages/project_setup.py`) and editable later.
- Read by outline generation, chapter generation, completeness assessment,
  verification, and exports.
- Updating one project never changes another.
- Invalid combinations (minimum > target) are rejected before writing.
- Migration 5 (`project_manuscript_settings`) applied to production; project 59
  defaulted to 11 / 2,200 / 1,000 and its manuscript fingerprint is unchanged
  (`52a76f2f…3080fcf`).

---

## 5. Backup and restore

- Backups use SQLite's online backup API, so they are transactionally consistent
  even while the database is writing (verified under WAL).
- Every backup is integrity- and foreign-key-checked; a failed run leaves no
  partial temp file.
- Retention prunes to the newest 30 and removes old files.
- Restore is explicitly gated (`confirm=True`), refuses corrupt backups without
  touching the live DB, always takes a pre-restore backup, and re-verifies after
  restoring.
- Real restore test reverted a post-backup mutation and preserved chapters,
  sources, projects, and FKs.

---

## 6. Duplication integrity

`ProjectService.duplicate_project` deep-copies projects via explicit old→new ID
maps. Verified:

- research batches, sources, chunks, findings, opportunities, evidence, scores,
  competitors, transformation maps, charters, outlines, chapters, quality
  reports, brands, exports, sales pages, distribution plans, analytics,
  autosaves, and revisions all copy;
- chunks/findings/evidence point at the duplicate's own source rows;
- quality reports and autosaves point at the duplicate's own chapters;
- editing the duplicate (chapter content, source title, project name) never
  changes the original;
- per-project word settings survive the copy.

---

## 7. Autosave and content safety

- Autosave writes only to `chapter_autosaves`, never to published chapter
  content.
- States are `SAVING` / `SAVED` / `FAILED`; failures retain the draft and error
  for recovery after a restart.
- External-edit conflicts are detected by hash and require explicit user choice;
  they are never auto-resolved.
- `save_chapter_content` refuses to overwrite an independently approved chapter
  unless `allow_approved_overwrite=True`, and archives a revision when it does.
- Batch generation skips approved chapters unless `force=True` and reports them
  as `SKIPPED_APPROVED`.

---

## 8. Production project 59 integrity

Final read-only check after all migrations and test runs:

| Check | Result |
|---|---|
| Schema migrations | `[1, 2, 3, 4, 5]` |
| Project name | PROBE Independent Pharmacy Digital Growth (India) |
| Settings | 11 required / 2,200 target / 1,000 minimum |
| Chapters | 11 (word counts: 737, 1471, 1458, then 0 × 8) |
| Total words | 3,666 |
| Research sources | 4 |
| Opportunities | 3 |
| Exports | 2 |
| `PRAGMA foreign_key_check` | empty |
| `PRAGMA integrity_check` | `ok` |
| Chapter fingerprint | `52a76f2f859ea8d9097d1f12b1eac6a816f318656a172687ddb3544ba3080fcf` (unchanged) |

No production content was modified by the audit. A pre-migration backup was
created automatically before migration 5.

---

## 9. Known defects and limitations

1. **Default model missing locally.** `config/settings.py` defaults to
   `qwen3.5:9b`, but only `qwen2.5-coder:7b` is installed. The app correctly
   reports the connection/model state; the default must be pulled or changed for
   out-of-the-box generation.
2. **Small-model output quality.** The 7B model produced an INCOMPLETE chapter
   (886 words with missing structural sections) and an audit verdict of
   NEEDS_IMPROVEMENT (score 0, human review required). Generation validation and
   publication gating handle this correctly, but content may need regeneration or
   manual editing.
3. **Outline flakiness.** Outline generation can still exhaust its 3 attempts on
   a weak model (placeholder titles or chapter counts). Validation is
   intentionally strict; the user must retry rather than receive an invalid
   outline.
4. **Console encoding only.** Printing non-ASCII (em dashes, emoji) to a Windows
   cp1252 console raises `UnicodeEncodeError`; the app and exports are unaffected
   and UTF-8 consoles work. Use `PYTHONIOENCODING=utf-8` for CLI scripts.
5. **Rejected evidence provenance.** A rejected claim citing a non-existent
   source now stores `source_id = NULL` and keeps the invalid ID only in
   `verification_notes` text, so no FK can fail. The raw numeric ID is no longer
   a column value (by design).
6. **Project 59 content gap.** Chapter 1 is 737 words (below the 1,000 minimum)
   and chapters 4–11 are empty. This is real unpublished content, accurately
   reported; nothing was overwritten.
7. **No version control.** The workspace is not a git repository; all changes are
   working-tree edits, so there is no commit history to roll back to.

---

## Verdict

All automated tests pass (111/111), the live Ollama pipeline passes end to end
(21/21), every count-displaying screen agrees with the canonical service,
per-project settings are isolated, backup/restore is WAL-safe and verified,
duplication remaps every foreign key, tests cannot touch production data, and
production project 59 is byte-for-byte unchanged with integrity `ok`.
