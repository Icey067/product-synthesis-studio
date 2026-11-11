# Product Synthesis Studio

A local-first, AI-powered digital product creation platform. Product Synthesis Studio guides you through market research, customer problem analysis, transformation mapping, chapter generation, quality audits, and professional document export — powered entirely by local AI inference with zero paid API keys.

---

## ⚡ Features

- **Local AI Inference**: Powered by local models via [Ollama](https://ollama.com/) (e.g., `qwen2.5-coder:7b`, `qwen3.5:9b`).
- **Market Intelligence**: Discover and cluster high-urgency customer problems with transparent multi-factor scoring.
- **Deep Research Library**: Ingest research documents (PDF, TXT, MD, CSV) and public URLs with automatic SHA-256 deduplication and SSRF protection.
- **Transformation Mapping**: Map the journey from acute customer pain points (*Before*) to practical solutions and measurable outcomes (*After*).
- **Master Product Charter**: Establish a single source of truth for promises, deliverables, unique mechanisms, and required disclaimers.
- **Product Builder & Chapter Studio**: Draft structured, actionable educational chapters with action checklists, worksheets, and autosaved revision history.
- **Quality Assurance Auditor**: Automated factual grounding, citation verification, readability assessment, and compliance checks.
- **Brand Studio**: Generate original product names, curated color palettes, and typography pairings.
- **Publication Exports**: Compile chapters into professionally formatted A4 PDFs (via ReportLab) and editable Word documents (via python-docx).

---

## 🚀 Quick Start

### Prerequisites

1. **Python 3.11** installed.
2. **Ollama** installed and running:
   ```bash
   ollama pull qwen2.5-coder:7b
   ollama serve
   ```

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Icey067/product-synthesis-studio.git
   cd product-synthesis-studio
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # Windows (PowerShell):
   .\.venv\Scripts\Activate.ps1
   # macOS/Linux:
   source .venv/bin/activate
   ```

3. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Launch the application:
   ```bash
   # Using the Windows batch launcher:
   start.bat

   # Or run directly via Streamlit:
   streamlit run app.py
   ```

5. Open your browser at `http://localhost:8501`.

---

## 🛠️ Tech Stack

- **Frontend / UI**: [Streamlit](https://streamlit.io/)
- **Local AI Engine**: [Ollama](https://ollama.com/)
- **Database**: SQLite (with schema migrations & foreign key enforcement)
- **Document Processing**: [ReportLab](https://www.reportlab.com/) (PDF) & [python-docx](https://python-docx.readthedocs.io/) (Word)
- **Validation**: [Pydantic v2](https://docs.pydantic.dev/)

---

## 📄 License

MIT License.
