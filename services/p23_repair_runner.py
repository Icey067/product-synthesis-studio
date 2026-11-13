"""Non-destructive P2.3 benchmark repair orchestration.

The runner requires a disposable SQLite copy.  It clones a failed benchmark
into a new repair project, archives the copied manuscript in that repair
project's revision history, resets only the repair copy for section-by-section
generation, and records deterministic before/after audits.  It never touches
the source project or the production database.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _configure_database(path: Path) -> None:
    import config.settings as settings
    import database.db as db
    import database.migrations as migrations

    settings.DATABASE_PATH = path
    settings.BACKUP_DIR = path.parent / "backups"
    db.DATABASE_PATH = path
    migrations.DATABASE_PATH = path
    migrations.BACKUP_DIR = settings.BACKUP_DIR


def _summary(project_id: int, chapters: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    from services.book_architecture_service import BookArchitectureService
    from services.claim_service import ClaimService
    from services.repetition_service import RepetitionService

    claims = ClaimService.project_summary(project_id)
    repetition = RepetitionService.scan_project(project_id, list(chapters), persist=True)
    architecture = BookArchitectureService.build(project_id, force=True)
    return {
        "claims": {k: v for k, v in claims.items() if k != "claims"},
        "repetition": {k: v for k, v in repetition.items() if k != "findings"},
        "architecture": {
            "status": architecture.get("status"),
            "overlap_count": architecture.get("overlap_count"),
        },
    }


def _archive_and_reset(project_id: int) -> None:
    """Keep copied failed text recoverable before starting a repair draft."""
    from database.db import transaction

    with transaction() as conn:
        chapters = conn.execute(
            "SELECT id, content, word_count FROM chapters WHERE project_id = ?", (project_id,)
        ).fetchall()
        for chapter in chapters:
            content = chapter["content"] or ""
            if content.strip():
                next_revision = conn.execute(
                    "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM chapter_revisions WHERE chapter_id = ?",
                    (chapter["id"],),
                ).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO chapter_revisions (chapter_id, revision_number, content, word_count, notes)
                    VALUES (?, ?, ?, ?, 'P2.1 manuscript archived before P2.3 repair')
                    """,
                    (chapter["id"], next_revision, content, chapter["word_count"] or len(content.split())),
                )
        conn.execute(
            """
            UPDATE chapters SET content = '', word_count = 0, actual_word_count = 0,
                structural_complete = 0, evidence_complete = 0, editorial_status = 'NOT_REVIEWED',
                approval_status = 'UNREVIEWED', validation_status = 'PENDING', validation_json = NULL,
                lifecycle_status = 'INCOMPLETE', is_locked = 0
            WHERE project_id = ?
            """,
            (project_id,),
        )
        conn.execute(
            """
            UPDATE chapter_sections SET content = '', word_count = 0, status = 'PLANNED',
                validation_status = 'PENDING', validation_json = NULL, claim_summary_json = NULL,
                repetition_summary_json = NULL, approved = 0, error_message = NULL
            WHERE project_id = ?
            """,
            (project_id,),
        )


