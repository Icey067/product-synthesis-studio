# PRODUCT SYNTHESIS STUDIO
**Local-First AI Digital Product Creation Factory**

Product Synthesis Studio (PSS) is an original, complete, local-first software platform designed to identify high-urgency market problems, research solutions, synthesize empirical evidence, write full structured digital products (ebooks, playbooks, workbooks), audit content quality, design branding, and compile publication-grade PDF and DOCX deliverables.

Built specifically for Windows 11 with local GPU/CPU offloading via **Ollama**, without mandatory cloud AI APIs or subscription paywalls.

---

## ⚡ Key Architecture & Features

1. **Local-First AI Engine:** Connected directly to your local Ollama instance (`http://localhost:11434`). Installed models are detected automatically and can be routed per task (research, synthesis, charter, outline, long-form writing, editing, quality review). No cloud model is ever used.
2. **Transparent Multi-Attribute Opportunity Scoring:** 7-factor weighted scoring model (Demand 20%, Willingness to Pay 20%, Urgency 20%, Competitor Gap 15%, Specificity 10%, Solvability 10%, Feasibility 5%). The model-estimated score is always displayed separately from independently computed evidence confidence and is never presented as verified profitability.
3. **Deep Research Library:** Ingests PDFs, TXT, Markdown, CSV, and public web pages with built-in SSRF defense, SHA-256 deduplication, citation ID tracking (`[Source-ID]`), chunk-level indexing, and relevance-ranked retrieval for large libraries.
4. **Source Trust Separation:** Your reliability label is stored only as a **declaration**. An independent assessment (publisher, provenance, methodology, content signals) produces the reliability actually used for evidence and confidence — a self-declared `HIGH` never creates credibility.
5. **Evidence vs. Score Separation:** A quote can be found in a source without factually supporting a claim. Quote-only or uncertain support is flagged for human review and never populates factual fields.
6. **Customer Transformation Mapping:** Visual Before & After journey maps with phased implementation milestones.
7. **Master Product Charter Blueprint:** Central source of truth for promises, unique mechanisms, scope boundaries, and mandatory disclaimers.
8. **Chapter Builder & Autosave Revision Engine:** Drafts chapters one at a time with dedicated worksheets, action checklists, and pitfall analyses without overwriting locked content. Editor drafts autosave after a pause, survive navigation, and can be recovered or discarded explicitly.
9. **Backup & Recovery:** Transactionally consistent SQLite snapshots via the online backup API, integrity verification, interval-based scheduled backups, automatic pre-destructive/pre-migration snapshots, and confirmation-gated restore.
10. **Automated Quality Auditor:** Evaluates drafts across factual claims, readability, repetitions, and compliance flags.
11. **Brand Studio & Curated Palettes:** Original color palettes, typography pairings, and layout concepts.
12. **Publication-Ready Exporters:** Generates clean A4 PDFs (ReportLab with dynamic `Page X of Y` headers/footers) and structured Microsoft Word documents (`python-docx`).
13. **Sales Offer & Launch Package:** Ethical landing page copy, FAQs, social teasers, and broadcast announcement drafts.
14. **Commercial Analytics:** Log visits, sales, revenue, and customer feedback to track real iterations.
15. **Complete Project Duplication:** Copies the entire project graph — research, chunks, findings, opportunities, evidence, scores, charters, outlines, chapters, revisions, audits, brands, exports, sales pages, distribution, analytics and activity history — with explicit ID remapping so the copy is independently editable.

---

## 💻 Hardware Compatibility & Sizing

Tailored for your setup:
- **OS:** Windows 11
- **CPU:** Intel Core i7-14650HX (16 Cores, 24 Threads)
- **GPU:** NVIDIA GeForce RTX 4060 Laptop GPU (8 GB VRAM)
- **RAM:** 16 GB System Memory
- **Inference Profile:** 8,192 token context window, single concurrent inference request, with automatic layer offloading to system RAM.

---

## 🚀 Quick Start Guide (Windows 11)

