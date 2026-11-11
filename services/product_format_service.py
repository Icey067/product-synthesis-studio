"""Product-format contracts.

The user selects a format (ebook, workbook, course, ...).  The language model
must not silently turn an ebook into a software product.  This module provides
deterministic checks used by every generation stage plus contract text that is
injected into prompts.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from models.enums import ProductFormat

BOOK_FORMATS = {
    ProductFormat.EBOOK,
    ProductFormat.PRACTICAL_GUIDE,
    ProductFormat.WORKBOOK,
    ProductFormat.PLANNER,
    ProductFormat.CHECKLIST_BUNDLE,
    ProductFormat.TEMPLATE_COLLECTION,
}

FORMAT_PRIMARY_DELIVERABLE = {
    ProductFormat.EBOOK: "a readable digital book (PDF/EPUB) with educational chapters",
    ProductFormat.PRACTICAL_GUIDE: "a readable practical guide with step-by-step chapters",
    ProductFormat.WORKBOOK: "a printable workbook with exercises, worksheets and reflection prompts",
    ProductFormat.PLANNER: "a printable planner with schedules, templates and tracking pages",
    ProductFormat.CHECKLIST_BUNDLE: "a bundle of printable checklists and operating procedures",
    ProductFormat.TEMPLATE_COLLECTION: "a collection of editable templates with instructions",
    ProductFormat.STRUCTURED_COURSE: "a structured course curriculum with lessons and exercises",
}

# Phrases that promise a software product to the reader. These are intentionally
# narrow so ordinary words such as "apply" or "application of a method" do not
# trigger a false positive.
_SOFTWARE_PATTERNS = [
    r"\b(?:our|the|this|my)\s+(?:new\s+)?(?:mobile\s+|desktop\s+|android\s+|ios\s+)?app\b",
    r"\b(?:mobile|desktop|android|ios|web)\s+application\b",
    r"\bapplication\s+(?:download|install|subscription|login|dashboard)\b",
    r"\bdownload\s+(?:the|our|this|my)?\s*(?:mobile\s+)?(?:app|application|software)\b",
    r"\binstall\s+(?:the|our|this|my)?\s*(?:app|application|software)\b",
    r"\b(?:app|application|software)\s+(?:download|install|subscription|login|dashboard|interface)\b",
    r"\b(?:saas|software-as-a-service|browser extension|mobile application|web application)\b",
    r"\b(?:google play|apple app store|play store)\b",
]

_GENERIC_OUTLINE_TITLES = {
    "introduction and the problem",
    "customer diagnosis & self-assessment",
    "customer diagnosis and self-assessment",
    "foundations and preparation",
    "the core solution framework",
    "practical implementation step 1",
    "practical implementation step 2",
    "practical implementation step 3",
    "tools, templates, and worksheets",
    "tools, templates and worksheets",
    "mistakes, pitfalls, and troubleshooting",
    "mistakes, pitfalls and troubleshooting",
    "case studies and practical applications",
    "30-day roadmap and next steps",
}

SOFTWARE_CATEGORY_TERMS = (
    "digital tool",
    "software",
    "mobile app",
    "application",
    "saas",
    "platform",
)

BOOK_TERMS = (
    "ebook",
    "e-book",
    "book",
    "guide",
    "workbook",
    "playbook",
    "manual",
    "handbook",
    "chapters",
    "kindle",
    "pdf",
    "planner",
    "checklist",
    "template",
)


def _blob(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_blob(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_blob(v) for v in value)
    return ""


def detect_software_claims(text: str) -> List[str]:
    if not text:
        return []
    found: List[str] = []
    for pattern in _SOFTWARE_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            snippet = text[max(0, match.start() - 50): match.end() + 50].replace("\n", " ")
            found.append(snippet.strip())
    # De-duplicate while preserving order
    seen = set()
    unique = []
    for item in found:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def format_family(product_format: str) -> str:
    try:
        fmt = ProductFormat(product_format)
    except ValueError:
        return "book"
    return "book" if fmt in BOOK_FORMATS else "course"


def contract_text(product_format: str) -> str:
    try:
        fmt = ProductFormat(product_format)
    except ValueError:
        fmt = ProductFormat.EBOOK
    primary = FORMAT_PRIMARY_DELIVERABLE.get(fmt, "the selected digital product format")
    base = (
        f"PRODUCT FORMAT CONTRACT — the selected format is '{fmt.value}'.\n"
        f"The primary deliverable MUST be {primary}. "
        "Everything you write must be usable in that format."
    )
    if fmt in BOOK_FORMATS:
        base += (
            "\nDo NOT promise, sell, or require a mobile app, web app, software download, "
            "SaaS login, browser extension, or app-store purchase. Do not tell the reader to "
            "download or install an application. Software may only be mentioned as optional "
            "supplementary material and never as the main product. Exercises, worksheets and "
            "templates must live inside the book itself."
        )
    elif fmt == ProductFormat.STRUCTURED_COURSE:
        base += (
            "\nThe product is a course curriculum. Lessons, exercises and worksheets are part "
            "of the course materials; do not promise a separate app or software product."
        )
    return base


def _issue(code: str, severity: str, message: str, correction: str = "") -> Dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "correction": correction,
    }


def validate_charter(product_format: str, charter: Dict[str, Any]) -> Tuple[bool, List[Dict[str, str]]]:
    issues: List[Dict[str, str]] = []
    fmt = format_family(product_format)
    blob = _blob(
        {
            "title": charter.get("product_title", ""),
            "promise": charter.get("core_promise", ""),
            "description": charter.get("product_description", ""),
            "mechanism": charter.get("unique_mechanism", ""),
            "differentiation": charter.get("differentiation", ""),
            "deliverables": charter.get("deliverables", []),
        }
    )

    if fmt == "book":
        category = str(charter.get("product_category", "")).lower()
        if any(term in category for term in SOFTWARE_CATEGORY_TERMS):
            issues.append(_issue(
                "format_category_mismatch", "HIGH",
                f"Product category '{charter.get('product_category')}' describes software, not a published book.",
                "Set product_category to a publishing/education category such as 'Business Education' or 'Practical Guide'.",
            ))

        claims = detect_software_claims(blob)
        if claims:
            issues.append(_issue(
                "format_software_promise", "HIGH",
                "The charter promises software: " + "; ".join(claims[:3]),
                "Rewrite the promise/description so the deliverable is a readable book, guide or workbook.",
            ))

        deliverables = [str(d) for d in charter.get("deliverables", [])]
        if deliverables:
            if not any(any(term in d.lower() for term in BOOK_TERMS) for d in deliverables):
                issues.append(_issue(
                    "format_missing_book_deliverable", "HIGH",
                    "None of the deliverables is a book, guide, workbook, planner or template collection.",
                    "Make the first deliverable the ebook/manuscript (PDF/EPUB) and list only in-book assets.",
                ))
            if detect_software_claims(" ".join(deliverables)):
                issues.append(_issue(
                    "format_software_deliverable", "HIGH",
                    "A software app/application is listed as a deliverable.",
                    "Replace software deliverables with in-book chapters, worksheets, checklists or templates.",
                ))
    else:
        claims = detect_software_claims(blob)
        if claims:
            issues.append(_issue(
                "format_software_promise", "MEDIUM",
                "The selected non-book format mentions software: " + "; ".join(claims[:3]),
                "Keep the deliverable aligned with the selected course/workbook format.",
            ))

    ok = not any(i["severity"] == "HIGH" for i in issues)
    return ok, issues


def validate_transformation_map(product_format: str, data: Dict[str, Any]) -> Tuple[bool, List[Dict[str, str]]]:
    issues: List[Dict[str, str]] = []
    if format_family(product_format) != "book":
        return True, issues
    blob = _blob({"after": data.get("after_situation", {}), "milestones": data.get("milestones", [])})
    claims = detect_software_claims(blob)
    if claims:
        issues.append(_issue(
            "format_software_transformation", "HIGH",
            "The transformation map describes app/software outcomes for a book project: " + "; ".join(claims[:3]),
            "Describe reader capabilities and business outcomes, not software features or downloads.",
        ))
    return not any(i["severity"] == "HIGH" for i in issues), issues


def validate_outline(
    product_format: str,
    chapters: Any,
    required_chapters: Optional[int] = None,
) -> Tuple[bool, List[Dict[str, str]]]:
    issues: List[Dict[str, str]] = []
    # Accept either a list of chapters or the full {"chapters": [...]} payload.
    if isinstance(chapters, dict):
        chapters = chapters.get("chapters", [])
    if not isinstance(chapters, list):
        return False, [_issue("outline_invalid", "HIGH", "The outline payload is not a list of chapters.", "Return a JSON object with a chapters array.")]
    if not chapters:
        return False, [_issue("outline_empty", "HIGH", "The outline contains no chapters.", "Return 8-14 chapters.")]

    # A project may be configured for fewer than eight required chapters. The
    # outline must be allowed to satisfy that configuration instead of being
    # rejected by a hard-coded full-book range. Larger projects may also exceed
    # fourteen chapters when they explicitly require more.
    try:
        required = int(required_chapters) if required_chapters is not None else None
    except (TypeError, ValueError):
        required = None
    min_chapters = min(8, required) if required else 8
    max_chapters = max(14, required) if required else 14
    if not min_chapters <= len(chapters) <= max_chapters:
        issues.append(_issue(
            "outline_count", "HIGH",
            f"The outline has {len(chapters)} chapters; this project needs {min_chapters}-{max_chapters}.",
            f"Return between {min_chapters} and {max_chapters} substantial chapters.",
        ))

    numbers = [int(c.get("chapter_number", 0)) for c in chapters]
    if numbers != list(range(1, len(chapters) + 1)):
        issues.append(_issue(
            "outline_numbering", "HIGH",
            "Chapter numbers are missing, duplicated or out of order.",
            "Number chapters sequentially starting at 1.",
        ))

    titles = [str(c.get("title", "")).strip().lower() for c in chapters]
    if len(set(titles)) != len(titles):
        issues.append(_issue(
            "outline_duplicate_titles", "HIGH",
            "Two or more chapters share the same title.",
            "Give every chapter a distinct, topic-specific title.",
        ))

    generic = [t for t in titles if t in _GENERIC_OUTLINE_TITLES]
    if generic:
        issues.append(_issue(
            "outline_generic_titles", "HIGH",
            "The outline reused template placeholder titles: " + ", ".join(generic[:4]),
            "Replace every placeholder with a title that names the specific customer problem and outcome for this niche.",
        ))

    format_claims = detect_software_claims(_blob(chapters))
    if format_family(product_format) == "book" and format_claims:
        issues.append(_issue(
            "outline_software_scope", "MEDIUM",
            "Outline topics drift into building or downloading software.",
            "Keep chapter topics inside the book's educational scope.",
        ))

    return not any(i["severity"] == "HIGH" for i in issues), issues


def validate_chapter(product_format: str, content: str) -> Tuple[bool, List[Dict[str, str]]]:
    issues: List[Dict[str, str]] = []
    if format_family(product_format) == "book":
        claims = detect_software_claims(content)
        if claims:
            issues.append(_issue(
                "chapter_software_promise", "HIGH",
                "Chapter promises or instructs the reader to use software: " + "; ".join(claims[:2]),
                "Rewrite those passages as in-book exercises or manual methods.",
            ))
    return not any(i["severity"] == "HIGH" for i in issues), issues


def validate_branding(product_format: str, data: Dict[str, Any]) -> Tuple[bool, List[Dict[str, str]]]:
    issues: List[Dict[str, str]] = []
    if format_family(product_format) == "book":
        blob = _blob({
            "cover": data.get("cover_concept", ""),
            "interior": data.get("interior_design_guidelines", ""),
            "story": data.get("brand_story", ""),
        })
        if detect_software_claims(blob):
            issues.append(_issue(
                "branding_software", "MEDIUM",
                "Branding copy describes an app/software product for a book project.",
                "Describe a book cover and interior reading experience.",
            ))
    return not any(i["severity"] == "HIGH" for i in issues), issues


def validate_sales(product_format: str, data: Dict[str, Any]) -> Tuple[bool, List[Dict[str, str]]]:
    issues: List[Dict[str, str]] = []
    if format_family(product_format) == "book":
        blob = _blob({
            "headline": data.get("headline", ""),
            "sub": data.get("subheadline", ""),
            "solution": data.get("solution_introduction", ""),
            "included": data.get("whats_included", []),
            "benefits": data.get("core_benefits", []),
        })
        claims = detect_software_claims(blob)
        if claims:
            issues.append(_issue(
                "sales_software_promise", "HIGH",
                "Sales copy promises software: " + "; ".join(claims[:3]),
                "Sell the book/workbook itself, not an application.",
            ))
    return not any(i["severity"] == "HIGH" for i in issues), issues


def corrective_instructions(product_format: str, issues: List[Dict[str, str]], stage: str) -> str:
    if not issues:
        return ""
    lines = [
        f"CORRECTION REQUIRED for the {stage}. Your previous response violated the product-format contract.",
        contract_text(product_format),
        "Fix every issue below and return the full corrected response:",
    ]
    for issue in issues:
        lines.append(f"- [{issue['severity']}] {issue['message']} Fix: {issue['correction']}")
    return "\n".join(lines)
