"""Export Unicode / Markdown / branding regression tests."""
import os

import fitz
import docx

from models.schemas import ProjectCreate
from services.export_service import ExportService
from services.project_service import ProjectService

CHARTER = {
    "product_title": "Clinic Revenue Playbook",
    "subtitle": "Unicode and Markdown Verification",
    "target_audience": "Clinic owners",
    "product_category": "Business Education",
    "disclaimer_requirements": ["Educational only."],
}

CONTENT = (
    "## Introduction\n\n"
    "Revenue rose to ₹48,000 for R&D teams while a <custom-tag> stayed intact.\n\n"
    "| Metric | Value |\n"
    "|---|---|\n"
    "| No-show rate | 14.8% |\n"
    "| Reminder recovery | 68% |\n\n"
    "- [x] Reviewed the cancellation log\n"
    "- [ ] Wrote the reminder script\n\n"
    "- A plain bullet point\n\n"
    "1. First numbered step\n"
    "2. Second numbered step\n\n"
    "> A quoted reminder tip.\n"
)


def _project():
    return ProjectService.create_project(ProjectCreate(name="Unicode Export Test"))


def test_pdf_preserves_unicode_rupee_ampersand_and_markdown():
    p_id = _project()
    chapters = [{"chapter_number": 1, "title": "Revenue Setup", "purpose": "Verify",
                 "content": CONTENT}]
    ok, path, msg = ExportService.generate_pdf(
        p_id, CHARTER, chapters, sources=[], brand={"brand_name": "Acme Dental Press"}
    )
    assert ok, msg
    doc = fitz.open(path)
    text = "".join(page.get_text() for page in doc)
    doc.close()

    assert "₹48,000" in text, text
    assert "R&D" in text
    assert "<custom-tag>" in text
    assert "14.8%" in text
    assert "Reviewed the cancellation log" in text
    assert "Wrote the reminder script" in text
    assert "A plain bullet point" in text
    assert "First numbered step" in text

    # Customer branding, not studio branding
    assert "Acme Dental Press" in text
    lowered = text.lower()
    assert "product synthesis studio" not in lowered
    assert "proprietary" not in lowered

    os.remove(path)
    ProjectService.delete_project(p_id)


def test_docx_preserves_unicode_and_markdown():
    p_id = _project()
    chapters = [{"chapter_number": 1, "title": "Revenue Setup", "purpose": "Verify",
                 "content": CONTENT}]
    ok, path, msg = ExportService.generate_docx(
        p_id, CHARTER, chapters, sources=[], brand={"brand_name": "Acme Dental Press"}
    )
    assert ok, msg
    document = docx.Document(path)
    body = "\n".join(p.text for p in document.paragraphs)
    table_text = "\n".join(
        cell.text for table in document.tables for row in table.rows for cell in row.cells
    )
    header = document.sections[0].header.paragraphs[0].text
    footer = document.sections[0].footer.paragraphs[0].text

    assert "₹48,000" in body
    assert "R&D" in body
    assert "14.8%" in table_text
    assert "Acme Dental Press" in header
    assert "Acme Dental Press" in footer

    os.remove(path)
    ProjectService.delete_project(p_id)
