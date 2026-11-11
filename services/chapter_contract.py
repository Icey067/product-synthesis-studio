"""Deterministic chapter structure contract.

Measures real word counts, required sections, citations and format compliance.
No model is allowed to certify that a chapter meets these requirements.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from database.db import get_connection, transaction
from models.enums import ProductFormat
from services.product_format_service import validate_chapter as validate_chapter_format

CITATION_RE = re.compile(r"\[(?:Source|Ref)-(\d+)\]", re.IGNORECASE)

SECTION_PATTERNS: Dict[str, List[str]] = {
    "introduction": [r"##\s*introduction", r"\bintroduction\b", r"##\s*overview", r"\bwhy this matters\b"],
    "step_by_step": [r"##\s*step[- ]by[- ]step", r"\bstep[- ]by[- ]step\b", r"##\s*implementation", r"\bimplementation steps?\b"],
    "practical_example": [r"##\s*practical example", r"\bfor example\b", r"\bcase study\b", r"\bscenario\b", r"\bin practice\b"],
    "common_mistakes": [r"##\s*common mistakes", r"\bcommon mistakes\b", r"\bpitfalls?\b", r"\bhigh[- ]cost traps?\b", r"\bwhat not to do\b"],
    "worksheet": [r"##\s*worksheet", r"\bworksheet\b", r"\bexercise\b", r"-\s*\[\s?\]", r"\bchecklist\b", r"\bself[- ]assessment\b"],
    "summary": [r"##\s*chapter summary", r"\bchapter summary\b", r"\bkey takeaways\b", r"\brecap\b", r"24[- ]hour action\b", r"\bnext steps?\b"],
}


def get_int_setting(key: str, default: int) -> int:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM application_settings WHERE key = ?", (key,)
            ).fetchone()
            return int(row["value"]) if row and row["value"] is not None else default
    except Exception:
        return default


def set_int_setting(key: str, value: int) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO application_settings (key, value) VALUES (?, ?)",
            (key, str(int(value))),
        )


def target_words(project_id: Optional[int] = None) -> int:
    """Project target when available, otherwise the global default."""
    if project_id is not None:
        try:
            from services.project_service import ProjectService

            return int(ProjectService.get_chapter_settings(project_id)["target_words"])
        except Exception:
            pass
    return max(500, get_int_setting("chapter_target_words", 2200))


def min_publication_words(project_id: Optional[int] = None) -> int:
    """Project minimum when available, otherwise the global default."""
    if project_id is not None:
        try:
            from services.project_service import ProjectService

            return int(ProjectService.get_chapter_settings(project_id)["min_words"])
        except Exception:
            pass
    return max(200, get_int_setting("min_chapter_words", 1000))


def count_words(content: str) -> int:
    return len((content or "").split())


def find_citations(content: str) -> List[int]:
    seen = set()
    result = []
    for raw in CITATION_RE.findall(content or ""):
        val = int(raw)
        if val not in seen:
            seen.add(val)
            result.append(val)
    return result


def detect_sections(content: str) -> Dict[str, bool]:
    lower = (content or "").lower()
    return {
        name: any(re.search(pattern, lower, re.IGNORECASE) for pattern in patterns)
        for name, patterns in SECTION_PATTERNS.items()
    }


def analyze_chapter(
    content: str,
    target: Optional[int] = None,
    min_words: Optional[int] = None,
    source_ids: Optional[List[int]] = None,
    product_format: str = "Ebook",
) -> Dict[str, Any]:
    target = int(target or target_words())
    contract_min = int(min_words) if min_words is not None else int(target * 0.85)
    words = count_words(content)
    sections = detect_sections(content)
    missing_sections = [name for name, present in sections.items() if not present]

    citations = find_citations(content)
    valid_source_ids = set(int(s) for s in (source_ids or []))
    invalid_citations = [c for c in citations if valid_source_ids and c not in valid_source_ids]
    citation_required = bool(valid_source_ids)

    format_ok, format_issues = validate_chapter_format(product_format, content or "")

    issues: List[Dict[str, str]] = []
    if words < contract_min:
        issues.append({
            "code": "word_count_low",
            "message": f"Chapter has {words} words; minimum for this target is {contract_min}.",
        })
    for section in missing_sections:
        issues.append({
            "code": f"missing_section:{section}",
            "message": f"Required section missing: {section.replace('_', ' ')}.",
        })
    if citation_required and not citations:
        issues.append({
            "code": "missing_citations",
            "message": "No [Source-ID] citations; research sources are available and must be used.",
        })
    if invalid_citations:
        issues.append({
            "code": "invalid_citations",
            "message": f"Invalid source IDs: {invalid_citations}.",
        })
    if not format_ok:
        for fi in format_issues:
            issues.append({"code": fi["code"], "message": fi["message"]})

    complete = (
        words >= contract_min
        and not missing_sections
        and not invalid_citations
        and (not citation_required or bool(citations))
        and format_ok
    )

    structural_complete = bool(words) and not missing_sections and format_ok
    # "Evidence complete" means the chapter actually cites at least one valid
    # source.  With zero sources or zero citations it is a real gap, not a pass;
    # otherwise an empty chapter could look evidence-complete.
    evidence_complete = bool(citations) and not invalid_citations

    return {
        "status": "COMPLETE" if complete else "INCOMPLETE",
        "word_count": words,
        "actual_words": words,
        "planned_words": target,
        "target_words": target,
        "min_words": contract_min,
        "completion_pct": round(min(100.0, (words / target) * 100), 1) if target else 0.0,
        "sections": sections,
        "missing_sections": missing_sections,
        "citation_ids": citations,
        "invalid_citations": invalid_citations,
        "citation_required": citation_required,
        "format_ok": format_ok,
        "structural_complete": structural_complete,
        "evidence_complete": evidence_complete,
        "issues": issues,
    }


def deficiencies_for_expansion(report: Dict[str, Any]) -> str:
    lines = []
    if report["word_count"] < report["min_words"]:
        lines.append(
            f"- Length: {report['word_count']} words vs minimum {report['min_words']}. Add substantive, specific material (not repetition)."
        )
    for section in report.get("missing_sections", []):
        lines.append(f"- Missing section: {section.replace('_', ' ')}.")
    if report.get("citation_required") and not report.get("citation_ids"):
        lines.append("- Missing citations: integrate at least two [Source-ID] citations from the supplied research.")
    if report.get("invalid_citations"):
        lines.append(f"- Invalid citations: {report['invalid_citations']}. Remove or replace them.")
    for issue in report.get("issues", []):
        if issue["code"].startswith("chapter_software"):
            lines.append(f"- Format violation: {issue['message']}")
    return "\n".join(lines) or "- General depth and specificity."


def save_validation(chapter_id: int, report: Dict[str, Any]) -> None:
    with transaction() as conn:
        conn.execute(
            """
            UPDATE chapters
            SET validation_status = ?, validation_json = ?, target_word_count = ?,
                actual_word_count = ?, structural_complete = ?, evidence_complete = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                report["status"],
                json.dumps(report),
                report["target_words"],
                int(report.get("actual_words", report.get("word_count", 0)) or 0),
                1 if report.get("structural_complete") else 0,
                1 if report.get("evidence_complete") else 0,
                chapter_id,
            ),
        )
