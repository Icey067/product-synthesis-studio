"""Structured factual-claim verification for publication quality (P2.2).

A claim is extracted from chapter prose and stored with its type, chapter and
section, cited source IDs, exact supporting excerpts, verification status,
confidence and human-review state.  Numerical claims are checked for the exact
number, unit, population, geography, time period and meaning.  A source that
merely contains the same number does **not** automatically verify a claim.

Everything in this module is deterministic.  No language model can mark a claim
as verified.  The module never invents a replacement number: an unsupported
figure is either left unsupported, qualified or sent to human review.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from database.db import get_connection, transaction
from services.evidence_service import normalize_text
from services.research_service import tokenize

# ---------------------------------------------------------------------------
# Claim taxonomy
# ---------------------------------------------------------------------------
CLAIM_TYPES: Dict[str, str] = {
    "NUMERICAL_STATISTIC": "Numerical statistic",
    "MARKET_CLAIM": "Market claim",
    "SCIENTIFIC_CLAIM": "Scientific claim",
    "BUSINESS_PERFORMANCE": "Business performance claim",
    "HISTORICAL_FACT": "Historical fact",
    "GENERAL_FACT": "General factual statement",
    "RECOMMENDATION": "Recommendation",
    "HYPOTHESIS": "Hypothesis",
    "ILLUSTRATIVE_EXAMPLE": "Illustrative example",
    "USER_ASPIRATION": "User-defined aspiration",
}

EVIDENCE_REQUIRED_TYPES = {
    "NUMERICAL_STATISTIC",
    "MARKET_CLAIM",
    "SCIENTIFIC_CLAIM",
    "BUSINESS_PERFORMANCE",
    "HISTORICAL_FACT",
    "GENERAL_FACT",
}

VERIFICATION_STATUSES = {
    # P2.2 lifecycle labels are retained alongside the existing deterministic
    # outcomes so old audit records remain readable.
    "CANDIDATE",
    "QUOTE_FOUND",
    "NEEDS_HUMAN_REVIEW",
    "HUMAN_VERIFIED",
    "REJECTED",
    "VERIFIED",
    "PARTIALLY_VERIFIED",
    "UNSUPPORTED",
    "HUMAN_REVIEW",
    "QUALIFIED",
    "NOT_APPLICABLE",
    "UNVERIFIED",
    # P2.3.4 strict evidence pipeline.  Lexical overlap alone can never
    # certify a claim: the deterministic ceiling is EXCERPT_MATCHED, and only
    # a recorded human decision can promote a claim to HUMAN_VERIFIED.
    "SOURCE_FOUND",
    "EXCERPT_MATCHED",
    "PARTIALLY_SUPPORTED",
    "HUMAN_REVIEW_REQUIRED",
}

HUMAN_REVIEW_STATUSES = {"NOT_REQUIRED", "REQUIRED", "PENDING", "APPROVED", "REJECTED"}

RELIABILITY_RANK = {
    "HIGH": 1.0,
    "MEDIUM": 0.75,
    "LOW": 0.4,
    "UNASSESSED": 0.35,
    "UNKNOWN": 0.35,
}

# ---------------------------------------------------------------------------
# Numeric patterns
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(
    r"(?P<currency>(?:₹|inr|rs\.?|\$|us\$|usd)\s?\d[\d,]*(?:\.\d+)?)"
    r"|(?P<percent>\d[\d,]*(?:\.\d+)?\s?(?:%|percent|per\s+cent))"
    r"|(?P<magnitude>\d[\d,]*(?:\.\d+)?\s?(?:billion|million|crore|lakh|trillion|thousand)\b)"
    r"|(?P<ratio>\d[\d,]*(?:\.\d+)?\s?(?:x|times)\b)"
    r"|(?P<count>\b\d[\d,]*(?:\.\d+)?\s?"
    r"(?:customers?|clients?|patients?|users?|orders?|employees?|businesses?|"
    r"firms?|stores?|shops?|households?|respondents?|participants?|people|"
    r"small businesses?|msmes?|vendors?|sellers?|buyers?|subscribers?|members?|"
    r"clinics?|salons?|families|family|consumers?|merchants?|retailers?|"
    r"entrepreneurs?|workers?|owners?|teams?|branches?|outlets?)\b)",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PLAIN_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")

_POPULATION_TERMS = {
    "customers", "customer", "clients", "client", "patients", "patient",
    "users", "user", "respondents", "participants", "businesses", "business",
    "firms", "firm", "stores", "store", "shops", "shop", "households",
    "household", "employees", "employee", "workers", "worker", "owners",
    "owner", "women", "men", "adults", "adult", "msmes", "msme", "vendors",
    "vendor", "sellers", "seller", "buyers", "buyer", "subscribers",
    "subscriber", "members", "member", "consumers", "consumer", "smbs", "smb",
    "entrepreneurs", "entrepreneur", "retailers", "retailer", "clinics",
    "clinic", "salons", "salon", "families", "family",
}

_GEOGRAPHY_TERMS = {
    "india", "indian", "delhi", "mumbai", "bangalore", "bengaluru", "chennai",
    "kolkata", "hyderabad", "pune", "gujarat", "maharashtra", "karnataka",
    "kerala", "tamil", "rajasthan", "up", "uttar", "bihar", "punjab",
    "united", "states", "usa", "us", "uk", "britain", "europe", "africa",
    "asia", "global", "worldwide", "rural", "urban", "metro", "metropolitan",
    "tier", "state", "district", "region", "country", "countries",
}

_MONTHS = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}
_TIME_WORDS = {
    "year", "years", "month", "months", "week", "weeks", "day", "days",
    "quarter", "quarters", "q1", "q2", "q3", "q4", "annual", "annually",
    "monthly", "weekly", "daily", "fy", "fiscal", "last", "this", "next",
    "recent", "recently", "today", "currently",
}

_UNIT_FAMILIES: List[Tuple[str, str]] = [
    ("percent", "PERCENT"),
    ("per cent", "PERCENT"),
    ("%", "PERCENT"),
    ("crore", "CURRENCY"),
    ("lakh", "CURRENCY"),
    ("billion", "MAGNITUDE"),
    ("million", "MAGNITUDE"),
    ("trillion", "MAGNITUDE"),
    ("thousand", "MAGNITUDE"),
    ("rupee", "CURRENCY"),
    ("rupees", "CURRENCY"),
    ("inr", "CURRENCY"),
    ("usd", "CURRENCY"),
    ("dollar", "CURRENCY"),
    ("dollars", "CURRENCY"),
    ("₹", "CURRENCY"),
    ("$", "CURRENCY"),
    ("times", "RATIO"),
    ("x", "RATIO"),
    ("days", "DURATION"),
    ("day", "DURATION"),
    ("weeks", "DURATION"),
    ("week", "DURATION"),
    ("months", "DURATION"),
    ("month", "DURATION"),
    ("years", "DURATION"),
    ("year", "DURATION"),
    ("hours", "DURATION"),
    ("hour", "DURATION"),
]

_STRONG_RECOMMENDATION = re.compile(
    r"\b(should|must|recommend|recommended|start by|begin by|avoid|never|"
    r"make sure|ensure that|use the|apply the|do not|don't|instead,|"
    r"your next step|action step)\b",
    re.IGNORECASE,
)
_HYPOTHESIS = re.compile(
    r"\b(may|might|could|possibly|perhaps|hypothesis|hypothesise|hypothesize|"
    r"it is likely|we suspect|one possibility)\b",
    re.IGNORECASE,
)
_EXAMPLE = re.compile(
    r"\b(for example|for instance|imagine|suppose|consider a|consider an|"
    r"say you|let's say|as an illustration|illustrative example)\b",
    re.IGNORECASE,
)
# Sample copy the model writes inside examples (a caption, a promo message, a
# template).  A quoted snippet containing a figure is illustrative, not a
# research statistic.
_QUOTED_SAMPLE = re.compile(r'"[^"]{0,240}\d[^"]{0,240}"')
# Reader-facing targets/instructions ("Resolve at least 80% ... within 24
# hours") are guidance for the reader, not externally verifiable statistics.
_TARGET_INSTRUCTION = re.compile(
    r"\b(at least|no more than|up to|within \d+\s?(?:hours|days|weeks|months)|"
    r"e\.g\.|i\.e\.|aim for|try to)\b",
    re.IGNORECASE,
)
_IMPERATIVE_START = re.compile(
    r"^(?:resolve|set|get|use|monitor|compare|ensure|add|create|reply|respond|"
    r"avoid|keep|make|choose|write|schedule|review|track|share|post|ask|check|"
    r"verify|complete|prepare|list|call|send|aim|achieve|reach|maintain|"
    r"include|greet|welcome|offer|announce|provide|record|note|mark|assign|"
    r"pick|select|follow|start|begin|run|conduct|measure|reduce|increase|"
    r"improve|automate|enable|disable|update|publish|build|apply|do)\b",
    re.IGNORECASE,
)
# Promo/sample copy such as "Get 10% off on your next purchase."
_PROMO_COPY = re.compile(
    r"\b(?:enjoy|get|save|flat|grab)\s+\d+(?:\.\d+)?\s?%|%\s?off\b",
    re.IGNORECASE,
)
_ASPIRATION = re.compile(
    r"\b(target|targets|goal|goals|aim|aims|aspire|aspiration|objective|"
    r"intend|intends|intention|plan to|want to|hope to|expect to reach|"
    r"our goal|your goal)\b"
    r"|\b(?:this|the|our|your)\s+(?:plan|program|programme|approach|system|"
    r"strategy|framework|method)\s+will\b"
    r"|\bwill\s+(?:deliver|help you (?:achieve|reach|get|earn|gain)|"
    r"enable you to|allow you to)\b",
    re.IGNORECASE,
)
_GUARANTEE = re.compile(
    r"\b(will|guaranteed|guarantees|results? in|delivers?|ensures?|"
    r"you can expect|expect a|achieve[sd]?|generates?|produces?|"
    r"increases?|improves?|boosts?|reduces?|drives?)\b",
    re.IGNORECASE,
)
_MARKET = re.compile(
    r"\b(market|markets|industry|demand|customer|competitor|pricing|"
    r"revenue|sales|segment|niche|supply|consumer)\b",
    re.IGNORECASE,
)
_SCIENTIFIC = re.compile(
    r"\b(study|studies|research|survey|data|evidence|clinical|scientific|"
    r"statistically|peer[- ]reviewed|experiment|sample|report findings)\b",
    re.IGNORECASE,
)
_BUSINESS_PERFORMANCE = re.compile(
    r"\b(retention|churn|conversion|engagement|repeat sales|repeat purchase|"
    r"profit|margin|revenue|growth|roi|return on|productivity|"
    r"cost saving|cost savings|sales lift|click[- ]through)\b",
    re.IGNORECASE,
)
_HISTORICAL = re.compile(
    r"\b(in (?:19|20)\d{2}|since (?:19|20)\d{2}|during (?:19|20)\d{2}|"
    r"historically|was founded|were introduced|previously|in the past)\b",
    re.IGNORECASE,
)
_FACTUAL = re.compile(
    r"\b(according to|research shows|studies show|data shows|the report|"
    r"the survey|statistics|measured|reported|found that|estimated that|"
    r"account(?:s|ed)? for|represent(?:s|ed)?|percent of|percentage of)\b",
    re.IGNORECASE,
)
_EXTERNAL_VERIFIABLE = re.compile(
    r"\b(is|are|was|were|has|have|had|accounts?|represents?|includes?|"
    r"consists? of|equals?|totals?|measures?|reports?)\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?!\[)|(?<=\])\s+|\n+")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

MIN_CLAIM_WORDS = 7
EXCERPT_CHARS = 700
# Relevance floor: below this the cited excerpt is not even topically related.
MEANING_MIN_COVERAGE = 0.18
MEANING_MIN_TOKENS = 2
# Strict pipeline (P2.3.4).  A verdict may never rest on lexical overlap
# alone, so overlap only classifies *how far* the evidence reaches:
#   >= 0.60 -> EXCERPT_MATCHED (pending human confirmation)
#   >= 0.30 -> PARTIALLY_SUPPORTED
#   >= 0.18 -> SOURCE_FOUND (source exists, excerpt is only topically related)
#   <  0.18 -> SOURCE_FOUND with an explicit "excerpt does not support" note
EXCERPT_MATCH_MIN_COVERAGE = 0.60
PARTIAL_MATCH_MIN_COVERAGE = 0.30


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------
def _words(text: str) -> List[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]{1,}", text or "")]


def _numbers(text: str) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    for match in _NUM_RE.finditer(text or ""):
        raw = match.group(0).strip()
        kind = match.lastgroup or "count"
        number_match = re.search(r"\d[\d,]*(?:\.\d+)?", raw)
        if not number_match:
            continue
        number_text = number_match.group(0).replace(",", "")
        try:
            value = float(number_text)
        except ValueError:
            continue
        # Plain years are dates, not statistics, unless written as a percentage
        # or currency amount.
        if kind == "count" and value < 100 and "." not in number_text:
            # Small counts (for example "3 customers") are still claims, but the
            # regex already restricted the noun list; keep them.
            pass
        unit = raw[number_match.end():].strip().lower()
        found.append(
            {
                "raw": raw,
                "value": value,
                "number": number_text,
                "unit": unit,
                "kind": kind,
                "family": _unit_family(unit or kind),
                "position": match.start(),
            }
        )
    return found


def _unit_family(unit: str) -> str:
    unit = (unit or "").strip().lower()
    for token, family in _UNIT_FAMILIES:
        if token and token in unit:
            return family
    if unit in {"percent", "percentage"}:
        return "PERCENT"
    if unit in {"count", "currency"}:
        return unit.upper()
    return "COUNT"


def _extract_dimension(claim_text: str, vocabulary: set[str]) -> List[str]:
    words = _words(claim_text)
    return sorted({w for w in words if w in vocabulary})


def _extract_time(claim_text: str) -> List[str]:
    found = set(re.findall(r"\b(?:19|20)\d{2}\b", claim_text or ""))
    words = _words(claim_text)
    found.update(w for w in words if w in _MONTHS or w in _TIME_WORDS)
    return sorted(found)


def _meaningful_tokens(text: str) -> set[str]:
    return {t for t in tokenize(text or "") if len(t) > 2}


def _coverage(claim_text: str, source_text: str) -> float:
    claim_tokens = _meaningful_tokens(claim_text)
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & _meaningful_tokens(source_text)) / len(claim_tokens)


def _best_excerpt(source_text: str, claim_text: str, numbers: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """Return the exact best supporting excerpt plus deterministic check flags."""
    text = (source_text or "").strip()
    if not text:
        return "", {"exact_number": False, "found_numbers": [], "coverage": 0.0}
    source_numbers = _numbers(text)
    claim_numbers = numbers
    matched: List[Dict[str, Any]] = []
    for claim_number in claim_numbers:
        match = None
        for source_number in source_numbers:
            if abs(source_number["value"] - claim_number["value"]) < 1e-9:
                match = source_number
                if source_number["family"] == claim_number["family"]:
                    break
        matched.append(
            {
                "claim_raw": claim_number["raw"],
                "claim_value": claim_number["value"],
                "claim_family": claim_number["family"],
                "source_raw": match["raw"] if match else "",
                "source_family": match["family"] if match else "",
                "number_found": match is not None,
                "unit_family_match": bool(match and match["family"] == claim_number["family"]),
            }
        )

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    claim_tokens = _meaningful_tokens(claim_text)
    best_index = 0
    best_score = -1.0
    for index, sentence in enumerate(sentences):
        score = _coverage(claim_text, sentence)
        if any(abs(n["value"] - c["value"]) < 1e-9 for n in _numbers(sentence) for c in claim_numbers):
            score += 1.0
        if score > best_score:
            best_score = score
            best_index = index
    if not sentences:
        return text[:EXCERPT_CHARS], {
            "exact_number": bool(claim_numbers) and all(m["number_found"] for m in matched),
            "found_numbers": matched,
            "coverage": _coverage(claim_text, text),
            "all_numbers_present": bool(claim_numbers) and all(m["number_found"] for m in matched),
            "all_units_match": bool(claim_numbers) and all(m["unit_family_match"] for m in matched),
        }

    # Anchor the excerpt on the best-matching sentence first, then extend the
    # window with adjacent sentences while the character budget lasts.  A hard
    # left-truncation must never cut the matched sentence out of the excerpt.
    best_text = sentences[best_index]
    budget = max(EXCERPT_CHARS, len(best_text) + 1)
    excerpt = best_text
    after = best_index + 1
    while after < len(sentences) and len(excerpt) + 1 + len(sentences[after]) <= budget:
        excerpt = excerpt + " " + sentences[after]
        after += 1
    before = best_index - 1
    while before >= 0 and len(sentences[before]) + 1 + len(excerpt) <= budget:
        excerpt = sentences[before] + " " + excerpt
        before -= 1
    if len(excerpt) > EXCERPT_CHARS:
        excerpt = excerpt[:EXCERPT_CHARS].rsplit(" ", 1)[0] + "…"
    excerpt_numbers = _numbers(excerpt)
    all_present = bool(claim_numbers) and all(
        any(abs(n["value"] - c["value"]) < 1e-9 for n in excerpt_numbers)
        for c in claim_numbers
    )
    all_units = bool(claim_numbers) and all(
        any(
            abs(n["value"] - c["value"]) < 1e-9 and n["family"] == c["family"]
            for n in excerpt_numbers
        )
        for c in claim_numbers
    )
    return excerpt, {
        "exact_number": all_present,
        "found_numbers": matched,
        "coverage": round(_coverage(claim_text, excerpt), 3),
        "all_numbers_present": all_present,
        "all_units_match": all_units,
    }


def _source_reliability(source: Dict[str, Any]) -> float:
    value = (
        source.get("assessed_reliability")
        or source.get("reliability_assessment")
        or source.get("declared_reliability")
        or "UNASSESSED"
    )
    return RELIABILITY_RANK.get(str(value).upper(), 0.35)


# ---------------------------------------------------------------------------
# Provenance, attribution and causal-wording checks (P2.3.4)
# ---------------------------------------------------------------------------
_NAMED_INSTITUTIONS: List[Tuple[str, str]] = [
    (r"\breserve bank of india\b|\brbi\b", "Reserve Bank of India"),
    (r"\bnpci\b|national payments? corporation", "NPCI"),
    (r"\bgoogle\b", "Google"),
    (r"\bsebi\b", "SEBI"),
    (r"\btrai\b", "TRAI"),
    (r"\bministry of [a-z ]{3,40}", "Ministry"),
    (r"\bgovernment of india\b", "Government of India"),
    (r"\bworld health organization\b|\bwho\b", "WHO"),
    (r"\bworld bank\b", "World Bank"),
    (r"\bniti aayog\b", "NITI Aayog"),
    (r"\bims?\b|international monetary fund", "IMF"),
    (r"\bunesco\b", "UNESCO"),
    (r"\bnpci\b", "NPCI"),
]
_SUBMISSION_PHRASE = re.compile(
    r"\b(?:submitted|presented|filed|sent|lodged|delivered)\s+(?:to|with|before)\s+"
    r"(?:the\s+)?(?P<entity>[A-Z][A-Za-z]*(?:\s+(?:of|for|and)?\s*[A-Z][A-Za-z]*){0,6})",
)
_CAUSAL_PHRASE = re.compile(
    r"\b(?:causes?|caused by|causing|leads? to|led to|results? in|resulted in|"
    r"because of|due to|drives?|proves? that)\b",
    re.IGNORECASE,
)
_CORRELATION_HINT = re.compile(
    r"\b(?:associated with|correlat|linked to|tends? to|suggests?|"
    r"perceived|perception|survey|respondents? (?:said|reported|stated))\b",
    re.IGNORECASE,
)


def _haystack_for(source: Dict[str, Any], excerpt: str) -> str:
    parts = [
        source.get("title") or "",
        source.get("publisher") or "",
        source.get("url") or "",
        excerpt or "",
        (source.get("raw_content") or "")[:4000],
    ]
    return " ".join(str(p) for p in parts)


def provenance_check(
    claim_text: str,
    evidence: List[Dict[str, Any]],
    sources: Dict[int, Dict[str, Any]],
) -> Dict[str, Any]:
    """Deterministic provenance screen.

    Catches (a) claims that name an institution the cited source never
    mentions, (b) "submitted/presented to X" phrasing that the source does not
    support, and (c) causal wording applied to sources that only report
    association or perception.  None of these decide the verdict alone; they
    force human review when they fail.
    """
    text = claim_text or ""
    named: List[str] = []
    for pattern, label in _NAMED_INSTITUTIONS:
        if re.search(pattern, text, re.IGNORECASE) and label not in named:
            named.append(label)
    submission = _SUBMISSION_PHRASE.search(text)
    submission_entity = (submission.group("entity").strip() if submission else "")
    causal_claim = bool(_CAUSAL_PHRASE.search(text))

    combined = " ".join(
        _haystack_for(sources.get(int(e.get("source_id") or 0), {}), e.get("excerpt", ""))
        for e in evidence
    )
    combined_lower = combined.lower()
    missing = [
        label for label in named
        if label.lower() not in combined_lower
    ]
    submission_supported = True
    if submission_entity:
        # The named recipient must appear in the source text or metadata.
        submission_supported = submission_entity.lower() in combined_lower

    causal_risk = False
    if causal_claim and evidence:
        excerpts = " ".join(e.get("excerpt", "") for e in evidence)
        causal_risk = not _CAUSAL_PHRASE.search(excerpts) and bool(_CORRELATION_HINT.search(excerpts))

    quote_match = False
    if evidence:
        bare = re.sub(r"\[(?:Source|Ref)-\d+\]", "", text, flags=re.IGNORECASE)
        bare = re.sub(r"\s+", " ", bare).strip(" *#").lower()
        for e in evidence:
            excerpt_norm = re.sub(r"\s+", " ", e.get("excerpt", "")).lower()
            if bare and bare in excerpt_norm:
                quote_match = True
                break

    return {
        "named_institutions": named,
        "missing_institutions": missing,
        "submission_entity": submission_entity,
        "submission_supported": submission_supported,
        "causal_wording": causal_claim,
        "causal_risk": causal_risk,
        "quote_match": quote_match,
    }



def _classify(text: str, numbers: List[Dict[str, Any]], cited: bool) -> str:
    if _ASPIRATION.search(text):
        return "USER_ASPIRATION"
    if numbers:
        if _EXAMPLE.search(text) or _QUOTED_SAMPLE.search(text) or _PROMO_COPY.search(text):
            return "ILLUSTRATIVE_EXAMPLE"
        if _TARGET_INSTRUCTION.search(text) and _IMPERATIVE_START.match(text.strip()):
            return "RECOMMENDATION"
        return "NUMERICAL_STATISTIC"
    if _EXAMPLE.search(text):
        return "ILLUSTRATIVE_EXAMPLE"
    if _HYPOTHESIS.search(text):
        return "HYPOTHESIS"
    if _STRONG_RECOMMENDATION.search(text):
        return "RECOMMENDATION"
    if _HISTORICAL.search(text):
        return "HISTORICAL_FACT"
    if _SCIENTIFIC.search(text):
        return "SCIENTIFIC_CLAIM"
    if _BUSINESS_PERFORMANCE.search(text):
        return "BUSINESS_PERFORMANCE"
    if _MARKET.search(text) and _FACTUAL.search(text):
        return "MARKET_CLAIM"
    if _FACTUAL.search(text) or cited:
        return "GENERAL_FACT"
    return "GENERAL_FACT"


def _refine_type(claim_text: str, claim_type: str) -> str:
    """Never record clearly illustrative/aspirational prose as a statistic.

    Callers may pass a provisional type; quoted sample copy, promo snippets and
    reader-facing target instructions are relabelled before verification so a
    worksheet figure is never treated as a fabricated research statistic.
    """
    if claim_type != "NUMERICAL_STATISTIC":
        return claim_type
    if _ASPIRATION.search(claim_text or ""):
        return "USER_ASPIRATION"
    if _EXAMPLE.search(claim_text) or _QUOTED_SAMPLE.search(claim_text) or _PROMO_COPY.search(claim_text):
        return "ILLUSTRATIVE_EXAMPLE"
    if _TARGET_INSTRUCTION.search(claim_text) and _IMPERATIVE_START.match((claim_text or "").strip()):
        return "RECOMMENDATION"
    return claim_type


def _is_candidate(text: str, numbers: List[Dict[str, Any]], cited: bool, claim_type: str) -> bool:
    word_count = len(text.split())
    if word_count < MIN_CLAIM_WORDS:
        return False
    if numbers:
        return True
    if cited:
        return True
    if claim_type in {"MARKET_CLAIM", "SCIENTIFIC_CLAIM", "BUSINESS_PERFORMANCE", "HISTORICAL_FACT"}:
        return bool(_FACTUAL.search(text) or _EXTERNAL_VERIFIABLE.search(text))
    if claim_type in {"RECOMMENDATION", "HYPOTHESIS", "ILLUSTRATIVE_EXAMPLE", "USER_ASPIRATION"}:
        return True
    return bool(_FACTUAL.search(text))


def _claim_key(text: str) -> str:
    normalized = normalize_text(text)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:20]


def _extract_citations(text: str) -> List[int]:
    return sorted({int(x) for x in re.findall(r"\[(?:Source|Ref)-(\d+)\]", text or "", re.IGNORECASE)})


def _heading_to_section(heading: str, section_map: Optional[Dict[str, str]] = None) -> str:
    if section_map:
        lookup = {normalize_text(k): v for k, v in section_map.items()}
        found = lookup.get(normalize_text(heading))
        if found:
            return found
    return normalize_text(heading).replace(" ", "_") or "body"


def _sentences_with_sections(
    content: str,
    section_map: Optional[Dict[str, str]] = None,
) -> List[Tuple[str, str]]:
    """Return ``(sentence, section_key)`` pairs, skipping headings and lists."""
    result: List[Tuple[str, str]] = []
    section = "body"
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            section = _heading_to_section(heading.group(1), section_map)
            continue
        line = _LIST_MARKER.sub("", line)
        for sentence in _SENTENCE_SPLIT.split(line):
            sentence = sentence.strip()
            if len(sentence.split()) >= MIN_CLAIM_WORDS:
                result.append((sentence, section))
    return result


class ClaimService:
    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    @classmethod
    def analyze_claim(
        cls,
        claim_text: str,
        claim_type: str,
        cited_ids: List[int],
        sources: Dict[int, Dict[str, Any]],
    ) -> Dict[str, Any]:
        # A caller may pass a provisional type, but an aspirational promise or
        # sample/example copy must never be recorded as an external statistic.
        claim_type = _refine_type(claim_text, claim_type)
        numbers = _numbers(claim_text)
        population = _extract_dimension(claim_text, _POPULATION_TERMS)
        geography = _extract_dimension(claim_text, _GEOGRAPHY_TERMS)
        time_period = _extract_time(claim_text)

        evidence: List[Dict[str, Any]] = []
        for source_id in cited_ids:
            source = sources.get(int(source_id))
            if not source:
                continue
            excerpt, flags = _best_excerpt(source.get("raw_content") or "", claim_text, numbers)
            evidence.append(
                {
                    "source_id": int(source_id),
                    "title": source.get("title", ""),
                    "publisher": source.get("publisher", ""),
                    "url": source.get("url", ""),
                    "reliability": str(
                        source.get("assessed_reliability")
                        or source.get("reliability_assessment")
                        or "UNASSESSED"
                    ).upper(),
                    "reliability_score": _source_reliability(source),
                    "excerpt": excerpt,
                    **flags,
                }
            )

        number_check = {
            "numbers": numbers,
            "all_present": bool(numbers) and bool(evidence) and all(e.get("all_numbers_present") for e in evidence),
            "any_present": any(e.get("exact_number") for e in evidence),
        }
        unit_check = {
            "units": [n["unit"] or n["kind"] for n in numbers],
            "all_match": bool(numbers) and bool(evidence) and all(e.get("all_units_match") for e in evidence),
            "any_match": any(e.get("all_units_match") for e in evidence),
        }

        def dimension_check(tokens: List[str]) -> Dict[str, Any]:
            if not tokens:
                return {"required": False, "present": True, "missing": []}
            missing: List[str] = []
            if evidence:
                combined = " ".join(e.get("excerpt", "") for e in evidence)
                # Years and other numeric time markers are stripped by _words(),
                # so include them explicitly or a valid "In 2024" source would be
                # reported as a missing time period.
                combined_words = set(_words(combined))
                combined_words.update(re.findall(r"\b(?:19|20)\d{2}\b", combined))
                missing = [token for token in tokens if token not in combined_words]
            else:
                missing = list(tokens)
            return {"required": True, "present": not missing, "missing": missing}

        population_check = dimension_check(population)
        geography_check = dimension_check(geography)
        time_check = dimension_check(time_period)
        best_coverage = max((e.get("coverage") or 0.0 for e in evidence), default=0.0)
        meaning_check = {
            "coverage": round(best_coverage, 3),
            "threshold": MEANING_MIN_COVERAGE,
            "present": best_coverage >= MEANING_MIN_COVERAGE,
        }

        provenance = provenance_check(claim_text, evidence, sources)

        status, confidence, severity, human, note = cls._decide(
            claim_type=claim_type,
            claim_text=claim_text,
            cited_ids=cited_ids,
            evidence=evidence,
            numbers=numbers,
            number_check=number_check,
            unit_check=unit_check,
            population_check=population_check,
            geography_check=geography_check,
            time_check=time_check,
            meaning_check=meaning_check,
            provenance=provenance,
        )

        return {
            "claim_text": claim_text.strip(),
            "claim_type": claim_type,
            "citation_ids": [int(i) for i in cited_ids],
            "source_ids": [int(i) for i in cited_ids if int(i) in sources],
            "excerpts": evidence,
            "verification_status": status,
            "confidence": round(confidence, 2),
            "human_review_status": human,
            "severity": severity,
            "review_note": note,
            "number_check": number_check,
            "unit_check": unit_check,
            "population_check": population_check,
            "geography_check": geography_check,
            "time_check": time_check,
            "meaning_check": meaning_check,
            "provenance_check": provenance,
        }

    @classmethod
    def _decide(
        cls,
        claim_type: str,
        claim_text: str,
        cited_ids: List[int],
        evidence: List[Dict[str, Any]],
        numbers: List[Dict[str, Any]],
        number_check: Dict[str, Any],
        unit_check: Dict[str, Any],
        population_check: Dict[str, Any],
        geography_check: Dict[str, Any],
        time_check: Dict[str, Any],
        meaning_check: Dict[str, Any],
        provenance: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, float, str, str, str]:
        """Strict evidence-pipeline verdict (P2.3.4).

        Lexical overlap is never treated as factual entailment.  The strongest
        deterministic outcome is ``EXCERPT_MATCHED`` — the excerpt reproduces
        the claim's key content (numbers, units, context) — which still stores
        ``human_review_status = REQUIRED`` so only a recorded human decision
        can promote it to ``HUMAN_VERIFIED``.
        """
        provenance = provenance or {}

        # Non-factual claim kinds: no external citation is required, but an
        # aspirational number must never be presented as a promise.
        if claim_type in {"RECOMMENDATION", "HYPOTHESIS", "ILLUSTRATIVE_EXAMPLE", "USER_ASPIRATION"}:
            if claim_type == "USER_ASPIRATION":
                if numbers and _GUARANTEE.search(claim_text):
                    return (
                        "HUMAN_REVIEW",
                        0.25,
                        "HIGH",
                        "REQUIRED",
                        "Aspirational target is phrased as a guaranteed outcome. Relabel it as a goal the reader may choose to pursue; never promise the result.",
                    )
                return (
                    "QUALIFIED",
                    0.7,
                    "LOW",
                    "NOT_REQUIRED",
                    "Aspirational/illustrative statement, not a factual claim. Keep it visibly qualified.",
                )
            return (
                "NOT_APPLICABLE",
                0.8,
                "LOW",
                "NOT_REQUIRED",
                f"{CLAIM_TYPES[claim_type]} — not an externally verifiable fact.",
            )

        # Factual claims require at least one citation.
        if not cited_ids or not evidence:
            if numbers:
                return (
                    "UNSUPPORTED",
                    0.0,
                    "CRITICAL",
                    "REQUIRED",
                    "Numerical claim has no usable citation. Remove the number, qualify the statement qualitatively, or send the figure to human verification.",
                )
            return (
                "UNSUPPORTED",
                0.0,
                "HIGH",
                "REQUIRED",
                "Factual claim has no usable citation. Add a source that supports it or rewrite it as general explanatory material.",
            )

        def _provenance_note(parts: List[str]) -> Optional[str]:
            if not parts:
                return None
            return "Provenance risk: " + "; ".join(parts) + ". A human must check the cited source directly."

        provenance_parts: List[str] = []
        if provenance.get("missing_institutions"):
            provenance_parts.append(
                "claim names " + ", ".join(provenance["missing_institutions"])
                + " but the cited source never mentions them"
            )
        if provenance.get("submission_entity") and not provenance.get("submission_supported"):
            provenance_parts.append(
                f"claim says material was submitted/presented to "
                f"'{provenance['submission_entity']}' but the source does not document that submission"
            )
        if provenance.get("causal_risk"):
            provenance_parts.append(
                "claim states causation while the cited passage only reports association/perception"
            )
        provenance_blocker = _provenance_note(provenance_parts)

        reliability = sum(e.get("reliability_score", 0.0) for e in evidence) / len(evidence)
        coverage = float(meaning_check.get("coverage") or 0.0)

        # A provenance failure (false attribution, unsupported submission,
        # causal wording on correlational evidence) always escalates to human
        # review, whatever the overlap level is.
        if provenance_blocker:
            if not numbers or number_check["all_present"]:
                return (
                    "HUMAN_REVIEW_REQUIRED",
                    0.45,
                    "HIGH",
                    "REQUIRED",
                    "Support level: "
                    + ("excerpt matched" if coverage >= EXCERPT_MATCH_MIN_COVERAGE else
                       "partial" if coverage >= PARTIAL_MATCH_MIN_COVERAGE else "source found only")
                    + f" (coverage {coverage:.2f}). " + provenance_blocker,
                )

        if not numbers:
            # Non-numeric factual claim: overlap only grades evidence reach.
            if coverage >= EXCERPT_MATCH_MIN_COVERAGE:
                if provenance_blocker:
                    return (
                        "HUMAN_REVIEW_REQUIRED",
                        0.5,
                        "MEDIUM",
                        "REQUIRED",
                        "Excerpt shares the claim's meaning, but " + provenance_blocker,
                    )
                return (
                    "EXCERPT_MATCHED",
                    min(0.9, 0.55 + 0.25 * reliability + coverage * 0.1),
                    "LOW",
                    "REQUIRED",
                    "Cited excerpt reproduces the claim's meaning (coverage "
                    f"{coverage:.2f}). Pending human confirmation; not yet HUMAN_VERIFIED.",
                )
            if coverage >= PARTIAL_MATCH_MIN_COVERAGE:
                note = (
                    "Cited excerpt only partially matches the claim (coverage "
                    f"{coverage:.2f}). Verify manually, narrow the claim, or cite a more specific passage."
                )
                if provenance_blocker:
                    note = provenance_blocker + " " + note
                return ("PARTIALLY_SUPPORTED", 0.45, "MEDIUM", "REQUIRED", note)
            note = (
                "Citation exists but the excerpt does not support the claim "
                f"(coverage {coverage:.2f}). Rewrite, qualify, or cite a directly relevant passage."
            )
            if provenance_blocker:
                note = provenance_blocker + " " + note
            return ("SOURCE_FOUND", 0.3, "MEDIUM", "REQUIRED", note)

        # Numerical claim.
        if not number_check["any_present"]:
            note = "The cited source does not contain the stated number. Remove or correct the figure; do not substitute another invented number."
            if provenance_blocker:
                note = provenance_blocker + " " + note
            return (
                "UNSUPPORTED",
                0.0,
                "CRITICAL",
                "REQUIRED",
                note,
            )
        if not number_check["all_present"]:
            note = "Only some of the stated numbers appear in the cited source. Verify every figure and remove the ones that are unsupported."
            if provenance_blocker:
                note = provenance_blocker + " " + note
            return (
                "PARTIALLY_SUPPORTED",
                0.2,
                "HIGH",
                "REQUIRED",
                note,
            )
        if not unit_check["all_match"]:
            note = "The number appears with a different unit in the source (for example a count versus a percentage). Correct the unit or remove the figure."
            if provenance_blocker:
                note = provenance_blocker + " " + note
            return (
                "PARTIALLY_SUPPORTED",
                0.3,
                "HIGH",
                "REQUIRED",
                note,
            )

        missing_dims: List[str] = []
        if not population_check["present"]:
            missing_dims.append("population")
        if not geography_check["present"]:
            missing_dims.append("geography")
        if not time_check["present"]:
            missing_dims.append("time period")
        if coverage < PARTIAL_MATCH_MIN_COVERAGE:
            missing_dims.append("meaning/context")

        if missing_dims:
            note = (
                "The number appears in the source, but the source does not verify: "
                + ", ".join(missing_dims)
                + ". Verify manually or remove the unsupported context."
            )
            if provenance_blocker:
                note = provenance_blocker + " " + note
            return (
                "PARTIALLY_SUPPORTED",
                0.35,
                "HIGH",
                "REQUIRED",
                note,
            )

        if provenance_blocker:
            return (
                "HUMAN_REVIEW_REQUIRED",
                0.5,
                "MEDIUM",
                "REQUIRED",
                "Number, unit and context all appear in the cited excerpt, but " + provenance_blocker,
            )

        if reliability < 0.6:
            return (
                "HUMAN_REVIEW_REQUIRED",
                min(0.7, 0.35 + 0.25 * reliability),
                "MEDIUM",
                "REQUIRED",
                "The number is present and contextualised, but the source is not assessed as credible enough to certify publication.",
            )

        confidence = min(0.9, 0.45 + 0.25 * reliability + coverage * 0.2)
        return (
            "EXCERPT_MATCHED",
            confidence,
            "LOW",
            "REQUIRED",
            "Cited excerpt contains the number, unit, population, geography, time period and meaning "
            f"(coverage {coverage:.2f}). Pending human confirmation; a recorded human decision is required "
            "before this claim may be presented as verified.",
        )

    # ------------------------------------------------------------------
    # Chapter extraction and persistence
    # ------------------------------------------------------------------
    @classmethod
    def analyze_chapter(
        cls,
        project_id: int,
        chapter_id: int,
        content: str,
        sources: Iterable[Dict[str, Any]],
        section_map: Optional[Dict[str, str]] = None,
        section_ids: Optional[Dict[str, int]] = None,
        persist: bool = True,
    ) -> Dict[str, Any]:
        source_map = {int(s["id"]): s for s in sources if s and s.get("id") is not None}
        claims: List[Dict[str, Any]] = []
        for text, section_key in _sentences_with_sections(content, section_map):
            cited = _extract_citations(text)
            # A citation in the same sentence is the primary evidence link.  If
            # there is none, use the nearest cited sentence in the same paragraph
            # only when that paragraph is short and explicitly linked; otherwise
            # the claim remains uncited on purpose.
            numbers = _numbers(text)
            claim_type = _classify(text, numbers, bool(cited))
            if not _is_candidate(text, numbers, bool(cited), claim_type):
                continue
            record = cls.analyze_claim(text, claim_type, cited, source_map)
            record["claim_key"] = _claim_key(text)
            record["section_key"] = section_key
            record["section_id"] = (section_ids or {}).get(section_key)
            claims.append(record)

        summary = cls.summarize(claims)
        if persist:
            cls._save_claims(project_id, chapter_id, claims)
            summary = cls.chapter_summary(chapter_id)
        return {"claims": claims, "summary": summary}

    @classmethod
    def summarize(cls, claims: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_type: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        for claim in claims:
            by_type[claim["claim_type"]] = by_type.get(claim["claim_type"], 0) + 1
            by_status[claim["verification_status"]] = by_status.get(claim["verification_status"], 0) + 1
        unsupported_numeric = [
            c for c in claims
            if c["claim_type"] == "NUMERICAL_STATISTIC" and c["verification_status"] == "UNSUPPORTED"
        ]
        critical = [c for c in claims if str(c.get("severity")).upper() == "CRITICAL"]
        human = [c for c in claims if c.get("human_review_status") in {"REQUIRED", "PENDING"}]
        required = [c for c in claims if c["claim_type"] in EVIDENCE_REQUIRED_TYPES]
        human_verified = [
            c for c in required
            if c["verification_status"] in {"HUMAN_VERIFIED"}
        ]
        excerpt_matched = [
            c for c in required
            if c["verification_status"] in {"EXCERPT_MATCHED"}
        ]

        def _count(statuses: set[str]) -> int:
            return sum(1 for c in claims if c["verification_status"] in statuses)

        return {
            "total_claims": len(claims),
            "by_type": by_type,
            "by_status": by_status,
            "evidence_required_claims": len(required),
            # Only a recorded human decision produces a "verified" claim.
            "verified_claims": len(human_verified),
            "excerpt_matched_count": len(excerpt_matched),
            "partially_supported_count": _count({"PARTIALLY_SUPPORTED"}),
            "source_found_count": _count({"SOURCE_FOUND"}),
            "human_review_required_count": _count({"HUMAN_REVIEW_REQUIRED"}),
            "citation_coverage": round(len(human_verified) / len(required), 3) if required else 1.0,
            "unsupported_count": sum(1 for c in claims if c["verification_status"] == "UNSUPPORTED"),
            "unsupported_numeric_count": len(unsupported_numeric),
            "partially_verified_count": _count({"PARTIALLY_VERIFIED", "PARTIALLY_SUPPORTED"}),
            "human_review_count": len(human),
            "critical_count": len(critical),
            "critical_unsupported_numeric": [c["claim_text"] for c in unsupported_numeric],
            "human_review_claims": [c["claim_text"] for c in human],
            "gate_ok": not unsupported_numeric and not critical,
        }

    @classmethod
    def _save_claims(cls, project_id: int, chapter_id: int, claims: List[Dict[str, Any]]) -> None:
        with transaction() as conn:
            existing = {
                row["claim_key"]: row
                for row in conn.execute(
                    "SELECT claim_key, human_review_status, review_note FROM chapter_claims "
                    "WHERE chapter_id = ?",
                    (chapter_id,),
                ).fetchall()
            }
            keys = [c["claim_key"] for c in claims]
            if keys:
                placeholders = ",".join("?" for _ in keys)
                conn.execute(
                    f"DELETE FROM chapter_claims WHERE chapter_id = ? AND claim_key NOT IN ({placeholders})",
                    (chapter_id, *keys),
                )
            else:
                conn.execute("DELETE FROM chapter_claims WHERE chapter_id = ?", (chapter_id,))

            for claim in claims:
                previous = existing.get(claim["claim_key"])
                human_status = claim.get("human_review_status") or "NOT_REQUIRED"
                review_note = claim.get("review_note") or ""
                if previous and previous["human_review_status"] in {"APPROVED", "REJECTED", "PENDING"}:
                    human_status = previous["human_review_status"]
                    review_note = previous.get("review_note") or review_note
                conn.execute(
                    """
                    INSERT INTO chapter_claims (
                        project_id, chapter_id, section_id, section_key, claim_key,
                        claim_text, claim_type, citation_ids_json, source_ids_json,
                        excerpts_json, verification_status, confidence,
                        human_review_status, review_note, severity,
                        number_check_json, unit_check_json, population_check_json,
                        geography_check_json, time_check_json, meaning_check_json,
                        provenance_check_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chapter_id, claim_key) DO UPDATE SET
                        section_id = excluded.section_id,
                        section_key = excluded.section_key,
                        claim_text = excluded.claim_text,
                        claim_type = excluded.claim_type,
                        citation_ids_json = excluded.citation_ids_json,
                        source_ids_json = excluded.source_ids_json,
                        excerpts_json = excluded.excerpts_json,
                        verification_status = excluded.verification_status,
                        confidence = excluded.confidence,
                        human_review_status = excluded.human_review_status,
                        review_note = excluded.review_note,
                        severity = excluded.severity,
                        number_check_json = excluded.number_check_json,
                        unit_check_json = excluded.unit_check_json,
                        population_check_json = excluded.population_check_json,
                        geography_check_json = excluded.geography_check_json,
                        time_check_json = excluded.time_check_json,
                        meaning_check_json = excluded.meaning_check_json,
                        provenance_check_json = excluded.provenance_check_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        project_id,
                        chapter_id,
                        claim.get("section_id"),
                        claim.get("section_key"),
                        claim["claim_key"],
                        claim["claim_text"],
                        claim["claim_type"],
                        json.dumps(claim.get("citation_ids") or []),
                        json.dumps(claim.get("source_ids") or []),
                        json.dumps(claim.get("excerpts") or []),
                        claim["verification_status"],
                        float(claim.get("confidence") or 0.0),
                        human_status,
                        review_note,
                        claim.get("severity") or "MEDIUM",
                        json.dumps(claim.get("number_check") or {}),
                        json.dumps(claim.get("unit_check") or {}),
                        json.dumps(claim.get("population_check") or {}),
                        json.dumps(claim.get("geography_check") or {}),
                        json.dumps(claim.get("time_check") or {}),
                        json.dumps(claim.get("meaning_check") or {}),
                        json.dumps(claim.get("provenance_check") or {}),
                    ),
                )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_claim(row: Any) -> Dict[str, Any]:
        data = dict(row)
        for key in (
            "citation_ids_json", "source_ids_json", "excerpts_json",
            "number_check_json", "unit_check_json", "population_check_json",
            "geography_check_json", "time_check_json", "meaning_check_json",
            "provenance_check_json",
        ):
            target = key.replace("_json", "")
            try:
                data[target] = json.loads(data.get(key) or "[]" if target in {"citation_ids", "source_ids", "excerpts"} else data.get(key) or "{}")
            except (TypeError, ValueError):
                data[target] = [] if target in {"citation_ids", "source_ids", "excerpts"} else {}
        return data

    @classmethod
    def list_claims(cls, chapter_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chapter_claims WHERE chapter_id = ? ORDER BY id",
                (chapter_id,),
            ).fetchall()
            return [cls._row_to_claim(row) for row in rows]

    @classmethod
    def chapter_summary(cls, chapter_id: int) -> Dict[str, Any]:
        claims = cls.list_claims(chapter_id)
        summary = cls.summarize(claims)
        summary["claims"] = claims
        return summary

    @classmethod
    def project_summary(cls, project_id: int) -> Dict[str, Any]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chapter_claims WHERE project_id = ? ORDER BY chapter_id, id",
                (project_id,),
            ).fetchall()
        claims = [cls._row_to_claim(row) for row in rows]
        summary = cls.summarize(claims)
        summary["claims"] = claims
        return summary

    @staticmethod
    def set_human_review(claim_id: int, status: str, note: str = "") -> bool:
        status = (status or "").upper()
        if status not in HUMAN_REVIEW_STATUSES:
            return False
        with transaction() as conn:
            existing = conn.execute(
                "SELECT project_id, verification_status FROM chapter_claims WHERE id = ?",
                (claim_id,),
            ).fetchone()
            if not existing:
                return False
            previous_status = str(existing["verification_status"] or "")
            verification_status = previous_status
            severity_sql = ""
            if status == "APPROVED":
                verification_status = "HUMAN_VERIFIED"
                severity_sql = ", severity = 'LOW'"
            elif status == "REJECTED":
                verification_status = "REJECTED"
                severity_sql = ", severity = 'CRITICAL'"
            cur = conn.execute(
                "UPDATE chapter_claims SET human_review_status = ?, verification_status = ?, "
                "review_note = ?, updated_at = CURRENT_TIMESTAMP" + severity_sql + " WHERE id = ?",
                (status, verification_status, note[:2000], claim_id),
            )
            if cur.rowcount:
                conn.execute(
                    """
                    INSERT INTO claim_verification_events (
                        claim_id, project_id, previous_status, verification_status,
                        human_review_status, explanation
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim_id, int(existing["project_id"]), previous_status,
                        verification_status, status, note[:2000],
                    ),
                )
            return cur.rowcount > 0

    @staticmethod
    def verification_events(claim_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM claim_verification_events WHERE claim_id = ? ORDER BY id DESC",
                (claim_id,),
            ).fetchall()
        return [dict(row) for row in rows]
