"""FIX 2 regression tests — product-format enforcement."""
from unittest.mock import MagicMock

from database.db import get_connection
from models.enums import ProductFormat
from models.schemas import ProductCharter, ProjectCreate
from services.charter_service import CharterService
from services.generation_utils import generate_with_validation
from services.ollama_client import OllamaClient
from services.product_format_service import (
    validate_charter,
    validate_chapter,
    validate_outline,
    validate_sales,
    detect_software_claims,
)
from services.project_service import ProjectService


GOOD_CHARTER = {
    "product_title": "The Dental No-Show Recovery Playbook",
    "subtitle": "A 30-Day Reminder System for Independent Clinics",
    "product_category": "Business Education",
    "target_audience": "Independent dental clinic owners",
    "customer_problem": "Patients miss appointments and waste chair time.",
    "core_promise": "Cut no-shows with a step-by-step reminder playbook.",
    "desired_outcome": "A working reminder process within 30 days.",
    "unique_mechanism": "Two-touch reminder scripts and a scheduling worksheet.",
    "differentiation": "Written specifically for two-chair clinics.",
    "product_description": "A readable ebook with chapters, scripts and worksheets.",
    "deliverables": ["Dental no-show playbook (PDF ebook)", "Printable reminder script worksheets"],
    "evidence_base": "Clinic operations research.",
    "product_limitations": ["Results vary by clinic."],
    "scope_exclusions": ["No software installation required."],
    "disclaimer_requirements": ["Educational only."],
}

BAD_CHARTER = {
    **GOOD_CHARTER,
    "product_title": "Dental Clinic Mobile App",
    "core_promise": "Our new app tracks every patient automatically.",
    "deliverables": ["Mobile app download", "Push notification dashboard"],
}


def test_ebook_rejects_software_primary_deliverable():
    ok, issues = validate_charter("Ebook", BAD_CHARTER)
    assert ok is False
    codes = {i["code"] for i in issues}
    assert "format_software_deliverable" in codes or "format_software_promise" in codes


def test_ebook_accepts_readable_book_charter():
    ok, issues = validate_charter("Ebook", GOOD_CHARTER)
    assert ok is True, issues


def test_format_detector_is_narrow_enough():
    # Ordinary "application of a method" must not be treated as software.
    assert detect_software_claims("Apply the application of this method daily.") == []
    assert detect_software_claims("Download the app from the app store.")


def test_outline_rejects_generic_template_titles():
    chapters = [
        {"chapter_number": i + 1, "title": title}
        for i, title in enumerate([
            "Introduction and The Problem",
            "Customer Diagnosis & Self-Assessment",
            "Foundations and Preparation",
            "The Core Solution Framework",
            "Practical Implementation Step 1",
            "Practical Implementation Step 2",
            "Practical Implementation Step 3",
            "Tools, Templates, and Worksheets",
            "Mistakes, Pitfalls, and Troubleshooting",
            "Case Studies and Practical Applications",
            "30-Day Roadmap and Next Steps",
        ])
    ]
    ok, issues = validate_outline("Ebook", chapters)
    assert ok is False
    assert any(i["code"] == "outline_generic_titles" for i in issues)


def test_outline_accepts_niche_specific_titles():
    chapters = [
        {"chapter_number": i + 1, "title": title}
        for i, title in enumerate([
            "Why Two-Chair Dental Clinics Lose $48,000 to No-Shows",
            "Auditing Last Month's Cancellation Log",
            "Writing the 48-Hour SMS Reminder Script",
            "Handling Same-Day Cancellations Without Double-Booking",
            "Training the Front Desk on Reminder Calls",
            "Designing a Deposit Policy Patients Accept",
            "Filling Empty Slots from a Standby List",
            "Measuring No-Show Rate Week by Week",
            "What to Do When a Patient Ignores Three Reminders",
            "Building the 30-Day Reminder Calendar",
            "Scaling the System to a Second Location",
        ])
    ]
    ok, issues = validate_outline("Ebook", chapters)
    assert ok is True, issues


def test_chapter_rejects_software_instructions_in_ebook():
    ok, issues = validate_chapter("Ebook", "Step 1: Download the app and create an account to track patients.")
    assert ok is False
    assert any(i["code"] == "chapter_software_promise" for i in issues)


def test_sales_copy_rejects_app_promise_for_ebook():
    ok, issues = validate_sales("Ebook", {
        "headline": "The dental app that ends no-shows",
        "subheadline": "Download the mobile application today.",
        "solution_introduction": "A simple app.",
        "whats_included": ["App access"],
        "core_benefits": ["Automatic reminders"],
    })
    assert ok is False
    assert any(i["code"] == "sales_software_promise" for i in issues)


def test_generation_regenerates_with_corrective_instructions():
    mock = MagicMock(spec=OllamaClient)
    bad = ProductCharter(**BAD_CHARTER)
    good = ProductCharter(**GOOD_CHARTER)
    mock.generate_structured.side_effect = [
        (True, bad, "ok"),
        (True, good, "ok"),
    ]

    ok, validated, msg, issues = generate_with_validation(
        mock,
        prompt="Generate a charter.",
        schema=ProductCharter,
        validator=validate_charter,
        product_format="Ebook",
        stage="Product Charter",
        max_attempts=2,
    )
    assert ok is True
    assert validated.product_title == GOOD_CHARTER["product_title"]
    # The retry prompt must explicitly carry corrective instructions.
    retry_prompt = mock.generate_structured.call_args_list[1].kwargs["prompt"]
    assert "CORRECTION REQUIRED" in retry_prompt


def test_charter_service_persists_product_format():
    p_id = ProjectService.create_project(
        ProjectCreate(name="Format Persistence Test", product_format=ProductFormat.EBOOK)
    )
    mock = MagicMock(spec=OllamaClient)
    mock.generate_structured.return_value = (True, ProductCharter(**GOOD_CHARTER), "ok")

    service = CharterService(mock)
    project = ProjectService.get_project(p_id)
    ok, data, msg = service.generate_charter(project)
    assert ok, msg

    with get_connection() as conn:
        row = conn.execute(
            "SELECT product_format FROM product_charters WHERE project_id = ?", (p_id,)
        ).fetchone()
    assert row is not None
    assert row["product_format"] == "Ebook"

    ProjectService.delete_project(p_id)
