import os
import pytest
import fitz
import docx
from database.db import init_db
from services.project_service import ProjectService
from services.export_service import ExportService
from models.schemas import ProjectCreate

@pytest.fixture(autouse=True)
def setup_db():
    init_db()

def test_pdf_and_docx_exports(tmp_path):
    # Create valid project in DB to satisfy foreign key constraints
    p_id = ProjectService.create_project(ProjectCreate(name="Export Test Project"))

    charter = {
        "product_title": "Automated Unit Test Guide",
        "subtitle": "Testing ReportLab and python-docx Pipelines",
        "target_audience": "Software Developers",
        "product_category": "Technology",
        "disclaimer_requirements": ["Test disclaimer: All examples are synthetic."],
    }
    chapters = [
        {
            "chapter_number": 1,
            "title": "Introduction to Verification",
            "purpose": "Verify layout rendering",
            "content": "## Chapter 1: Introduction\n\nThis is a verified test paragraph with **bold styling** and bullet points:\n\n- Point A\n- Point B\n\nChecklist verified.",
        },
        {
            "chapter_number": 2,
            "title": "Advanced Document Assembly",
            "purpose": "Check multi-page breaks",
            "content": "## Chapter 2: Assembly\n\nVerified second chapter content.",
        }
    ]
    sources = [
        {
            "id": 1,
            "title": "Python Document Processing Standards",
            "publisher": "Open Source Standards",
            "publication_date": "2024",
            "url": "https://example.com/standards",
        }
    ]

    # Test PDF
    ok_pdf, pdf_path, msg_pdf = ExportService.generate_pdf(p_id, charter, chapters, sources)
    assert ok_pdf is True
    assert os.path.exists(pdf_path)

    # Open PDF with PyMuPDF to ensure not corrupted or blank
    pdf_doc = fitz.open(pdf_path)
    assert len(pdf_doc) >= 4  # Cover, copyright, TOC, chapters, references
    pdf_text = "".join([page.get_text() for page in pdf_doc])
    assert "Automated Unit Test Guide" in pdf_text
    assert "Chapter 1: Introduction to Verification" in pdf_text
    pdf_doc.close()

    # Test DOCX
    ok_docx, docx_path, msg_docx = ExportService.generate_docx(p_id, charter, chapters, sources)
    assert ok_docx is True
    assert os.path.exists(docx_path)

    # Open DOCX with python-docx
    wdoc = docx.Document(docx_path)
    assert len(wdoc.paragraphs) > 5
    docx_text = "\n".join([p.text for p in wdoc.paragraphs])
    assert "Automated Unit Test Guide" in docx_text
    assert "Chapter 1: Introduction to Verification" in docx_text

    # Cleanup test files and project
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    if os.path.exists(docx_path):
        os.remove(docx_path)
    ProjectService.delete_project(p_id)
