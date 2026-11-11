"""FIX 3 regression tests — niche outlines and chapter contracts."""
from unittest.mock import MagicMock

from database.db import get_connection, transaction
from models.schemas import ProjectCreate
from services.chapter_contract import (
    analyze_chapter,
    count_words,
    deficiencies_for_expansion,
    detect_sections,
)
from services.generation_service import GenerationService
from services.ollama_client import OllamaClient
from services.project_service import ProjectService


def _complete_content() -> str:
    return (
        "## Introduction\n"
        "Independent two-chair dental clinics lose revenue whenever a patient quietly skips a scheduled visit. "
        "This chapter explains how to see the real cost of those empty chairs and why a written reminder process "
        "beats informal phone calls that staff forget during busy mornings. "
        "We will connect each step to the clinic schedule so the change survives a busy week.\n\n"
        "## Step-by-Step Implementation\n"
        "1. Export the last thirty days of cancellations and mark every no-show.\n"
        "2. Calculate lost chair minutes by multiplying missed visits by appointment length.\n"
        "3. Write a two-touch SMS script for forty-eight hours and two hours before the visit.\n"
        "4. Assign one front-desk owner and a backup for every working day.\n"
        "5. Review the weekly no-show rate every Friday morning. [Source-1]\n\n"
        "## Practical Example\n"
        "A clinic with eighteen daily appointments and a fourteen percent no-show rate recovers two chairs "
        "per day by confirming every visit twice. The front desk logs confirmations in the same scheduling "
        "worksheet and calls only the patients who do not reply to the message.\n\n"
        "## Common Mistakes & High-Cost Traps\n"
        "- Sending one reminder only, which patients ignore during commute hours.\n"
        "- Letting every staff member own the process, so nobody checks the log.\n"
        "- Discounting a deposit policy before testing a simple reminder script.\n"
        "- Measuring feelings instead of the actual weekly no-show percentage.\n\n"
        "## Worksheet / Exercise\n"
        "- [ ] List last month's no-show count and the lost chair minutes.\n"
        "- [ ] Write your forty-eight-hour reminder message.\n"
        "- [ ] Name the front-desk owner for each weekday.\n"
        "- [ ] Choose the Friday review time and the metric you will track.\n\n"
        "## Chapter Summary & 24-Hour Action Step\n"
        "A reliable reminder loop protects chair time without hiring staff. Today, export last month's "
        "cancellations, calculate the lost minutes, and send the first two-touch reminder to tomorrow's "
        "patients. Record the result so next week's review has a real baseline to compare against."
    )


def test_word_count_is_actual():
    assert count_words("one two three four") == 4
    assert count_words("") == 0


def test_section_detection():
    sections = detect_sections(_complete_content())
    assert all(sections.values()), sections


def test_complete_chapter_passes_contract():
    report = analyze_chapter(
        _complete_content(), target=200, source_ids=[1], product_format="Ebook"
    )
    assert report["status"] == "COMPLETE", report["issues"]
    assert report["word_count"] >= report["min_words"]
    assert report["missing_sections"] == []
    assert report["citation_ids"] == [1]
    assert report["format_ok"] is True


def test_incomplete_chapter_reports_missing_sections_and_words():
    short = "## Introduction\nThis is far too short and has no other required sections. [Source-1]"
    report = analyze_chapter(short, target=2200, source_ids=[1], product_format="Ebook")
    assert report["status"] == "INCOMPLETE"
    assert "step_by_step" in report["missing_sections"]
    assert report["word_count"] < report["min_words"]
    deficiencies = deficiencies_for_expansion(report)
    assert "Length" in deficiencies
    assert "Missing section" in deficiencies


def test_invalid_citation_is_detected():
    content = _complete_content().replace("[Source-1]", "[Source-99]")
    report = analyze_chapter(content, target=200, source_ids=[1], product_format="Ebook")
    assert report["status"] == "INCOMPLETE"
    assert report["invalid_citations"] == [99]


def test_generation_does_not_pretend_incomplete_chapter_is_complete():
    mock = MagicMock(spec=OllamaClient)
    mock.generate.return_value = (True, "## Introduction\nToo short to pass. [Source-1]")

    service = GenerationService(mock)
    chapter = {"chapter_number": 1, "title": "Reminder Scripts", "purpose": "Write scripts"}
    ok, content, report, msg = service.generate_chapter_validated(
        chapter=chapter,
        charter={"product_title": "Playbook"},
        evidence_sources=[{"id": 1, "raw_content": "Clinic reminder evidence."}],
        target=400,
        max_expansions=2,
    )
    assert ok is True
    assert report["status"] == "INCOMPLETE"
    assert "Still incomplete" in msg


def test_expansion_recovers_a_deficient_chapter():
    mock = MagicMock(spec=OllamaClient)
    mock.generate.side_effect = [
        (True, "## Introduction\nToo short to pass. [Source-1]"),
        (True, _complete_content()),
    ]
    service = GenerationService(mock)
    chapter = {"chapter_number": 1, "title": "Reminder Scripts", "purpose": "Write scripts"}
    ok, content, report, msg = service.generate_chapter_validated(
        chapter=chapter,
        charter={"product_title": "Playbook"},
        evidence_sources=[{"id": 1, "raw_content": "Clinic reminder evidence."}],
        target=200,
        max_expansions=1,
    )
    assert ok is True
    assert report["status"] == "COMPLETE"
    assert "## Worksheet / Exercise" in content


def test_save_preserves_revisions_and_refuses_locked_chapter():
    p_id = ProjectService.create_project(ProjectCreate(name="Revision Safety Test"))
    with transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO chapters (project_id, chapter_number, title, purpose, content, word_count)
            VALUES (?, 1, 'Revision Chapter', 'Purpose', 'ORIGINAL CONTENT', 2)
            """,
            (p_id,),
        )
        ch_id = cur.lastrowid

    report = {"status": "COMPLETE", "target_words": 200, "word_count": 4}
    ok = GenerationService.save_chapter_content(ch_id, "REVISED CONTENT ONE TWO", validation=report)
    assert ok is True
    ok2 = GenerationService.save_chapter_content(ch_id, "REVISED AGAIN", validation=report)
    assert ok2 is True

    with get_connection() as conn:
        revs = conn.execute(
            "SELECT * FROM chapter_revisions WHERE chapter_id = ? ORDER BY id", (ch_id,)
        ).fetchall()
        content = conn.execute(
            "SELECT content, validation_status FROM chapters WHERE id = ?", (ch_id,)
        ).fetchone()
    assert len(revs) == 2
    assert content["content"] == "REVISED AGAIN"
    assert content["validation_status"] == "COMPLETE"

    GenerationService.set_lock_status(ch_id, True)
    blocked = GenerationService.save_chapter_content(ch_id, "SHOULD NOT SAVE")
    assert blocked is False
    with get_connection() as conn:
        final = conn.execute("SELECT content FROM chapters WHERE id = ?", (ch_id,)).fetchone()
    assert final["content"] == "REVISED AGAIN"

    ProjectService.delete_project(p_id)
