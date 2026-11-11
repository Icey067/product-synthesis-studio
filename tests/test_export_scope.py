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

def test_chapter_specific_pdf_and_docx_exports(tmp_path):
    p_id = ProjectService.create_project(ProjectCreate(name="Single Chapter Export Test"))

    charter = {
        "product_title": "Concise Implementation Playbook",
        "subtitle": "Single Chapter Verification",
        "target_audience": "Clinic Directors",
        "product_category": "Business",
        "disclaimer_requirements": ["Results depend on clinic implementation."],
    }
    chapters = [
        {
            "chapter_number": 1,
            "title": "Diagnosis and Patient Communication",
            "purpose": "Verify targeted single chapter output",
            "content": "## Chapter 1: Diagnosis\n\nThis is a concise 500-word chapter body with action points.\n\n- Step 1: Audit schedule\n- Step 2: Implement SMS",
        },
        {
            "chapter_number": 2,
            "title": "Unwritten Chapter Two",
            "purpose": "Empty chapter that should be omitted",
            "content": "",
        },
        {
            "chapter_number": 3,
            "title": "Unwritten Chapter Three",
            "purpose": "Empty chapter that should be omitted",
            "content": "",
        }
    ]
    sources = [
        {
            "id": 101,
            "title": "Operations Clinical Whitepaper",
            "publisher": "National Institute",
            "publication_date": "2024",
            "url": "https://example.com/whitepaper",
        }
    ]

    # Export SINGLE Chapter 1
    ok_pdf, pdf_path, msg_pdf = ExportService.generate_pdf(p_id, charter, chapters, sources, chapter_number=1)
    assert ok_pdf is True
    assert os.path.exists(pdf_path)

    pdf_doc = fitz.open(pdf_path)
    page_count = len(pdf_doc)
    full_pdf_text = "".join([page.get_text() for page in pdf_doc])
    pdf_doc.close()

    # Verify that single-chapter export does NOT create 16 pages
    assert page_count <= 5, f"Expected 4-5 pages for single chapter, got {page_count}"
    assert "Chapter 1: Diagnosis and Patient Communication" in full_pdf_text
    assert "Chapter 2: Unwritten Chapter Two" not in full_pdf_text

    # Export Single Chapter DOCX
    ok_docx, docx_path, msg_docx = ExportService.generate_docx(p_id, charter, chapters, sources, chapter_number=1)
    assert ok_docx is True
    assert os.path.exists(docx_path)

    wdoc = docx.Document(docx_path)
    docx_text = "\n".join([p.text for p in wdoc.paragraphs])
    assert "Chapter 1: Diagnosis and Patient Communication" in docx_text
    assert "Chapter 2: Unwritten Chapter Two" not in docx_text

    # Cleanup
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    if os.path.exists(docx_path):
        os.remove(docx_path)
    ProjectService.delete_project(p_id)

