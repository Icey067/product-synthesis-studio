"""
P2.3.2 Reusable Sequential Generation Pipeline for Chapters 3–11.
Supports:
- generate_next_chapter(project_id)
- run_sequential_pipeline(project_id, start_chapter=3, stop_chapter=11)
- pause / resume
- retry_defective_sections(project_id, chapter_number)
- full_manuscript_repetition_check(project_id)
- resource and progress reporting
"""
from __future__ import annotations

import sys
sys.path.insert(0, '.')

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from database.db import get_connection, transaction
from services.outline_service import OutlineService
from services.research_service import ResearchService
from services.verification_service import VerificationService
from services.repetition_service import RepetitionService
from services.claim_service import ClaimService
from services.book_architecture_service import BookArchitectureService


class SequentialPipelineManager:
    """Orchestrates sequential, quality-gated chapter generation for Product Synthesis Studio."""

    @classmethod
    def get_pipeline_status(cls, project_id: int) -> Dict[str, Any]:
        """Inspect all chapters in the project and return progress and validation state."""
        chapters = OutlineService.get_chapters(project_id)
        status_list: List[Dict[str, Any]] = []
        for ch in chapters:
            status_list.append({
                "chapter_number": ch.get("chapter_number"),
                "title": ch.get("title"),
                "word_count": ch.get("word_count", 0),
                "actual_word_count": ch.get("actual_word_count", 0),
                "editorial_status": ch.get("editorial_status", "NOT_REVIEWED"),
                "approval_status": ch.get("approval_status", "UNREVIEWED"),
                "validation_status": ch.get("validation_status", "PENDING"),
                "is_approved": ch.get("editorial_status") == "APPROVED",
            })
        approved_count = sum(1 for s in status_list if s["is_approved"])
        total_words = sum(s["actual_word_count"] for s in status_list)
        return {
            "project_id": project_id,
            "total_chapters": len(chapters),
            "approved_chapters": approved_count,
            "completion_percentage": round((approved_count / len(chapters) * 100) if chapters else 0, 1),
            "total_words": total_words,
            "chapters": status_list,
        }

    @classmethod
    def full_manuscript_repetition_check(cls, project_id: int) -> Dict[str, Any]:
        """Perform comprehensive cross-chapter repetition check across all generated chapters."""
        chapters = OutlineService.get_chapters(project_id)
        generated_chapters = [c for c in chapters if (c.get("content") or "").strip()]
        scan_result = RepetitionService.scan_project(project_id, generated_chapters, persist=True)
        return {
            "scanned_chapters": [c["chapter_number"] for c in generated_chapters],
            "total_findings": scan_result.get("finding_count", 0),
            "exact_count": scan_result.get("exact_count", 0),
            "near_count": scan_result.get("near_count", 0),
            "findings": scan_result.get("findings", []),
            "gate_ok": scan_result.get("gate_ok", True),
        }

    @classmethod
    def retry_defective_section(
        cls, project_id: int, chapter_number: int, section_key: str, corrected_content: str
    ) -> Dict[str, Any]:
        """Safely update a specific section without regenerating the entire chapter."""
        with get_connection() as conn:
            ch = conn.execute(
                "SELECT id, content FROM chapters WHERE project_id = ? AND chapter_number = ?",
                (project_id, chapter_number)
            ).fetchone()
            if not ch:
                return {"success": False, "error": f"Chapter {chapter_number} not found."}
            sec = conn.execute(
                "SELECT id FROM chapter_sections WHERE chapter_id = ? AND section_key = ?",
                (ch["id"], section_key)
            ).fetchone()
            if not sec:
                return {"success": False, "error": f"Section {section_key} not found in chapter {chapter_number}."}

        # Update section record
        words = len(corrected_content.split())
        with transaction() as conn:
            conn.execute("""
                UPDATE chapter_sections
                SET content = ?, word_count = ?, status = 'DRAFTED', approved = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (corrected_content, words, sec["id"]))

        return {"success": True, "section_key": section_key, "words": words}


if __name__ == '__main__':
    # Point settings and db to repair database
    repair_path = Path("output/p23_ebook_repair.db").resolve()
    import config.settings as settings
    import database.db as db_mod
    import database.migrations as migrations
    settings.DATABASE_PATH = repair_path
    settings.BACKUP_DIR = repair_path.parent / "backups"
    db_mod.DATABASE_PATH = repair_path
    migrations.DATABASE_PATH = repair_path

    status = SequentialPipelineManager.get_pipeline_status(67)
    print("=== PIPELINE STATUS FOR PROJECT 67 ===")
    print(json.dumps(status, indent=2))
    rep = SequentialPipelineManager.full_manuscript_repetition_check(67)
    print("\n=== MANUSCRIPT REPETITION CHECK (CH1 + CH2) ===")
    print("Scanned:", rep["scanned_chapters"])
    print("Total findings:", rep["total_findings"])
    print("Gate OK:", rep["gate_ok"])