def _source_inventory(sources: Iterable[Dict[str, Any]], chapters: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from services.evidence_plan_service import EvidencePlanService

    result: List[Dict[str, Any]] = []
    chapter_list = list(chapters)
    for source in sources:
        title = str(source.get("title") or "")
        relevant_chapters: List[int] = []
        for chapter in chapter_list:
            plan = EvidencePlanService.build(int(source["project_id"]), chapter, sources=[source])
            if plan.get("items"):
                relevant_chapters.append(int(chapter.get("chapter_number") or 0))
        result.append(
            {
                "source_id": source.get("id"),
                "title": title,
                "publisher": source.get("publisher"),
                "publication_date": source.get("publication_date"),
                "reliability": source.get("assessed_reliability"),
                "content_chars": len(source.get("raw_content") or ""),
                "candidate_chapters": relevant_chapters,
                "limitations": source.get("limitations") or "",
            }
        )
    return result


def run(
    database_path: str | Path,
    source_project_id: int,
    max_chapters: int = 0,
) -> Dict[str, Any]:
    """Create a repair project, plan every chapter, and generate up to a limit.

    ``max_chapters=0`` means all chapters.  The result remains truthful if the
    local model is unavailable or a chapter fails its deterministic checks.
    """
    path = Path(database_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    _configure_database(path)

    from database.db import get_connection, init_db
    from services.book_architecture_service import BookArchitectureService
    from services.chapter_planning_service import ChapterPlanningService
    from services.ollama_client import OllamaClient
    from services.outline_service import OutlineService
    from services.project_service import ProjectService
    from services.research_service import ResearchService
    from services.verification_service import VerificationService

    init_db()
    source = ProjectService.get_project(source_project_id)
    if not source:
        raise ValueError(f"Project {source_project_id} was not found")
    repair_project_id = ProjectService.duplicate_project(source_project_id)
    if not repair_project_id:
        raise RuntimeError(f"Could not clone project {source_project_id}")
    ProjectService.update_project(repair_project_id, {"status": "P2_3_REPAIR"})

    source_chapters = OutlineService.get_chapters(source_project_id)
    sources = ResearchService.get_sources(repair_project_id)
    baseline = _summary(source_project_id, source_chapters)
    _archive_and_reset(repair_project_id)
    chapters = OutlineService.get_chapters(repair_project_id)
    architecture = BookArchitectureService.build(repair_project_id, force=True)
    planner = ChapterPlanningService()
    plans = [planner.plan_chapter(repair_project_id, chapter, refresh=True) for chapter in chapters]

    client = OllamaClient()
    connected, model_message = client.check_connection()
    models = client.list_models() if connected else []
    generation_limit = len(chapters) if max_chapters <= 0 else min(max_chapters, len(chapters))
    generated: List[Dict[str, Any]] = []
    if models:
        for chapter in chapters[:generation_limit]:
            outcome = planner.generate_chapter_structured(repair_project_id, chapter)
            generated.append(
                {
                    "chapter": chapter.get("chapter_number"),
                    "title": chapter.get("title"),
                    "saved": bool(outcome.get("saved")),
                    "message": outcome.get("message"),
                    "sections_generated": outcome.get("sections_generated", 0),
                    "word_count": (outcome.get("report") or {}).get("word_count", 0),
                    "claim_summary": (outcome.get("claims") or {}).get("summary", {}),
                }
            )
    else:
        generated.append({"saved": False, "message": f"Generation not started: {model_message}"})

    repaired_chapters = OutlineService.get_chapters(repair_project_id)
    for chapter in repaired_chapters:
        VerificationService.deterministic_checks(
            repair_project_id, chapter, {}, sources,
            product_format=source.get("product_format") or "Ebook",
        )
    after = _summary(repair_project_id, repaired_chapters)
    with get_connection() as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()

    return {
        "mode": "P2.3 repair project; originals preserved in the source project and repair revisions",
        "source_project_id": source_project_id,
        "repair_project_id": repair_project_id,
        "source_records": len(sources),
        "chapter_count": len(chapters),
        "models": models,
        "model_status": model_message,
        "architecture": {"status": architecture.get("status"), "overlap_count": architecture.get("overlap_count")},
        "source_inventory": _source_inventory(sources, chapters),
        "plans_built": len(plans),
        "generation_limit": generation_limit,
        "generated": generated,
        "baseline": baseline,
        "after": after,
        "database_integrity": {"integrity_check": integrity, "foreign_key_issues": len(foreign_keys)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="Disposable SQLite copy")
    parser.add_argument("project_id", type=int, help="Failed benchmark project ID")
    parser.add_argument("--max-chapters", type=int, default=0, help="0 means all chapters")
    args = parser.parse_args()
    print(json.dumps(run(args.database, args.project_id, args.max_chapters), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
