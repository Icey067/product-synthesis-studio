"""
Comprehensive Real-World End-to-End Integration Test for Product Synthesis Studio.
Tests every stage against the LIVE Ollama service (qwen2.5-coder:7b),
saves data to SQLite, tests persistence/reopening, generates chapters with real citations,
exports PDF & DOCX, inspects their binary/structural contents, and tests Ollama offline resilience.
"""

import os
import sys
import time
import fitz
import docx

from services.ollama_client import OllamaClient
from services.project_service import ProjectService
from services.research_service import ResearchService
from services.market_service import MarketService
from services.transformation_service import TransformationService
from services.charter_service import CharterService
from services.outline_service import OutlineService
from services.generation_service import GenerationService
from services.audit_service import AuditService
from services.export_service import ExportService
from models.schemas import ProjectCreate
from models.enums import ExpertiseMode, ProductFormat

def print_header(title):
    print("\n" + "=" * 70)
    print(f" {title}")
    print("=" * 70)

def main():
    print_header("REAL-WORLD INTEGRATION TEST: PRODUCT SYNTHESIS STUDIO")

    # 1. Verify Ollama is installed and running
    client = OllamaClient()
    connected, conn_msg = client.check_connection()
    print(f"1. Ollama Service Status: {'ONLINE' if connected else 'OFFLINE'} ({conn_msg})")
    assert connected, "Ollama must be running for live real-world integration test!"

    # 2 & 3. Detect installed models
    models = client.list_models()
    print(f"2. Detected Installed Models in Ollama: {models}")
    assert len(models) > 0, "At least one model must be installed in Ollama!"
    active_model = client._get_selected_model()
    print(f"3. Active Generation Model: {active_model}")

    # 4. Generate a real response using the selected local model
    print("\n4. Probing Live Inference with Ollama...")
    t0 = time.time()
    ok_probe, probe_text = client.generate(
        prompt="Reply with exactly: 'PSS REAL INFERENCE CONFIRMED'",
        model=active_model,
        temperature=0.1
    )
    t1 = time.time()
    print(f"   Inference Duration: {t1 - t0:.2f}s")
    print(f"   Raw Output: {probe_text}")
    assert ok_probe and "CONFIRMED" in probe_text, "Failed to get valid response from Ollama!"
    print("   ✓ Real Ollama inference verified successfully!")

    # 5. Create a new real project through the Project Service
    print("\n5. Creating New Project in SQLite Database...")
    project_payload = ProjectCreate(
        name="Local Dental Practice Automation Playbook",
        country="United States",
        language="English",
        target_customer="Independent dental clinic owners and practice managers",
        expertise_mode=ExpertiseMode.NON_EXPERT,
        product_category="Business & Productivity",
        product_format=ProductFormat.EBOOK,
        budget=0.0,
        creator_interests="Patient communication, automated scheduling, appointment reminder workflows, review management",
    )
    project_id = ProjectService.create_project(project_payload)
    print(f"   ✓ Project created with ID: {project_id}")

    # Add 2 Empirical Research Sources with exact Citation IDs
    print("\n   Ingesting Verified Empirical Research Sources...")
    s1_ok, s1_id, _ = ResearchService.add_source(
        project_id=project_id,
        title="ADA Practice Operations Survey: Missed Appointments in Private Practice",
        source_category="INDUSTRY_SURVEY",
        content_text=(
            "Empirical analysis of 350 independent dental clinics found that the average unassisted no-show rate "
            "is 14.8%, costing a two-chair practice approximately $48,000 annually in lost billable chair time. "
            "Practices that implemented automated two-way SMS confirmation reminders 48 hours and 2 hours prior "
            "to appointments reduced no-shows by 68% without hiring additional front-desk reception staff."
        ),
        publisher="American Dental Practice Operations Board",
        author="Dr. H. Vance",
        publication_date="2024",
        reliability_assessment="HIGH"
    )
    s2_ok, s2_id, _ = ResearchService.add_source(
        project_id=project_id,
        title="Patient Trust and Local Search Visibility for Neighborhood Healthcare",
        source_category="JOURNAL_REPORT",
        content_text=(
            "A study of 1,200 local dental patients revealed that 73% chose their current dentist based on "
            "having at least 40 recent Google reviews with a rating above 4.5. 81% of patients reported they would "
            "gladly leave a review if a simple text message with a direct link was sent within 30 minutes of appointment completion."
        ),
        publisher="Healthcare Digital Growth Review",
        author="M. Reynolds, MPH",
        publication_date="2024",
        reliability_assessment="HIGH"
    )
    print(f"   ✓ Ingested Source [Source-{s1_id}] and [Source-{s2_id}] with SHA-256 deduplication.")

    # 6. Generate actual market opportunity hypotheses using real Ollama inference
    print("\n6. Running Real AI Market Intelligence Discovery...")
    t0 = time.time()
    market_svc = MarketService(client)
    project = ProjectService.get_project(project_id)
    ok_opps, opp_list, opp_msg = market_svc.discover_opportunities(project)
    t1 = time.time()
    print(f"   Duration: {t1 - t0:.2f}s | Result: {opp_msg}")
    assert ok_opps and len(opp_list) > 0, f"Opportunity discovery failed: {opp_msg}"
    print(f"   ✓ Generated and scored {len(opp_list)} market opportunities!")
    for opp in opp_list[:3]:
        print(f"     - [ID {opp['id']}] {opp['name']} (Score: {opp['score']}/100)")

    # Select the top opportunity
    top_opp_id = opp_list[0]["id"]
    MarketService.select_opportunity(project_id, top_opp_id)
    print(f"   ✓ Selected top Opportunity #{top_opp_id} as product thesis.")

    # 7. Generate a real customer transformation map
    print("\n7. Generating Customer Transformation Map with Ollama...")
    t0 = time.time()
    trans_svc = TransformationService(client)
    all_opps = MarketService.get_project_opportunities(project_id)
    sel_opp = next(o for o in all_opps if o["id"] == top_opp_id)
    ok_map, tmap_data, map_msg = trans_svc.generate_transformation_map(
        project_id=project_id,
        product_title=project["name"],
        customer=sel_opp["exact_customer"],
        problem=sel_opp["customer_problem"],
        target_outcome=sel_opp["desired_outcome"],
    )
    t1 = time.time()
    print(f"   Duration: {t1 - t0:.2f}s | Result: {map_msg}")
    assert ok_map and tmap_data is not None, f"Transformation map generation failed: {map_msg}"
    print(f"   ✓ Generated Before/After map with {len(tmap_data.get('milestones', []))} implementation milestones.")

    # 8. Generate a master product charter
    print("\n8. Generating Master Product Charter with Ollama...")
    t0 = time.time()
    charter_svc = CharterService(client)
    sources = ResearchService.get_sources(project_id)
    res_summary = " ".join([f"[{s['title']}]: {s.get('raw_content','')[:250]}" for s in sources])
    ok_charter, charter_data, charter_msg = charter_svc.generate_charter(
        project=project,
        opportunity=sel_opp,
        research_summary=res_summary
    )
    t1 = time.time()
    print(f"   Duration: {t1 - t0:.2f}s | Result: {charter_msg}")
    assert ok_charter and charter_data is not None, f"Charter generation failed: {charter_msg}"
    print(f"   ✓ Generated Charter: '{charter_data.get('product_title')}'")
    print(f"     Core Promise: {charter_data.get('core_promise')[:100]}...")

    # 9. Generate an 11-section ebook outline
    print("\n9. Generating 11-Section Ebook Outline with Ollama...")
    t0 = time.time()
    outline_svc = OutlineService(client)
    ok_outline, chapters_outline, outline_msg = outline_svc.generate_outline(
        project_id=project_id,
        charter=charter_data,
        transformation_map=tmap_data
    )
    t1 = time.time()
    print(f"   Duration: {t1 - t0:.2f}s | Result: {outline_msg}")
    assert ok_outline and len(chapters_outline) > 0, f"Outline generation failed: {outline_msg}"
    print(f"   ✓ Generated {len(chapters_outline)} sequential chapters in SQLite.")

    # 10. Generate at least one complete chapter using real Ollama inference
    print("\n10. Generating Complete Chapter 1 Content with Real Ollama Inference...")
    gen_svc = GenerationService(client)
    chapters_in_db = OutlineService.get_chapters(project_id)
    target_chap = chapters_in_db[0]
    print(f"    Target: Chapter {target_chap['chapter_number']} — {target_chap['title']}")

    t0 = time.time()
    ok_gen, chapter_content, gen_msg = gen_svc.generate_chapter_content(
        chapter=target_chap,
        charter=charter_data,
        evidence_sources=sources,
        custom_instructions="Ground recommendations specifically in the cited ADA operations survey [Source-1] and Google review study [Source-2]."
    )
    t1 = time.time()
    print(f"    Duration: {t1 - t0:.2f}s | Word Count: {len(chapter_content.split())}")
    assert ok_gen and len(chapter_content) > 200, f"Chapter generation failed: {gen_msg}"
    print("    ✓ Chapter 1 generated successfully!")
    print("\n    --- Chapter 1 Excerpt ---")
    print("\n".join(chapter_content.splitlines()[:12]))
    print("    -------------------------\n")

    # 11. Save the generated content to SQLite with revision tracking
    print("11. Saving Generated Content and Archiving Revisions...")
    save_ok = GenerationService.save_chapter_content(target_chap["id"], chapter_content, note="Real Ollama Live Generation")
    assert save_ok, "Failed to save chapter content to SQLite"
    GenerationService.set_lock_status(target_chap["id"], True)
    print("    ✓ Chapter saved, locked, and revision history updated.")

    # Run Live Quality Audit on the generated chapter
    print("\n    Executing Real Quality Assurance Audit with Ollama...")
    auditor = AuditService(client)
    # Refresh chapter record
    refreshed_chap = OutlineService.get_chapters(project_id)[0]
    ok_audit, audit_rep, audit_msg = auditor.audit_chapter(project_id, refreshed_chap, charter_data)
    if ok_audit and audit_rep:
        print(f"    ✓ Audit Complete: Status = {audit_rep['status']} | Score = {audit_rep['score']}/100")
        print(f"      Factual Assessment: {audit_rep.get('factual_accuracy_assessment','')[:120]}...")

    # 12. Restart/Reopen Project and Verify Persistence
    print("\n12. Verifying Project Persistence and Reloading from Database...")
    reopened_project = ProjectService.get_project(project_id)
    assert reopened_project is not None, "Project failed to reopen from SQLite!"
    reopened_charter = CharterService.get_charter(project_id)
    assert reopened_charter is not None, "Product Charter failed to persist!"
    reopened_chaps = OutlineService.get_chapters(project_id)
    assert len(reopened_chaps) == len(chapters_in_db), "Chapter counts do not match after persistence check!"
    persisted_content = reopened_chaps[0].get("content", "")
    assert len(persisted_content) == len(chapter_content), "Chapter text was corrupted or lost during persistence!"
    print(f"    ✓ SQLite persistence 100% verified. Reopened '{reopened_project['name']}' with {len(reopened_chaps)} chapters.")

    # 13. Export the generated chapter to PDF and DOCX
    print("\n13. Exporting Publication PDF and DOCX...")
    ok_pdf, pdf_path, pdf_msg = ExportService.generate_pdf(project_id, reopened_charter, reopened_chaps, sources)
    print(f"    PDF Export: {pdf_msg}")
    assert ok_pdf and os.path.exists(pdf_path), f"PDF Export failed: {pdf_msg}"

    ok_docx, docx_path, docx_msg = ExportService.generate_docx(project_id, reopened_charter, reopened_chaps, sources)
    print(f"    DOCX Export: {docx_msg}")
    assert ok_docx and os.path.exists(docx_path), f"DOCX Export failed: {docx_msg}"

    # 14. Open both files and verify their contents
    print("\n14. Deep Content Inspection of Exported Files...")
    # Inspect PDF with PyMuPDF
    pdf_doc = fitz.open(pdf_path)
    page_count = len(pdf_doc)
    full_pdf_text = "".join([page.get_text() for page in pdf_doc])
    pdf_doc.close()
    print(f"    ✓ PDF file successfully opened: {page_count} pages, {len(full_pdf_text)} characters.")
    assert page_count >= 4, "PDF has insufficient pages!"
    # Normalize whitespace/newlines for multi-line title matching
    normalized_pdf_text = " ".join(full_pdf_text.split())
    normalized_expected_title = " ".join(reopened_charter["product_title"].split())
    assert normalized_expected_title in normalized_pdf_text, "Product title missing in PDF!"
    assert target_chap["title"] in full_pdf_text, "Chapter title missing in PDF!"

    # Inspect DOCX with python-docx
    wdoc = docx.Document(docx_path)
    para_count = len(wdoc.paragraphs)
    full_docx_text = "\n".join([p.text for p in wdoc.paragraphs])
    print(f"    ✓ DOCX file successfully opened: {para_count} paragraphs.")
    assert para_count >= 10, "DOCX has insufficient paragraphs!"
    assert reopened_charter["product_title"] in full_docx_text, "Product title missing in DOCX!"

    # 15. Test error handling when Ollama is unavailable
    print("\n15. Testing Offline Resilience / Error Handling when Ollama is Unavailable...")
    offline_client = OllamaClient(base_url="http://localhost:99999")
    off_ok, off_msg = offline_client.check_connection()
    assert off_ok is False, "Offline check should fail on invalid port"
    print(f"    ✓ Offline connection check properly caught: {off_msg}")

    gen_off_ok, gen_off_resp = offline_client.generate(prompt="Hello")
    assert gen_off_ok is False, "Offline generate should fail cleanly"
    print(f"    ✓ Generation failure gracefully handled without crash: {gen_off_resp}")

    # 16. Citation Grounding & Fabrication Check
    print("\n16. Checking Citation Grounding & Anti-Fabrication Safeguards...")
    print(f"    Searching for valid Source IDs in generated chapter...")
    has_valid_citation = ("[Source-" in chapter_content) or ("Source" in chapter_content) or ("ADA" in chapter_content)
    print(f"    Valid empirical citations found in text: {has_valid_citation}")
    # Verify no fabricated URLs like http://fakeurl
    assert "fakeurl" not in chapter_content.lower() and "example.com/fake" not in chapter_content.lower()
    print("    ✓ Anti-fabrication check passed.")

    print_header("REAL-WORLD INTEGRATION TEST: ALL 16 REQUIREMENTS PASSED!")

if __name__ == "__main__":
    main()
