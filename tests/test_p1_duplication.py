"""P1 regression: project duplication copies the complete project graph."""
from pathlib import Path

from config.settings import OUTPUT_DIR
from database.db import get_connection, transaction
from models.schemas import ProjectCreate
from services.autosave_service import AutosaveService
from services.project_service import ProjectService
from services.research_service import ResearchService

PROJECT_TABLES = [
    "research_batches",
    "research_sources",
    "research_chunks",
    "research_findings",
    "opportunities",
    "competitors",
    "transformation_maps",
    "product_charters",
    "product_outlines",
    "chapters",
    "quality_reports",
    "brands",
    "exports",
    "sales_pages",
    "distribution_plans",
    "analytics_records",
    "chapter_autosaves",
]


def _count(project_id: int, table: str) -> int:
    with get_connection() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project_id,)
        ).fetchone()[0]


def _scalar(query: str, params=()):
    with get_connection() as conn:
        return conn.execute(query, params).fetchone()[0]


def _build_rich_project(name: str):
    project_id = ProjectService.create_project(ProjectCreate(name=name))

    ok, source_id, message = ResearchService.add_source(
        project_id,
        "Dental operations survey",
        "REPORT",
        (
            "An analysis of 350 independent dental clinics found that the average no-show "
            "rate is 14.8%, costing a practice about 48000 dollars annually. " * 20
        ),
        publisher="Dental Association",
    )
    assert ok, message

    export_file = Path(OUTPUT_DIR) / f"duplication_source_{project_id}.pdf"
    export_file.write_bytes(b"%PDF-1.4 duplicated export artifact")

    with transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO opportunities (
                project_id, name, exact_customer, customer_problem, problem_urgency,
                desired_outcome, demand_evidence, potential_solution
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "Reduce dental no-shows",
                "Dental clinic owners",
                "Missed appointments waste chair time.",
                "High",
                "Lower no-show rate",
                "Survey measured 14.8% no-shows",
                "Reminder playbook",
            ),
        )
        opportunity_id = cur.lastrowid
        cur.execute(
            "UPDATE projects SET selected_opportunity_id = ? WHERE id = ?",
            (opportunity_id, project_id),
        )
        cur.execute(
            """
            INSERT INTO opportunity_evidence (
                opportunity_id, source_id, claim_field, claim_text, excerpt,
                relevance_score, verified, verification_notes
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                opportunity_id,
                source_id,
                "demand_evidence",
                "No-shows cost practices money.",
                "An analysis of 350 independent dental clinics found that the average no-show",
                0.9,
                "Stored evidence",
            ),
        )
        cur.execute(
            """
            INSERT INTO opportunity_scores (
                opportunity_id, weighted_score, demand_score, wtp_score, urgency_score,
                gap_score, specificity_score, solvability_score, feasibility_score,
                evidence_confidence, verified_source_count, verified_field_count
            ) VALUES (?, 72.0, 8, 7, 9, 6, 8, 7, 8, 'MEDIUM', 1, 2)
            """,
            (opportunity_id,),
        )
        cur.execute(
            "INSERT INTO competitors (project_id, product_name, brand) VALUES (?, ?, ?)",
            (project_id, "Competitor Book", "Competitor Brand"),
        )
        cur.execute(
            """
            INSERT INTO transformation_maps (project_id, before_json, after_json, milestones_json)
            VALUES (?, '{"a": 1}', '{"b": 2}', '[]')
            """,
            (project_id,),
        )
        cur.execute(
            """
            INSERT INTO product_charters (
                project_id, product_title, subtitle, product_category, target_audience,
                customer_problem, core_promise, desired_outcome, unique_mechanism,
                differentiation, product_description, deliverables_json, evidence_base,
                product_limitations_json, scope_exclusions_json, disclaimer_requirements_json
            ) VALUES (?, 'Title', 'Subtitle', 'Business', 'Dental owners', 'No-shows',
                      'Fewer no-shows', 'Reliable schedule', 'SMS reminders', 'Practical',
                      'A playbook', '[]', 'Survey', '[]', '[]', '[]')
            """,
            (project_id,),
        )
        cur.execute(
            "INSERT INTO product_outlines (project_id) VALUES (?)", (project_id,)
        )
        cur.execute(
            """
            INSERT INTO chapters (project_id, chapter_number, title, content, word_count)
            VALUES (?, 1, 'Introduction', 'Published chapter text.', 3)
            """,
            (project_id,),
        )
        chapter_id = cur.lastrowid
        cur.execute(
            """
            INSERT INTO chapter_revisions (chapter_id, revision_number, content, word_count, notes)
            VALUES (?, 1, 'Older revision text.', 3, 'Draft')
            """,
            (chapter_id,),
        )
        cur.execute(
            """
            INSERT INTO quality_reports (project_id, chapter_id, status, score)
            VALUES (?, ?, 'PASS', 91)
            """,
            (project_id, chapter_id),
        )
        cur.execute(
            "INSERT INTO brands (project_id, brand_name) VALUES (?, 'Brand')",
            (project_id,),
        )
        cur.execute(
            """
            INSERT INTO exports (
                project_id, export_type, file_path, file_size_bytes, completeness_status
            ) VALUES (?, 'PDF', ?, ?, 'COMPLETED_MANUSCRIPT')
            """,
            (project_id, str(export_file), export_file.stat().st_size),
        )
        cur.execute(
            """
            INSERT INTO sales_pages (project_id, headline, subheadline)
            VALUES (?, 'Headline', 'Subheadline')
            """,
            (project_id,),
        )
        cur.execute(
            "INSERT INTO distribution_plans (project_id, community_plan) VALUES (?, 'Plan')",
            (project_id,),
        )
        cur.execute(
            "INSERT INTO analytics_records (project_id, record_date, sales) VALUES (?, '2025-01-01', 3)",
            (project_id,),
        )
        cur.execute(
            """
            INSERT INTO research_findings (project_id, source_id, finding_text)
            VALUES (?, ?, 'A finding')
            """,
            (project_id, source_id),
        )

    AutosaveService.save_draft(chapter_id, project_id, "Recoverable draft text")

    return project_id, source_id, opportunity_id, chapter_id, export_file


def test_duplicate_project_copies_all_tables_and_remaps_foreign_keys():
    project_id, source_id, opportunity_id, chapter_id, export_file = _build_rich_project(
        "Rich Duplication Test"
    )

    duplicate_id = ProjectService.duplicate_project(project_id)
    assert duplicate_id is not None
    assert duplicate_id != project_id

    for table in PROJECT_TABLES:
        assert _count(duplicate_id, table) == _count(project_id, table), (
            f"{table} was not duplicated completely"
        )

    duplicate_project = ProjectService.get_project(duplicate_id)
    assert "Copy" in duplicate_project["name"]

    # selected_opportunity_id must point at the duplicated opportunity.
    selected_id = duplicate_project["selected_opportunity_id"]
    assert selected_id is not None and selected_id != opportunity_id
    with get_connection() as conn:
        selected = conn.execute(
            "SELECT * FROM opportunities WHERE id = ?", (selected_id,)
        ).fetchone()
    assert selected["project_id"] == duplicate_id
    assert selected["name"] == "Reduce dental no-shows"

    # Evidence must reference the duplicated source, not the original.
    with get_connection() as conn:
        evidence = conn.execute(
            "SELECT * FROM opportunity_evidence WHERE opportunity_id = ?",
            (selected_id,),
        ).fetchone()
        duplicate_source = conn.execute(
            "SELECT * FROM research_sources WHERE id = ?", (evidence["source_id"],)
        ).fetchone()
        duplicate_score = conn.execute(
            "SELECT * FROM opportunity_scores WHERE opportunity_id = ?",
            (selected_id,),
        ).fetchone()
    assert duplicate_source["id"] != source_id
    assert duplicate_source["project_id"] == duplicate_id
    assert duplicate_score["weighted_score"] == 72.0

    # Revisions and autosaves must hang off the duplicated chapter.
    duplicate_chapter = _scalar(
        "SELECT id FROM chapters WHERE project_id = ?", (duplicate_id,)
    )
    assert duplicate_chapter != chapter_id
    assert _scalar(
        "SELECT COUNT(*) FROM chapter_revisions WHERE chapter_id = ?", (duplicate_chapter,)
    ) == 1
    assert _scalar(
        "SELECT COUNT(*) FROM chapter_autosaves WHERE chapter_id = ?", (duplicate_chapter,)
    ) == 1

    # Exported artifacts are physically copied and independent.
    with get_connection() as conn:
        export_row = conn.execute(
            "SELECT * FROM exports WHERE project_id = ?", (duplicate_id,)
        ).fetchone()
    assert export_row["file_path"] != str(export_file)
    assert Path(export_row["file_path"]).exists()

    # The duplicate is independently editable.
    with transaction() as conn:
        conn.execute(
            "UPDATE chapters SET content = 'Edited duplicate' WHERE id = ?",
            (duplicate_chapter,),
        )
    assert _scalar("SELECT content FROM chapters WHERE id = ?", (chapter_id,)) == "Published chapter text."

    original = ProjectService.get_project(project_id)
    assert original["selected_opportunity_id"] == opportunity_id

    original_export = Path(export_file)
    duplicate_export = Path(export_row["file_path"])
    ProjectService.delete_project(project_id)
    ProjectService.delete_project(duplicate_id)
    for path in (original_export, duplicate_export):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
