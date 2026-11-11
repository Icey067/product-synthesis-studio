import time
from services.outline_service import OutlineService
from services.charter_service import CharterService
from services.research_service import ResearchService
from services.generation_service import GenerationService
from services.export_service import ExportService
from services.ollama_client import OllamaClient

def complete_sample_book():
    client = OllamaClient()
    connected, _ = client.check_connection()
    if not connected:
        print("Ollama is not running. Please start Ollama.")
        return

    # Target Project 1: The 30-Day Local Business Digital Growth Playbook
    project_id = 1
    charter = CharterService.get_charter(project_id)
    chapters = OutlineService.get_chapters(project_id)
    sources = ResearchService.get_sources(project_id)

    print(f"Targeting Project {project_id}: '{charter['product_title']}'")
    print(f"Total outline chapters: {len(chapters)}")

    gen_svc = GenerationService(client)

    # Generate chapters that do not yet have content
    for ch in chapters:
        content = ch.get("content", "").strip()
        ch_num = ch["chapter_number"]
        ch_title = ch["title"]

        if len(content) > 100:
            print(f"✓ Chapter {ch_num}: '{ch_title}' already written ({len(content.split())} words). Skipping.")
            continue

        print(f"Writing Chapter {ch_num}: '{ch_title}' via local Ollama...")
        t0 = time.time()
        ok, text, msg = gen_svc.generate_chapter_content(
            chapter=ch,
            charter=charter,
            evidence_sources=sources,
            custom_instructions="Focus on concrete daily actions, realistic local retail worksheets, and avoid fluff."
        )
        t1 = time.time()
        if ok:
            word_count = len(text.split())
            print(f"  ✓ Finished in {t1 - t0:.1f}s ({word_count} words). Saving...")
            GenerationService.save_chapter_content(ch["id"], text, note="Auto Full-Book Completion")
            GenerationService.set_lock_status(ch["id"], True)
        else:
            print(f"  ✗ Failed Chapter {ch_num}: {msg}")

    # After generating, export full complete book to PDF & DOCX
    refreshed_chapters = OutlineService.get_chapters(project_id)
    print("\nExporting complete multi-chapter publication to PDF and DOCX...")
    ok_pdf, pdf_path, pdf_msg = ExportService.generate_pdf(project_id, charter, refreshed_chapters, sources)
    print(f"PDF: {pdf_msg}")
    ok_docx, docx_path, docx_msg = ExportService.generate_docx(project_id, charter, refreshed_chapters, sources)
    print(f"DOCX: {docx_msg}")

if __name__ == "__main__":
    complete_sample_book()

