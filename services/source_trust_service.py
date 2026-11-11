"""Independent source-quality assessment.

User-declared reliability is kept in ``declared_reliability`` and is deliberately
excluded from the score.  ``assessed_reliability`` is computed only from
observable metadata and content signals: publisher, URL domain, study/report
language, methodology, dates, length and numeric evidence.

A user can therefore *label* a blog post "HIGH" without making it credible.  The
opposite is also true: a well-documented official dataset marked "LOW" by a
careless user can still be assessed as MEDIUM/HIGH by the application.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

from database.db import get_connection, transaction

UNASSESSED = "UNASSESSED"
HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"

_OFFICIAL_DOMAIN_SUFFIXES = (
    ".gov",
    ".gov.uk",
    ".gov.in",
    ".edu",
    ".ac.uk",
    ".who.int",
    ".worldbank.org",
    ".oecd.org",
    ".un.org",
    ".europa.eu",
    ".imf.org",
    ".census.gov",
    ".bls.gov",
)

_INSTITUTION_MARKERS = (
    "world bank", "oecd", "united nations", "u.s. census", "census bureau",
    "bureau of labor", "national statistics", "office for national statistics",
    "pew research", "statista", "nielsen", "gartner", "forrester", "mckinsey",
    "deloitte", "pwc", "kpmg", "ernst & young", "american dental association",
    "world health organization", "who", "imf", "european commission",
    "national bureau of economic research", "nber", "rand corporation",
)

_RESEARCH_MARKERS = (
    "peer-reviewed", "peer reviewed", "journal", "study", "survey", "dataset",
    "methodology", "sample size", "randomized", "longitudinal", "meta-analysis",
    "empirical", "statistically significant", "confidence interval", "report",
    "white paper", "working paper", "annual report",
)

_METHOD_MARKERS = (
    "methodology", "sample", "respondents", "participants", "dataset",
    "confidence interval", "statistically significant", "regression",
    "randomized", "longitudinal", "sample size",
)

_NUMBER_RE = re.compile(r"(?:₹|rs\.?|\$|us\$)\s?\d[\d,.]*|\d[\d,.]*\s?%|\b\d[\d,.]*\b", re.I)


def _text(value: Any) -> str:
    return str(value or "").strip()


def assess_source(source: Dict[str, Any]) -> Dict[str, Any]:
    """Return an assessment dict for a research-source row/dict.

    The declared reliability is recorded in the result for transparency but is
    never added to ``score``.
    """
    title = _text(source.get("title"))
    publisher = _text(source.get("publisher"))
    author = _text(source.get("author"))
    publication_date = _text(source.get("publication_date"))
    url = _text(source.get("url"))
    category = _text(source.get("source_category")).upper()
    content = _text(source.get("raw_content"))
    declared = _text(source.get("declared_reliability") or source.get("reliability_assessment")).upper() or UNASSESSED

    score = 0.0
    signals: list[str] = []

    lower_url = url.lower()
    if any(suffix in lower_url for suffix in _OFFICIAL_DOMAIN_SUFFIXES):
        score += 30
        signals.append("URL belongs to an official/statistical/education domain (+30).")
    elif lower_url.startswith("https://"):
        score += 3
        signals.append("Source uses HTTPS (+3).")

    lower_publisher = publisher.lower()
    if publisher and publisher.lower() not in ("unknown", "n/a", "none", ""):
        if any(marker in lower_publisher for marker in _INSTITUTION_MARKERS):
            score += 25
            signals.append("Publisher is a recognised institution/statistics body (+25).")
        else:
            score += 5
            signals.append("Publisher is named (+5).")

    lower_title_content = f"{title} {content[:4000]}".lower()
    research_hits = sum(1 for marker in _RESEARCH_MARKERS if marker in lower_title_content)
    if research_hits:
        points = min(20, 5 + research_hits * 3)
        score += points
        signals.append(f"Research/report language present ({research_hits} marker(s), +{points}).")

    method_hits = sum(1 for marker in _METHOD_MARKERS if marker in lower_title_content)
    if method_hits:
        points = min(15, 4 + method_hits * 3)
        score += points
        signals.append(f"Methodology/sample information present ({method_hits} marker(s), +{points}).")

    if author and author.lower() not in ("unknown", "n/a", "none", ""):
        score += 5
        signals.append("Named author (+5).")

    if publication_date and publication_date.lower() not in ("unknown", "n/a", "none", ""):
        score += 5
        signals.append("Publication date provided (+5).")

    if len(content) >= 5000:
        score += 10
        signals.append("Substantial source text (>= 5,000 chars, +10).")
    elif len(content) >= 2000:
        score += 6
        signals.append("Moderate source text (>= 2,000 chars, +6).")
    elif len(content) >= 800:
        score += 3
        signals.append("Some source text (>= 800 chars, +3).")

    numeric_hits = len(_NUMBER_RE.findall(content[:10000]))
    if numeric_hits >= 5:
        score += 5
        signals.append("Contains multiple quantitative data points (+5).")
    elif numeric_hits >= 1:
        score += 2
        signals.append("Contains quantitative data (+2).")

    if category in {"INDUSTRY_SURVEY", "JOURNAL_REPORT", "REPORT", "GOVERNMENT", "ACADEMIC", "DATASET"}:
        score += 5
        signals.append(f"Source category '{category}' is research-oriented (+5).")

    # Manual notes and unverified commentary are weak by construction.
    if category in {"MANUAL_NOTE", "PUBLIC_URL", "TEXT"} and not url:
        score = min(score, 20)
        signals.append("Manual/unstructured note without a public provenance URL (score capped at 20).")

    score = round(min(100.0, max(0.0, score)), 1)
    if score >= 55:
        assessed = HIGH
    elif score >= 30:
        assessed = MEDIUM
    elif score >= 10:
        assessed = LOW
    else:
        assessed = UNASSESSED

    # Never let the user's declaration influence the assessment.
    rationale = (
        f"Independently assessed from observable signals only (score {score}/100). "
        f"User-declared reliability '{declared}' was recorded but not used in this assessment. "
        + (" ".join(signals) if signals else "No credible provenance or research signals were found.")
    )

    return {
        "assessed_reliability": assessed,
        "assessment_score": score,
        "assessment_rationale": rationale,
        "declared_reliability": declared,
        "signals": signals,
        "assessed_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def save_assessment(conn, source_id: int, assessment: Dict[str, Any]) -> None:
    """Persist an assessment using an existing transaction connection."""
    conn.execute(
        """
        UPDATE research_sources
        SET assessed_reliability = ?, assessment_score = ?, assessment_json = ?,
            assessed_at = ?, reliability_assessment = ?
        WHERE id = ?
        """,
        (
            assessment["assessed_reliability"],
            assessment["assessment_score"],
            json.dumps(assessment),
            assessment["assessed_at"],
            assessment["assessed_reliability"],
            source_id,
        ),
    )


class SourceTrustService:
    @staticmethod
    def assess_and_store(source_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM research_sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            return None
        assessment = assess_source(dict(row))
        with transaction() as conn:
            save_assessment(conn, source_id, assessment)
        return assessment

    @staticmethod
    def assess_project_sources(project_id: int) -> int:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM research_sources WHERE project_id = ?", (project_id,)
            ).fetchall()
        assessments = [(int(row["id"]), assess_source(dict(row))) for row in rows]
        with transaction() as conn:
            for source_id, assessment in assessments:
                save_assessment(conn, source_id, assessment)
            conn.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (project_id, "SOURCE_TRUST_ASSESSED", f"Independently assessed {len(assessments)} source(s)."),
            )
        return len(assessments)

    @staticmethod
    def credibility_rank(reliability: str) -> int:
        return {HIGH: 3, MEDIUM: 2, LOW: 1}.get(str(reliability or "").upper(), 0)
