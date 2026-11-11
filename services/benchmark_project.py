"""Benchmark project: a real, honest end-to-end exercise of the pipeline.

The benchmark project is **not** a demo with planted research.  It is created
with the real target metadata (India, non-expert readers, English, ebook) and no
research sources at all.  Because there is no evidence library, the deterministic
report can only describe what actually exists — it must never imply that a
chapter is evidence-backed or commercially validated.

Run it with::

    python -m services.benchmark_project --report
    python -m services.benchmark_project --live      # generates with Ollama
    python -m services.benchmark_project --delete    # remove the benchmark

The report deliberately separates:

* planned / target / minimum / actual words,
* structural completeness,
* evidence completeness (including "no sources" as a failure, not a pass),
* editorial status.

An export is never treated as proof of commercial readiness.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional

from database.db import get_connection
from models.schemas import ProjectCreate
from services.chapter_contract import analyze_chapter, count_words
from services.manuscript_service import ManuscriptService
from services.outline_service import OutlineService
from services.project_service import ProjectService
from services.research_service import ResearchService
from services.retrieval_service import RetrievalService

BENCHMARK_NAME = "Practical digital growth for local micro-businesses"
BENCHMARK_COUNTRY = "India"
BENCHMARK_LANGUAGE = "English"
BENCHMARK_EXPERTISE = "NON_EXPERT"
BENCHMARK_FORMAT = "Ebook"
BENCHMARK_CHAPTERS = 11

LIMITATIONS = (
    "This report is a deterministic structural assessment only. It does not "
    "prove factual accuracy, commercial viability, or reader demand. No export "
    "artifact is treated as evidence of readiness, and no research was seeded "
    "for the benchmark."
)


def ensure_benchmark_project() -> int:
    """Create the benchmark project once and return its id."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM projects WHERE name = ? ORDER BY id ASC LIMIT 1",
            (BENCHMARK_NAME,),
        ).fetchone()
        if row:
            return int(row["id"])
    return ProjectService.create_project(
        ProjectCreate(
            name=BENCHMARK_NAME,
            country=BENCHMARK_COUNTRY,
            language=BENCHMARK_LANGUAGE,
            target_customer=(
                "Local micro-business owners in India with no dedicated marketing "
                "staff and a very small budget"
            ),
            expertise_mode=BENCHMARK_EXPERTISE,
            product_category="Business & Productivity",
            product_format=BENCHMARK_FORMAT,
            required_chapter_count=BENCHMARK_CHAPTERS,
            target_chapter_words=2200,
            min_chapter_words=1000,
        )
    )


