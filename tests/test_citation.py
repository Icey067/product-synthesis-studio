import pytest
from services.citation_service import CitationService
from services.research_service import ResearchService
from services.project_service import ProjectService
from models.schemas import ProjectCreate

def test_citation_extraction():
    sample_text = (
        "According to empirical findings [Source-1], footfall increases by 22%. "
        "Furthermore, recent surveys [Source-2] and follow-up studies [Ref-3] confirmed this trend. "
        "Duplicate references like [Source-1] should not produce duplicate IDs."
    )
    ids = CitationService.extract_citations(sample_text)
    assert ids == [1, 2, 3]

def test_citation_verification_against_sqlite():
    p_id = ProjectService.create_project(ProjectCreate(name="Citation Test Project"))
    
    # Ingest 1 real source
    ok, s_id, _ = ResearchService.add_source(
        project_id=p_id,
        title="Verified Medical Operations Study",
        source_category="STUDY",
        content_text="Detailed examination of clinic patient appointment cancellation patterns and remedies.",
        publisher="Medical Operations Board"
    )
    assert ok is True

    # Test chapter text with 1 valid citation and 1 invalid citation [Source-999]
    chapter_text = f"As proven in [Source-{s_id}], cancellations drop sharply. However, fabricated claim [Source-999] is invalid."
    report = CitationService.verify_chapter_citations(p_id, chapter_text)

    assert report["total_citations"] == 2
    assert len(report["valid_citations"]) == 1
    assert report["valid_citations"][0]["source_id"] == s_id
    assert 999 in report["invalid_citations"]
    assert report["is_clean"] is False
    assert report["status"] == "FAIL"

    # Clean text with only valid citations
    clean_text = f"Confirmed by [Source-{s_id}], automated reminders reduce clinic no-shows."
    clean_report = CitationService.verify_chapter_citations(p_id, clean_text)
    assert clean_report["is_clean"] is True
    assert clean_report["status"] == "PASS"

    ProjectService.delete_project(p_id)

def test_fake_url_detection():
    text_with_fake = "Check out our fake survey at https://www.fakeurl.com/data for proof."
    p_id = ProjectService.create_project(ProjectCreate(name="Fake URL Test"))
    report = CitationService.verify_chapter_citations(p_id, text_with_fake)
    assert len(report["suspicious_urls"]) > 0
    assert report["is_clean"] is False
    ProjectService.delete_project(p_id)

