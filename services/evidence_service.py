"""Deterministic evidence verification for market opportunities.

The verifier deliberately separates three different things that used to be
conflated:

1. **Quote existence** — does the cited excerpt actually appear in the source?
2. **Claim alignment** — does the claim match the quoted excerpt?
3. **Factual support** — is the source independently assessed as credible?

Only all three together count as verified support.  A verbatim quote from an
unassessed or low-quality source is reported as *uncertain* and requires human
review; it is never presented as established fact.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from services.research_service import tokenize

IMPORTANT_FIELDS = {
    "demand_evidence",
    "willingness_to_pay_evidence",
    "existing_competitors",
    "observed_prices",
    "customer_complaints",
    "missing_features",
}

EVIDENCE_FIELDS = IMPORTANT_FIELDS | {"competitor_products"}

CREDIBLE_ASSESSMENTS = {"HIGH", "MEDIUM"}
SUPPORT_VERIFIED = "VERIFIED_SUPPORT"
SUPPORT_QUOTE_ONLY = "QUOTE_ONLY_UNCERTAIN"
SUPPORT_LOW = "LOW_QUALITY_SUPPORT"

_WS_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9%$₹.,\-\s]")
_NUMBER_RE = re.compile(
    r"(?:₹|rs\.?|\$|us\$)\s?\d[\d,.]*|\d[\d,.]*\s?%|\d[\d,.]*\s?(?:billion|million|crore|lakh|trillion)",
    re.I,
)


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = _NON_WORD_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _numeric_tokens(text: str) -> set[str]:
    return {normalize_text(m.group(0)).replace(" ", "") for m in _NUMBER_RE.finditer(text or "")}


def excerpt_supported(excerpt: str, source_text: str) -> Tuple[bool, float, str]:
    """Check quote existence only.  Returns (exists, coverage, note)."""
    norm_excerpt = normalize_text(excerpt)
    norm_source = normalize_text(source_text)
    if not norm_excerpt:
        return False, 0.0, "No excerpt supplied."
    if len(norm_excerpt) < 25:
        return False, 0.0, "Excerpt is too short to verify."
    if norm_excerpt in norm_source:
        return True, 1.0, "Excerpt found verbatim in source."

    excerpt_tokens = {t for t in tokenize(excerpt) if len(t) >= 5}
    source_tokens = tokenize(source_text)
    if len(excerpt_tokens) < 5:
        return False, 0.0, "Excerpt has too few distinctive tokens to verify."
    covered = len(excerpt_tokens & source_tokens) / len(excerpt_tokens)
    numbers = _numeric_tokens(excerpt)
    numbers_ok = all(n in normalize_text(source_text) for n in numbers) if numbers else True
    if covered >= 0.80 and numbers_ok:
        return True, round(covered, 3), f"Excerpt tokens match source ({covered:.0%} coverage)."
    return False, round(covered, 3), "Excerpt was not found in the cited source."


def source_relevance(
    opportunity: Dict[str, Any],
    source_text: str,
    claim: str = "",
    source_title: str = "",
    excerpt: str = "",
) -> Tuple[float, str]:
    """Measure topic relevance with a stricter, multi-signal check.

    Whole-source overlap can be inflated by long documents.  We therefore also
    compare the opportunity to the source title and to the local excerpt, and
    require at least two distinctive shared terms.  This prevents a large,
    loosely-related document from accepting an unrelated claim by keyword
    accident.
    """
    opportunity_text = " ".join(
        str(opportunity.get(k, ""))
        for k in ("name", "exact_customer", "customer_problem", "desired_outcome", "potential_solution")
    )
    opp_tokens = tokenize(opportunity_text)
    src_tokens = tokenize(source_text)
    if not opp_tokens or not src_tokens:
        return 0.0, "No comparable vocabulary."

    overlap = opp_tokens & src_tokens
    title_overlap = opp_tokens & tokenize(source_title)
    excerpt_overlap = opp_tokens & tokenize(excerpt)

    # Distinctive shared terms in the source body plus title/excerpt signals.
    if len(overlap) < 2 and len(title_overlap) < 1 and len(excerpt_overlap) < 1:
        return 0.0, "Source shares fewer than two distinctive terms with the opportunity."

    score = len(overlap) / max(len(opp_tokens), 1)
    score += 0.10 * min(len(title_overlap), 3)
    score += 0.15 * min(len(excerpt_overlap), 3)

    if claim:
        claim_tokens = tokenize(claim)
        claim_overlap = claim_tokens & src_tokens
        if len(claim_overlap) >= 2:
            score += 0.10
        elif _numeric_tokens(claim) and _numeric_tokens(claim) & _numeric_tokens(source_text):
            score += 0.10
        else:
            # A claim with no lexical connection to the source is not relevant,
            # even if the opportunity topic is.
            return 0.0, "Claim shares no meaningful terms with the cited source."

    terms = sorted(overlap or (title_overlap | excerpt_overlap))
    return min(score, 1.0), f"Shares terms: {', '.join(terms[:8])}"


def claim_support_level(
    *,
    quote_exists: bool,
    relevance: float,
    claim_aligned: bool,
    assessed_reliability: str,
) -> str:
    if not quote_exists or relevance <= 0.0 or not claim_aligned:
        return "UNSUPPORTED"
    assessed = str(assessed_reliability or "UNASSESSED").upper()
    if assessed in CREDIBLE_ASSESSMENTS:
        return SUPPORT_VERIFIED
    if assessed == "LOW":
        return SUPPORT_LOW
    return SUPPORT_QUOTE_ONLY


def verify_claims(opportunity: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    source_map = {int(s["id"]): s for s in sources}
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for item in opportunity.get("evidence", []) or []:
        field = str(item.get("field", "")).strip() or "demand_evidence"
        source_id = item.get("source_id")
        excerpt = str(item.get("excerpt", "") or "")
        claim = str(item.get("claim", "") or "")

        base = {
            "field": field,
            "source_id": source_id,
            "excerpt": excerpt,
            "claim": claim,
        }

        try:
            source_id = int(source_id)
        except (TypeError, ValueError):
            rejected.append({**base, "verified": False, "relevance_score": 0.0,
                             "notes": "Missing or non-numeric source ID."})
            continue

        source = source_map.get(source_id)
        if not source:
            rejected.append({**base, "source_id": source_id, "verified": False,
                             "relevance_score": 0.0,
                             "notes": f"[Source-{source_id}] does not exist in this project."})
            continue

        quote_exists, excerpt_score, excerpt_note = excerpt_supported(
            excerpt, source.get("raw_content") or ""
        )
        if not quote_exists:
            rejected.append({**base, "source_id": source_id, "verified": False,
                             "relevance_score": excerpt_score,
                             "quote_exists": False,
                             "notes": excerpt_note})
            continue

        relevance, relevance_note = source_relevance(
            opportunity,
            source.get("raw_content") or "",
            claim or excerpt,
            source_title=source.get("title", ""),
            excerpt=excerpt,
        )
        if relevance <= 0.0:
            rejected.append({**base, "source_id": source_id, "verified": False,
                             "relevance_score": relevance,
                             "quote_exists": True,
                             "notes": "Source is not relevant to this opportunity. " + relevance_note})
            continue

        # A claim must be connected to the excerpt, not just pasted next to it.
        claim_tokens = {t for t in tokenize(claim) if len(t) >= 5}
        excerpt_tokens = tokenize(excerpt)
        claim_aligned = not claim_tokens or len(claim_tokens & excerpt_tokens) >= 2
        if not claim_aligned:
            rejected.append({**base, "source_id": source_id, "verified": False,
                             "relevance_score": relevance,
                             "quote_exists": True,
                             "notes": "Claim does not match the quoted excerpt."})
            continue

        declared = str(
            source.get("declared_reliability") or source.get("reliability_assessment") or "UNASSESSED"
        ).upper()
        assessed = str(
            source.get("assessed_reliability") or source.get("reliability_assessment") or "UNASSESSED"
        ).upper()
        support_level = claim_support_level(
            quote_exists=quote_exists,
            relevance=relevance,
            claim_aligned=claim_aligned,
            assessed_reliability=assessed,
        )
        uncertain = support_level != SUPPORT_VERIFIED

        accepted.append({
            **base,
            "source_id": source_id,
            "verified": True,
            "quote_exists": True,
            "support_level": support_level,
            "credible": not uncertain,
            "relevance_score": round((excerpt_score + relevance) / 2, 3),
            "notes": (
                f"{excerpt_note} Relevant source: {relevance_note} "
                f"Declared reliability: {declared} (not evidence). "
                f"Independently assessed: {assessed}. "
                + (
                    "Factual support is uncertain; HUMAN REVIEW REQUIRED."
                    if uncertain else "Independently assessed source supports the claim."
                )
            ),
            "declared_reliability": declared,
            "assessed_reliability": assessed,
            "reliability": assessed,
            "source_title": source.get("title", ""),
        })

    verified_fields = sorted({a["field"] for a in accepted})
    credible_accepted = [a for a in accepted if a.get("credible")]
    credible_fields = sorted({a["field"] for a in credible_accepted})
    credible_sources = sorted({
        a["source_id"] for a in credible_accepted
        if str(a.get("assessed_reliability", "UNASSESSED")).upper() in CREDIBLE_ASSESSMENTS
    })
    uncertain = [a for a in accepted if not a.get("credible")]
    return {
        "accepted": accepted,
        "rejected": rejected,
        "verified_fields": verified_fields,
        "credible_fields": credible_fields,
        "credible_source_ids": credible_sources,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "quote_only_count": len(uncertain),
        "human_review_required": bool(uncertain),
        "support_summary": {
            a["field"]: a["support_level"] for a in accepted
        },
    }


def sanitize_fields(opportunity: Dict[str, Any], accepted: List[Dict[str, Any]]) -> Dict[str, str]:
    """Replace unsupported market claims with an explicit hypothesis label.

    Only independently credible support is allowed to populate a factual field.
    Quote-only support is surfaced as a human-review item, not as evidence.
    """
    by_field: Dict[str, List[Dict[str, Any]]] = {}
    for item in accepted:
        if item.get("credible") and item.get("support_level") == SUPPORT_VERIFIED:
            by_field.setdefault(item["field"], []).append(item)

    result: Dict[str, str] = {}
    for field in EVIDENCE_FIELDS:
        items = by_field.get(field)
        if items:
            claims = []
            for item in items[:3]:
                text = (item.get("claim") or item.get("excerpt") or "").strip()
                if text:
                    claims.append(f"{text} [Source-{item['source_id']}]")
            result[field] = " ".join(claims)
        else:
            result[field] = (
                "Unverified hypothesis — no independently assessed source supports this claim. "
                "Human review required before treating it as fact."
            )
    return result
