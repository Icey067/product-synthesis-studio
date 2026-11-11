import pytest
from database.db import init_db, get_connection
from services.project_service import ProjectService
from services.research_service import ResearchService
from models.schemas import ProjectCreate
from models.enums import ExpertiseMode, ProductFormat

@pytest.fixture(autouse=True)
def setup_database():
    init_db()

def test_project_lifecycle():
    p_in = ProjectCreate(
        name="Lifecycle Test Project",
        country="United States",
        language="English",
        target_customer="Test Audience",
        expertise_mode=ExpertiseMode.NON_EXPERT,
        product_category="Technology & AI",
        product_format=ProductFormat.EBOOK,
        budget=100.0,
        creator_interests="Unit testing",
    )
    p_id = ProjectService.create_project(p_in)
    assert p_id > 0

    # Retrieve
    proj = ProjectService.get_project(p_id)
    assert proj is not None
    assert proj["name"] == "Lifecycle Test Project"

    # Update
    ProjectService.update_project(p_id, {"status": "IN_PROGRESS", "research_progress": 40})
    proj_updated = ProjectService.get_project(p_id)
    assert proj_updated["status"] == "IN_PROGRESS"
    assert proj_updated["research_progress"] == 40

    # Duplicate
    dup_id = ProjectService.duplicate_project(p_id)
    assert dup_id is not None
    dup_proj = ProjectService.get_project(dup_id)
    assert "Copy" in dup_proj["name"]

    # Delete
    assert ProjectService.delete_project(dup_id) is True
    assert ProjectService.get_project(dup_id) is None

def test_research_duplicate_detection():
    p_in = ProjectCreate(
        name="Research Deduplication Test",
        target_customer="Test Audience",
    )
    p_id = ProjectService.create_project(p_in)

    content = "Unique factual research text discussing market penetration rates."
    ok1, id1, msg1 = ResearchService.add_source(p_id, "Source One", "TXT", content)
    assert ok1 is True

    # Attempt to insert identical content in the same project
    ok2, id2, msg2 = ResearchService.add_source(p_id, "Source Two", "TXT", content)
    assert ok2 is False
    assert "Duplicate content detected" in msg2
    assert id2 == id1

    # Clean up
    ProjectService.delete_project(p_id)

