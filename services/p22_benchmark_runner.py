"""Run a non-destructive P2.2 deterministic audit of an existing benchmark.

The runner is deliberately conservative: it works only on a caller-supplied
copy of a SQLite database, clones the requested project, and performs no model
generation.  It therefore reports exactly what P2.2 could verify from the
existing manuscript and its stored source records; it never pretends that old
text was regenerated.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List


def _configure_database(path: Path) -> None:
    """Point the app's local services at a disposable database copy."""
    import config.settings as settings
    import database.db as db
    import database.migrations as migrations

    settings.DATABASE_PATH = path
    settings.BACKUP_DIR = path.parent / "backups"
    db.DATABASE_PATH = path
    migrations.DATABASE_PATH = path
    migrations.BACKUP_DIR = settings.BACKUP_DIR


def run(database_path: str | Path, source_project_id: int) -> Dict[str, Any]:
    path = Path(database_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    _configure_database(path)

    from database.db import get_connection, init_db
    from services.book_architecture_service import BookArchitectureService
    from services.claim_service import ClaimService
    from services.outline_service import OutlineService
    from services.project_service import ProjectService
    from services.repetition_service import RepetitionService
    from services.research_service import ResearchService
    from services.verification_service import VerificationService

    init_db()
    source = ProjectService.get_project(source_project_id)
    if not source:
        raise ValueError(f"Project {source_project_id} was not found in {path}")
    clone_id = ProjectService.duplicate_project(source_project_id)
    if not clone_id:
        raise RuntimeError(f"Could not clone project {source_project_id}")
    chapters = OutlineService.get_chapters(clone_id)
    sources = ResearchService.get_sources(clone_id)
    architecture = BookArchitectureService.build(clone_id, force=True)
    repetition = RepetitionService.scan_project(clone_id, persist=True)

    chapter_results: List[Dict[str, Any]] = []
    total_claims = 0
    unsupported_numeric = 0
    unsupported_factual = 0
    invalid_citations = 0
    human_review_chapters = 0
    approved = 0
    for chapter in chapters:
        content = chapter.get("content") or ""
        claims = ClaimService.analyze_chapter(
            clone_id, int(chapter["id"]), content, sources, persist=True
        )
        deterministic = VerificationService.deterministic_checks(
            clone_id,
            chapter,
            {},
            sources,
            product_format=source.get("product_format") or "Ebook",
        )
        status, score, human_review = VerificationService.final_status(deterministic)
        summary = claims["summary"]
        total_claims += int(summary.get("total_claims") or 0)
        unsupported_numeric += int(summary.get("unsupported_numeric_count") or 0)
        unsupported_factual += int(summary.get("unsupported_count") or 0)
        invalid_citations += len(deterministic["citation_audit"].get("invalid_citations") or [])
        human_review_chapters += 1 if human_review else 0
        approved += 1 if status == "PASS" else 0
        chapter_results.append(
            {
                "chapter": chapter.get("chapter_number"),
                "title": chapter.get("title"),
                "word_count": deterministic["structure"].get("word_count", 0),
                "status": status,
                "score": score,
                "human_review_required": human_review,
                "claim_summary": {k: v for k, v in summary.items() if k != "claims"},
                "invalid_citations": deterministic["citation_audit"].get("invalid_citations") or [],
                "structure_complete": not bool(deterministic["structure"].get("missing_sections")),
            }
        )

    with get_connection() as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "mode": "P2.2 deterministic audit of existing content — no chapters regenerated",
        "source_project_id": source_project_id,
        "clone_project_id": clone_id,
        "source_records": len(sources),
        "chapter_count": len(chapter_results),
        "chapters": chapter_results,
        "totals": {
            "claims": total_claims,
            "unsupported_numeric_claims": unsupported_numeric,
            "unsupported_factual_claims": unsupported_factual,
            "invalid_citations": invalid_citations,
            "citation_coverage": round(
                sum(float(row["claim_summary"].get("citation_coverage") or 0) for row in chapter_results)
                / len(chapter_results),
                3,
            ) if chapter_results else 0.0,
            "exact_repeated_sentences": int(repetition.get("exact_count") or 0),
            "near_duplicate_passages": int(repetition.get("near_count") or 0),
            "chapter_purpose_overlaps": int(architecture.get("overlap_count") or 0),
            "structurally_complete_chapters": sum(1 for row in chapter_results if row["structure_complete"]),
            "human_review_chapters": human_review_chapters,
            "approved_chapters": approved,
            "publication_ready": approved == len(chapter_results) and bool(chapter_results),
        },
        "repetition": {k: v for k, v in repetition.items() if k != "findings"},
        "architecture": {
            "status": architecture.get("status"),
            "overlap_count": architecture.get("overlap_count"),
            "coverage_matrix": architecture.get("coverage_matrix"),
        },
        "database_integrity": {"integrity_check": integrity, "foreign_key_issues": len(foreign_keys)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="A disposable database copy to audit")
    parser.add_argument("project_id", type=int, help="Source project ID in that copy")
    args = parser.parse_args()
    print(json.dumps(run(args.database, args.project_id), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