def deterministic_report(project_id: int) -> Dict[str, Any]:
    """Assess the real state of the benchmark project without inventing data."""
    project = ProjectService.get_project(project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    chapters = OutlineService.get_chapters(project_id)
    sources = ResearchService.get_sources(project_id)
    chapter_rows: List[Dict[str, Any]] = []
    totals = {"planned": 0, "target": 0, "actual": 0, "structural": 0, "evidence": 0}
    complete = 0

    for chapter in chapters:
        content = chapter.get("content") or ""
        source_ids = RetrievalService.used_source_ids(chapter["id"])
        report = analyze_chapter(
            content,
            target=int(chapter.get("target_word_count") or 0) or None,
            min_words=int(project.get("min_chapter_words") or 0) or None,
            source_ids=source_ids,
            product_format=project.get("product_format") or BENCHMARK_FORMAT,
        )
        words = int(report.get("actual_words") or count_words(content))
        planned = int(chapter.get("planned_word_count") or chapter.get("target_word_count") or 0)
        target = int(report.get("target_words") or 0)
        totals["planned"] += planned
        totals["target"] += target
        totals["actual"] += words
        totals["structural"] += 1 if report.get("structural_complete") else 0
        totals["evidence"] += 1 if report.get("evidence_complete") else 0
        if report.get("status") == "COMPLETE":
            complete += 1
        chapter_rows.append(
            {
                "chapter": chapter.get("chapter_number"),
                "title": chapter.get("title", ""),
                "planned_words": planned,
                "target_words": target,
                "min_words": int(report.get("min_words") or 0),
                "actual_words": words,
                "status": report.get("status"),
                "structural_complete": bool(report.get("structural_complete")),
                "evidence_complete": bool(report.get("evidence_complete")),
                "missing_sections": report.get("missing_sections", []),
                "invalid_citations": report.get("invalid_citations", []),
                "citations": report.get("citation_ids", []),
                "research_sources_used": source_ids,
                "editorial_status": chapter.get("editorial_status") or "NOT_REVIEWED",
                "approved": ManuscriptService.chapter_is_approved(chapter["id"]),
            }
        )

    with get_connection() as conn:
        exports = [
            dict(row)
            for row in conn.execute(
                "SELECT export_type, file_path, completeness_status FROM exports "
                "WHERE project_id = ? ORDER BY id",
                (project_id,),
            ).fetchall()
        ]

    return {
        "project_id": project_id,
        "project_name": project.get("name"),
        "metadata": {
            "country": project.get("country"),
            "language": project.get("language"),
            "expertise_mode": project.get("expertise_mode"),
            "product_format": project.get("product_format"),
        },
        "chapter_count": len(chapters),
        "research_sources": len(sources),
        "evidence_note": (
            "No research sources are seeded for this benchmark; evidence "
            "completeness therefore reflects real gaps rather than synthetic data."
            if not sources
            else "Evidence is drawn only from sources actually added to this project."
        ),
        "totals": totals,
        "complete_chapters": complete,
        "chapters": chapter_rows,
        "exports": exports,
        "commercial_readiness_claim": False,
        "limitations": LIMITATIONS,
    }


def run_live_pipeline(
    project_id: int,
    max_chapters: Optional[int] = None,
    log=print,
) -> Dict[str, Any]:
    """Run the real generation pipeline against the local model.

    Every stage reuses an existing artifact if present so an interrupted run can
    be resumed.  No research sources are created.
    """
    from services.batch_service import BatchService
    from services.charter_service import CharterService
    from services.transformation_service import TransformationService

    project = ProjectService.get_project(project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    result: Dict[str, Any] = {"project_id": project_id}

    charter = CharterService.get_charter(project_id)
    if not charter:
        log("Generating product charter...")
        ok, charter, message = CharterService().generate_charter(project)
        result["charter"] = {"ok": ok, "message": message}
        if not ok:
            return result
    else:
        log("Charter already exists; reusing it.")

    transformation = TransformationService.get_map(project_id)
    if not transformation:
        log("Generating transformation map...")
        ok, transformation, message = TransformationService().generate_transformation_map(
            project_id,
            charter.get("product_title", project.get("name", "")),
            charter.get("target_audience", project.get("target_customer", "")),
            charter.get("customer_problem", ""),
            charter.get("desired_outcome", ""),
        )
        result["transformation"] = {"ok": ok, "message": message}
        if not ok:
            return result
    else:
        log("Transformation map already exists; reusing it.")

    chapters = OutlineService.get_chapters(project_id)
    if not chapters:
        log("Generating chapter outline...")
        ok, chapters, message = OutlineService().generate_outline(
            project_id, charter, transformation, BENCHMARK_CHAPTERS
        )
        result["outline"] = {"ok": ok, "message": message}
        if not ok:
            return result
    else:
        log(f"Outline already exists ({len(chapters)} chapters); reusing it.")

    log("Generating chapters section by section...")
    batch = BatchService()
    outcome = batch.run(
        project_id,
        structured=True,
        max_chapters=max_chapters,
        progress_cb=lambda done, total, chapter, message: log(
            f"  [{done}/{total}] Chapter {chapter.get('chapter_number')}: {message}"
        ),
    )
    result["batch"] = {
        "status": outcome.get("status"),
        "produced": outcome.get("produced"),
        "failed": outcome.get("failed"),
        "message": outcome.get("message"),
    }
    return result


def delete_benchmark_project() -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM projects WHERE name = ? ORDER BY id ASC LIMIT 1",
            (BENCHMARK_NAME,),
        ).fetchone()
    if not row:
        return False
    return ProjectService.delete_project(int(row["id"]))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true", help="create the project if missing")
    parser.add_argument("--live", action="store_true", help="generate content with local Ollama")
    parser.add_argument("--report", action="store_true", help="print the deterministic report")
    parser.add_argument("--delete", action="store_true", help="delete the benchmark project")
    parser.add_argument(
        "--max-chapters",
        type=int,
        default=None,
        help="limit live generation to N chapters",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    from database.db import init_db

    init_db()

    if args.delete:
        print("Deleted." if delete_benchmark_project() else "No benchmark project found.")
        return 0

    project_id = ensure_benchmark_project()
    print(f"Benchmark project id: {project_id}")

    if args.live:
        outcome = run_live_pipeline(project_id, max_chapters=args.max_chapters)
        print(json.dumps(outcome, indent=2, default=str))

    if args.report or not args.live:
        report = deterministic_report(project_id)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print(json.dumps(report, indent=2, default=str))
            print("\n" + LIMITATIONS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
