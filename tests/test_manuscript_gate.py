"""FIX 5 regression tests — publication completeness gate."""
import os

from database.db import transaction
from models.schemas import ProjectCreate
from services.export_service import ExportService
from services.manuscript_service import ManuscriptService, STATUS_DRAFT, STATUS_READY
from services.outline_service import OutlineService
from services.project_service import ProjectService


def _make_book(p_id, complete_count=3):
    ids = []
    with transaction() as conn:
        cur = conn.cursor()
        for n in range(1, 12):
            complete = n <= complete_count
            content = ("word " * 1200) if complete else ("" if n > complete_count + 2 else "short draft")
            words = 1200 if complete else (0 if not content else 2)
            cur.execute(
                """
                INSERT INTO chapters (
                    project_id, chapter_number, title, purpose, content, word_count, validation_status
                ) VALUES (?, ?, ?, 'Purpose', ?, ?, 'PENDING')
                """,
                (p_id, n, f"Niche Chapter {n} About Clinic Operations", content, words),
            )
            ids.append(cur.lastrowid)
    return ids


CHARTER = {
    "product_title": "Dental No-Show Recovery Playbook",
    "subtitle": "A 30-Day Reminder System",
    "target_audience": "Independent dental clinic owners",
    "product_category": "Business Education",
    "disclaimer_requirements": ["Educational only."],
}


def test_three_of_eleven_is_a_draft_and_blocks_publication():
    p_id = ProjectService.create_project(ProjectCreate(name="Gate Draft Test"))
    _make_book(p_id, complete_count=3)
    chapters = OutlineService.get_chapters(p_id)

    assessment = ManuscriptService.assess(p_id, chapters, sources=[])
    assert assessment["status"] == STATUS_DRAFT
    assert assessment["can_publish"] is False
    assert assessment["complete_count"] == 3
    assert assessment["total_chapters"] == 11
    assert assessment["label"] == "DRAFT — 3/11 CHAPTERS COMPLETE"
    missing_numbers = {m["chapter"] for m in assessment["missing_chapters"]}
    assert missing_numbers == set(range(4, 12))

    ok, path, msg = ExportService.generate_pdf(
        p_id, CHARTER, chapters, sources=[],
        require_complete=True, allow_partial=False, assessment=assessment,
    )
    assert ok is False
    assert path == ""
    assert "Publication blocked" in msg
    assert "3/11" in msg

    ProjectService.delete_project(p_id)


def test_partial_draft_export_is_allowed_and_clearly_labelled():
    p_id = ProjectService.create_project(ProjectCreate(name="Gate Partial Test"))
    _make_book(p_id, complete_count=3)
    chapters = OutlineService.get_chapters(p_id)
    assessment = ManuscriptService.assess(p_id, chapters, sources=[])

    ok, path, msg = ExportService.generate_pdf(
        p_id, CHARTER, chapters, sources=[],
        require_complete=True, allow_partial=True, assessment=assessment,
    )
    assert ok is True, msg
    assert "_DRAFT" in os.path.basename(path)
    assert "DRAFT — 3/11 CHAPTERS COMPLETE" in msg
    assert os.path.exists(path)
    os.remove(path)

    ProjectService.delete_project(p_id)


def test_single_chapter_export_bypasses_full_book_gate():
    p_id = ProjectService.create_project(ProjectCreate(name="Gate Chapter Test"))
    _make_book(p_id, complete_count=3)
    chapters = OutlineService.get_chapters(p_id)

    ok, path, msg = ExportService.generate_pdf(
        p_id, CHARTER, chapters, sources=[],
        chapter_number=1, require_complete=True, allow_partial=False,
    )
    assert ok is True, msg
    assert os.path.exists(path)
    os.remove(path)

    ProjectService.delete_project(p_id)


def test_all_chapters_complete_and_approved_becomes_publication_ready():
    p_id = ProjectService.create_project(ProjectCreate(name="Gate Ready Test"))
    ids = _make_book(p_id, complete_count=11)
    with transaction() as conn:
        cur = conn.cursor()
        for ch_id in ids:
            cur.execute(
                """
                INSERT INTO quality_reports (
                    project_id, chapter_id, status, score, factual_accuracy_assessment,
                    unsupported_statements_json, repetition_issues_json, readability_score,
                    practical_value_assessment, recommended_corrections_json,
                    disclaimer_flags_json, summary_verdict, human_review_required,
                    deterministic_checks_json
                ) VALUES (?, ?, 'PASS', 95, 'ok', '[]', '[]', 'ok', 'ok', '[]', '[]', 'ok', 0, ?)
                """,
                (p_id, ch_id, '{"counts": {"issues": 0}, "citations_exist": true}'),
            )
    chapters = OutlineService.get_chapters(p_id)
    assessment = ManuscriptService.assess(p_id, chapters, sources=[])
    assert assessment["status"] == STATUS_READY
    assert assessment["can_publish"] is True
    assert assessment["label"].startswith("PUBLICATION READY")
    assert assessment["complete_count"] == 11
    assert assessment["unapproved_chapters"] == []

    ProjectService.delete_project(p_id)


def test_legacy_model_only_pass_cannot_approve_a_chapter():
    """A pre-auditor report with a model PASS but no deterministic checks is unverified."""
    p_id = ProjectService.create_project(ProjectCreate(name="Gate Legacy Audit Test"))
    ids = _make_book(p_id, complete_count=11)
    with transaction() as conn:
        cur = conn.cursor()
        for ch_id in ids:
            cur.execute(
                """
                INSERT INTO quality_reports (
                    project_id, chapter_id, status, score, factual_accuracy_assessment,
                    unsupported_statements_json, repetition_issues_json, readability_score,
                    practical_value_assessment, recommended_corrections_json,
                    disclaimer_flags_json, summary_verdict, human_review_required
                ) VALUES (?, ?, 'PASS', 95, 'model says great', '[]', '[]', 'ok', 'ok', '[]', '[]', 'great', 0)
                """,
                (p_id, ch_id),
            )
    chapters = OutlineService.get_chapters(p_id)
    assessment = ManuscriptService.assess(p_id, chapters, sources=[])
    assert assessment["can_publish"] is False
    assert assessment["unapproved_chapters"] == list(range(1, 12))
    assert any("verified PASS audit" in r for r in assessment["blocking_reasons"])

    ProjectService.delete_project(p_id)


def test_missing_audit_approval_prevents_ready_status():
    p_id = ProjectService.create_project(ProjectCreate(name="Gate Unapproved Test"))
    _make_book(p_id, complete_count=11)  # complete but no PASS audits
    chapters = OutlineService.get_chapters(p_id)
    assessment = ManuscriptService.assess(p_id, chapters, sources=[])
    assert assessment["can_publish"] is False
    assert assessment["complete_count"] == 11
    assert assessment["unapproved_chapters"] == list(range(1, 12))
    assert any("verified PASS audit" in r for r in assessment["blocking_reasons"])

    ProjectService.delete_project(p_id)