### Option A — One-Click Launch (Recommended)
Simply double-click the included batch launcher:
```powershell
start.bat
```
This automatically verifies Python 3.11, prepares the isolated `.venv`, initializes the database schema, checks Ollama connectivity, and launches the application at `http://localhost:8501`.

---

### Option B — Manual Step-by-Step Setup

#### 1. Install & Start Ollama
1. Download Ollama for Windows from [ollama.com](https://ollama.com).
2. Open PowerShell and pull any model that fits your hardware, for example:
   ```powershell
   ollama pull qwen2.5-coder:7b
   ```
   The application does not assume a model is installed — it lists what is actually available and lets you choose a default plus per-task overrides in **Settings**. Compare candidates with the built-in benchmark before relying on one.
3. Ensure the local service is running:
   ```powershell
   ollama serve
   ```

#### 2. Virtual Environment & Dependencies
In PowerShell, navigate to the project directory:
```powershell
cd "c:\Users\flame\OneDrive\Desktop\Coding\Practical Projects\Synthesize\Product_Synthesis_Studio"
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

#### 3. Initialize SQLite Database
```powershell
python -m database.db
```

#### 4. (Optional) Seed the Sample Demonstration Project
A complete sample project (*"The 30-Day Local Business Digital Growth Playbook"* tailored for micro-businesses in India) is included:
```powershell
python -m sample_data.seed_sample_project
```

#### 5. Launch the Studio Application
```powershell
streamlit run app.py --server.port 8501 --server.address localhost
```
Your browser will open automatically at `http://localhost:8501`.

---

## 🧪 Running Automated Tests

Run the full automated test suite (including scoring algorithms, security SSRF filters, file parsers, database persistence, and document compilation):
```powershell
.\.venv\Scripts\pytest.exe -v
```

To run the live probe against your local Ollama service:
```powershell
python -m tests.test_live_ollama
```

---

## 📂 Project Directory Structure

```
Product_Synthesis_Studio/
│
├── app.py                     # Streamlit application entry point & router
├── requirements.txt           # Pinned production dependencies
├── README.md                  # Complete documentation and user guide
├── start.bat                  # One-click Windows batch launcher
├── pytest.ini                 # Test suite configuration
│
├── config/
│   ├── settings.py            # Global paths, timeouts, security thresholds
│   └── prompts.py             # System prompts and structured JSON templates
│
├── database/
│   ├── db.py                  # Thread-safe SQLite connection & transactions
│   ├── migrations.py          # Forward migrations with pre-migration backups
│   └── schema.sql             # Relational schema (foreign keys, cascading delete)
│
├── models/
│   ├── schemas.py             # Pydantic data models for structured AI output
│   └── enums.py               # Application-wide enumerations
│
├── services/
│   ├── ollama_client.py       # Robust local Ollama HTTP client with retry logic
│   ├── project_service.py     # Project CRUD, deep duplication, and dashboard KPIs
│   ├── market_service.py      # Market problem discovery, coverage, and counting
│   ├── scoring_service.py     # 7-factor model score + independent evidence confidence
│   ├── evidence_service.py    # Quote existence vs. factual support verification
│   ├── source_trust_service.py# Independent source reliability assessment
│   ├── backup_service.py      # Verified snapshots, scheduling, gated restore
│   ├── autosave_service.py    # Debounced, recoverable editor drafts
│   ├── competitor_service.py  # Competitor auditing and feature gap mapping
│   ├── research_service.py    # Ingestion, hashing, chunk indexing, ranked retrieval
│   ├── synthesis_service.py   # Bounded multi-batch evidence synthesis
│   ├── transformation_service.py # Before-and-After transformation journeys
│   ├── charter_service.py     # Master Product Charter blueprint engine
│   ├── outline_service.py     # 11-chapter sequential outline builder
│   ├── generation_service.py  # Chapter drafting with worksheet integrations
│   ├── audit_service.py       # Rigorous quality & factual auditing
│   ├── branding_service.py    # Brand identity, palettes, and cover concepts
│   ├── export_service.py      # ReportLab PDF & python-docx compilation
│   ├── sales_service.py       # Ethical sales copy, FAQs, and social posts
│   └── analytics_service.py   # Commercial metrics & feedback logger
│
├── ui_pages/                  # Custom sidebar router (not Streamlit auto-nav)
│   ├── dashboard.py           # KPIs, project switcher, Ollama health
│   ├── project_setup.py       # New project creation wizard
│   ├── market_intelligence.py # Opportunities, score/confidence split, coverage
│   ├── competitors.py         # Competitor matrix & differentiation
│   ├── research_library.py    # Upload, URL fetcher, declared vs. assessed trust
│   ├── transformation.py      # Visual Before/After roadmap
│   ├── product_charter.py     # Editable master product blueprint
│   ├── product_builder.py     # Chapter editor, locking, autosave & recovery
│   ├── quality_auditor.py     # Automated audit report viewer
│   ├── brand_studio.py        # Color palettes & typography pairings
│   ├── exports.py             # One-click PDF & Word document downloads
│   ├── distribution.py        # Sales pages & marketing announcement assets
│   ├── analytics.py           # Revenue, sales, and visit tracking charts
│   └── settings.py            # Ollama config, backup, verify, and restore
│
├── utils/
│   ├── security.py            # DNS-aware SSRF defense, IP pinning, prompt wrapping
│   ├── file_handler.py        # PDF (PyMuPDF), TXT, MD, CSV text extraction
│   └── url_handler.py         # Safe public webpage scraper with per-hop checks
│
├── assets/
│   └── styles.css             # Premium dark SaaS aesthetic styling
│
├── backups/                   # Verified SQLite snapshots (manual/scheduled/pre-*)
│
├── output/                    # Destination for compiled PDF and DOCX files
├── sample_data/
│   └── seed_sample_project.py # Full reproducible sample project
└── tests/
    ├── conftest.py            # Isolated temp DB/output/backup directories
    ├── test_database.py       # Persistence, updates, cascading deletion tests
    ├── test_exports.py        # ReportLab and DOCX generation tests
    ├── test_generation.py     # Structured validation and mock AI tests
    ├── test_scoring.py        # Weighted matrix and evidence-confidence tests
    ├── test_security.py       # SSRF blocking & file rejection tests
    ├── test_p1_security.py    # DNS pinning, encoded IPs, redirects, prompt isolation
    ├── test_p1_source_trust.py# Declared vs. independently assessed reliability
    ├── test_p1_evidence_separation.py # Quote vs. support, model score vs. confidence
    ├── test_p1_duplication.py # Complete deep-copy and ID remapping
    ├── test_p1_backup_autosave.py # Verified backups, gated restore, debounced drafts
    ├── test_p1_research_navigation.py # Chunk retrieval, batching, router cleanup
    └── test_live_ollama.py    # Live Ollama endpoint probe
```

---

## 🔒 Security & Privacy Guarantees

- **No Remote Telemetry or Cloud Offloading:** Runs 100% locally on your machine.
- **SSRF Defense:** URL fetching resolves DNS and blocks RFC 1918 private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopbacks (`127.0.0.0/8`, `localhost`), link-local/metadata (`169.254.0.0/16`), shared address space, and non-HTTP protocols (`file://`, `ftp://`). Decimal, octal, hexadecimal and abbreviated IPv4 encodings are decoded before validation.
- **No DNS Rebinding:** A validated hostname is pinned to its approved address for the actual request, and every redirect hop is re-validated, so a host cannot resolve to a public address and then rebind to an internal one.
- **Bounded Fetching:** Only text-like content types are accepted, response size is capped, and timeouts are enforced.
- **Prompt Isolation:** Untrusted research texts are escaped and enclosed within strict `<RESEARCH_SOURCE>` delimiters to prevent indirect prompt injection; hostile closing tags or system-tag lookalikes cannot break out of the wrapper.
- **Honest Evidence:** Self-declared source reliability never creates credibility, quotes are not treated as factual proof, and model scores are labelled as estimates rather than verified profitability.
- **Recoverable Data:** Backups are transactionally consistent and integrity-checked; restore is refused unless explicitly confirmed, and always creates a pre-restore snapshot first.

