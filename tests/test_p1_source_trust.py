"""P1 source-trust regressions.

A user-declared reliability label must never influence the independent
assessment or the credibility used by evidence verification.
"""
from database.db import get_connection
from models.schemas import ProjectCreate
from services.project_service import ProjectService
from services.research_service import ResearchService
from services.source_trust_service import SourceTrustService, assess_source


def _project(name: str) -> int:
    return ProjectService.create_project(ProjectCreate(name=name))


def _row(source_id: int) -> dict:
    with get_connection() as conn:
        return dict(
            conn.execute("SELECT * FROM research_sources WHERE id = ?", (source_id,)).fetchone()
        )


def test_new_source_defaults_to_unassessed_declaration():
    project_id = _project("Trust Default Test")
    ok, source_id, message = ResearchService.add_source(
        project_id,
        "Manual field note",
        "MANUAL_NOTE",
        "A short observation about front-desk scheduling behaviour.",
    )
    assert ok, message
    row = _row(source_id)
    assert row["declared_reliability"] == "UNASSESSED"
    # An unstructured note without a provenance URL cannot be assessed as HIGH.
    assert row["assessed_reliability"] in {"UNASSESSED", "LOW"}
    ProjectService.delete_project(project_id)


def test_declared_label_never_changes_assessment():
    base = {
        "title": "A casual opinion blog post",
        "publisher": "Unknown",
        "source_category": "PUBLIC_URL",
        "url": "https://example.com/opinion",
        "raw_content": "This blog contains personal opinions and no methodology or data.",
    }
    declared_high = assess_source({**base, "declared_reliability": "HIGH"})
    declared_low = assess_source({**base, "declared_reliability": "LOW"})

    assert declared_high["assessed_reliability"] == declared_low["assessed_reliability"]
    assert declared_high["assessment_score"] == declared_low["assessment_score"]
    assert declared_high["declared_reliability"] == "HIGH"
    assert "not used" in declared_high["assessment_rationale"].lower()


def test_official_dataset_assessed_higher_than_opinion_blog():
    official = assess_source(
        {
            "title": "Census Bureau annual business survey dataset",
            "publisher": "U.S. Census Bureau",
            "source_category": "DATASET",
            "url": "https://www.census.gov/data/business-survey",
            "raw_content": (
                "Methodology: sample size 12,000 respondents. "
                + "The survey measured annual revenue and employment. " * 100
            ),
        }
    )
    blog = assess_source(
        {
            "title": "My thoughts on business",
            "publisher": "Unknown",
            "source_category": "PUBLIC_URL",
            "url": "https://example.com/blog",
            "raw_content": "I think business is hard and revenue matters.",
        }
    )
    assert SourceTrustService.credibility_rank(
        official["assessed_reliability"]
    ) > SourceTrustService.credibility_rank(blog["assessed_reliability"])


def test_assess_and_store_persists_independent_assessment():
    project_id = _project("Assess Store Test")
    ok, source_id, message = ResearchService.add_source(
        project_id,
        "World Bank enterprise survey",
        "REPORT",
        "Methodology: sample size 500 respondents. " * 40,
        publisher="World Bank",
        url="https://www.worldbank.org/enterprise-survey",
    )
    assert ok, message

    assessment = SourceTrustService.assess_and_store(source_id)
    assert assessment is not None
    row = _row(source_id)
    assert row["assessed_reliability"] == assessment["assessed_reliability"]
    assert row["assessment_score"] == assessment["assessment_score"]
    assert row["assessed_at"]
    ProjectService.delete_project(project_id)


def test_assess_project_sources_updates_every_source():
    project_id = _project("Assess Project Test")
    for index in range(3):
        ResearchService.add_source(
            project_id,
            f"Report {index}",
            "REPORT",
            f"Survey report {index} with methodology and sample size {100 + index}. " * 20,
            publisher="OECD",
            url="https://www.oecd.org/report",
        )
    count = SourceTrustService.assess_project_sources(project_id)
    assert count == 3
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT assessed_reliability FROM research_sources WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    assert all(row["assessed_reliability"] != "UNASSESSED" for row in rows)
    ProjectService.delete_project(project_id)
