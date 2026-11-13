"""Independent, deterministic verification used by the quality auditor.

The language model may comment on usefulness and readability, but it can never
certify factual accuracy. Citation existence, source relevance, claim support,
statistics, structure, length and format are checked in code.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from services.chapter_contract import analyze_chapter
from services.citation_service import CitationService
from services.evidence_service import normalize_text
from services.research_service import tokenize
from services.product_format_service import validate_chapter as validate_chapter_format
from services.claim_service import ClaimService
from services.repetition_service import RepetitionService
from services.book_architecture_service import BookArchitectureService

STAT_PATTERN = re.compile(
    r"(?:₹|rs\.?|\$|us\$)\s?\d[\d,.]*"
    r"|\d[\d,.]*\s?%"
    r"|\b\d[\d,.]*\s?(?:billion|million|crore|lakh|trillion)\b"
    r"|\b\d{2,}[,.]\d+\b",
    re.IGNORECASE,
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?!\[)|\n+")
SEVERITY_WEIGHT = {"CRITICAL": 40, "HIGH": 15, "MEDIUM": 6, "LOW": 2}


def split_sentences(text: str) -> List[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text or "") if len(s.strip()) > 25]


def _numbers(text: str) -> set[str]:
    return {normalize_text(m.group(0)).replace(" ", "") for m in STAT_PATTERN.finditer(text or "")}


def _issue(check: str, severity: str, message: str, correction: str = "",
           claim: str = "", source_id: Optional[int] = None) -> Dict[str, Any]:
    return {
        "check": check,
        "severity": severity,
        "message": message,
        "correction": correction,
        "claim": claim,
        "source_id": source_id,
    }


class VerificationService:
    @classmethod
    def deterministic_checks(
        cls,
        project_id: int,
        chapter: Dict[str, Any],
        charter: Dict[str, Any],
        sources: List[Dict[str, Any]],
        product_format: str = "Ebook",
    ) -> Dict[str, Any]:
        content = chapter.get("content", "") or ""
        source_map = {int(s["id"]): s for s in sources}
        source_ids = list(source_map.keys())

        from services.project_service import ProjectService

        settings = ProjectService.get_chapter_settings(project_id)
        structure = analyze_chapter(
            content,
            target=chapter.get("target_word_count") or settings["target_words"],
            min_words=settings["min_words"],
            source_ids=source_ids,
            product_format=product_format,
        )
        citation_audit = CitationService.verify_chapter_citations(project_id, content)

        issues: List[Dict[str, Any]] = []

        # 1-2. Citation existence
        for invalid in citation_audit["invalid_citations"]:
            issues.append(_issue(
                "citation_exists", "CRITICAL",
                f"[Source-{invalid}] does not exist in this project's research library.",
                f"Remove [Source-{invalid}] or replace it with a real source.",
                source_id=invalid,
            ))

        # Required structure and length
        if structure["word_count"] < structure["min_words"]:
            issues.append(_issue(
                "word_count", "HIGH",
                f"Chapter has {structure['word_count']} words; minimum is {structure['min_words']}.",
                "Expand the deficient sections with specific, non-repetitive material.",
            ))
        for missing in structure["missing_sections"]:
            issues.append(_issue(
                "required_section", "HIGH",
                f"Required section missing: {missing.replace('_', ' ')}.",
                "Add the missing section with concrete reader-facing content.",
            ))
        if structure["citation_required"] and not structure["citation_ids"]:
            issues.append(_issue(
                "citation_required", "HIGH",
                "No [Source-ID] citations exist although research sources are available.",
                "Ground the major claims in at least two verified sources.",
            ))

        # 3-4. Structured claim verification (P2.2).  Every important claim is
        # extracted and checked for number, unit, population, geography, time
        # period and meaning.  The model never participates in this decision.
        claim_result = {"claims": [], "summary": ClaimService.summarize([])}
        try:
            if content.strip():
                claim_result = ClaimService.analyze_chapter(
                    project_id,
                    int(chapter.get("id") or 0),
                    content,
                    sources,
                    persist=bool(chapter.get("id")),
                )
        except Exception as exc:  # never let a claim-engine bug hide an audit
            claim_result["error"] = str(exc)
        claims = claim_result.get("claims") or []
        claim_summary = claim_result.get("summary") or ClaimService.summarize([])

        for claim in claims:
            status = claim.get("verification_status")
            number_values = [
                token.get("raw")
                for token in (claim.get("number_check", {}).get("numbers") or [])
                if token.get("raw")
            ]
            if status == "UNSUPPORTED":
                cited_valid = any(
                    int(cid) in source_map for cid in (claim.get("citation_ids") or [])
                )
                if number_values and claim.get("citation_ids") and cited_valid:
                    issues.append(_issue(
                        "statistic_not_in_source", "CRITICAL",
                        "Cited source does not contain the stated number(s) "
                        f"{', '.join(number_values)}: {claim['claim_text'][:240]}",
                        claim.get("review_note") or "Remove or correct the figure; do not invent a replacement.",
                        claim=claim["claim_text"], source_id=(claim.get("citation_ids") or [None])[0],
                    ))
                elif number_values:
                    issues.append(_issue(
                        "unsupported_statistic", "CRITICAL",
                        f"Unsupported numerical claim ({', '.join(number_values)}): "
                        f"{claim['claim_text'][:240]}",
                        claim.get("review_note") or "Remove the number, qualify the statement, or send it to human verification.",
                        claim=claim["claim_text"],
                    ))
                else:
                    issues.append(_issue(
                        "unsupported_claim", "HIGH",
                        f"Factual claim has no supporting source: {claim['claim_text'][:240]}",
                        claim.get("review_note") or "Add a supporting citation or rewrite as general explanatory material.",
                        claim=claim["claim_text"],
                    ))
            elif status == "PARTIALLY_VERIFIED":
                details: List[str] = []
                checks = {
                    "population": claim.get("population_check", {}),
                    "geography": claim.get("geography_check", {}),
                    "time period": claim.get("time_check", {}),
                    "meaning/context": claim.get("meaning_check", {}),
                }
                for label, check in checks.items():
                    if check.get("required", True) and not check.get("present", False):
                        details.append(label)
                if not claim.get("unit_check", {}).get("all_match", False):
                    details.append("unit")
                if number_values:
                    issues.append(_issue(
                        "statistic_context_mismatch", "HIGH",
                        "The number appears in the source but the claim is not fully verified"
                        + (f" ({', '.join(details)})" if details else "")
                        + f": {claim['claim_text'][:220]}",
                        claim.get("review_note") or "Verify the missing context manually or narrow the claim.",
                        claim=claim["claim_text"],
                    ))
                else:
                    issues.append(_issue(
                        "claim_source_relevance", "MEDIUM",
                        f"Cited excerpt does not clearly support the claim: {claim['claim_text'][:220]}",
                        claim.get("review_note") or "Cite a more directly relevant passage or verify manually.",
                        claim=claim["claim_text"],
                    ))
            elif status == "HUMAN_REVIEW":
                issues.append(_issue(
                    "claim_human_review", "MEDIUM",
                    f"Claim requires human verification: {claim['claim_text'][:240]}",
                    claim.get("review_note") or "A human must verify the figure and its context before publication.",
                    claim=claim["claim_text"],
                ))

        # 5. Cross-chapter repetition (P2.2).  Deterministic, fingerprint and
        # token-similarity based.  Headings/definitions/disclaimers excluded.
        repetition_result: Dict[str, Any] = {
            "findings": [], "gate_ok": True, "finding_count": 0,
            "max_per_chapter": 0, "by_match_type": {},
        }
        try:
            if content.strip() and project_id:
                RepetitionService.scan_project(project_id, persist=True)
                repetition_result = RepetitionService.chapter_summary(int(chapter.get("id") or 0))
        except Exception as exc:
            repetition_result["error"] = str(exc)
        for finding in (repetition_result.get("findings") or [])[:10]:
            issues.append(_issue(
                "repetition",
                finding.get("severity", "MEDIUM"),
                f"{finding.get('match_label') or finding.get('match_type')} in chapter "
                f"{finding.get('duplicate_chapter_number')} (similarity "
                f"{finding.get('similarity')}): {str(finding.get('duplicate_text'))[:200]}",
                finding.get("recommendation") or "Rewrite the duplicate with chapter-specific substance.",
                claim=str(finding.get("duplicate_text"))[:300],
            ))
        if not repetition_result.get("gate_ok", True):
            issues.append(_issue(
                "repetition_threshold", "HIGH",
                f"Chapter has {repetition_result.get('finding_count')} repetition findings; "
                f"configured maximum is {repetition_result.get('max_per_chapter')}.",
                "Rewrite the duplicate passages; do not simply delete useful information.",
            ))

        # 6. Chapter purpose uniqueness (P2.2 architecture).
        architecture_result: Dict[str, Any] = {"ok": True, "overlap_count": 0, "overlaps": []}
        try:
            if project_id:
                architecture_result = BookArchitectureService.gate(project_id)
        except Exception as exc:
            architecture_result["error"] = str(exc)
        chapter_number = int(chapter.get("chapter_number") or 0)
        chapter_overlaps = [
            overlap for overlap in (architecture_result.get("overlaps") or [])
            if chapter_number in {overlap.get("chapter"), overlap.get("overlaps_with")}
        ]
        for overlap in chapter_overlaps:
            issues.append(_issue(
                "chapter_purpose_overlap", "HIGH",
                f"Chapter {overlap.get('chapter')} and chapter {overlap.get('overlaps_with')} "
                f"have substantially overlapping purposes (similarity {overlap.get('similarity')}).",
                "Give each chapter a distinct question, learning outcome and contribution.",
            ))

        # 7. Required format
        format_ok, format_issues = validate_chapter_format(product_format, content)
        for fi in format_issues:
            issues.append(_issue(
                fi["code"], fi["severity"], fi["message"], fi["correction"]
            ))

        counts = {level: sum(1 for i in issues if i["severity"] == level) for level in SEVERITY_WEIGHT}
        score = max(0, 100 - sum(SEVERITY_WEIGHT[i["severity"]] for i in issues))

        statistics_supported = not any(
            i["check"] in {"unsupported_statistic", "statistic_not_in_source", "statistic_context_mismatch"}
            for i in issues
        )
        claims_supported = not any(
            i["check"] in {"unsupported_claim", "claim_human_review", "statistic_context_mismatch"}
            for i in issues
        )
        repetition_ok = bool(repetition_result.get("gate_ok", True)) and not any(
            i["check"] == "repetition" for i in issues
        )
        unique_purpose = not chapter_overlaps

        return {
            "structure": structure,
            "citation_audit": citation_audit,
            "issues": issues,
            "counts": counts,
            "score": score,
            "claims": claims,
            "claim_summary": {k: v for k, v in claim_summary.items() if k != "claims"},
            "repetition": {
                k: v for k, v in repetition_result.items() if k != "findings"
            },
            "repetition_findings": repetition_result.get("findings") or [],
            "architecture": architecture_result,
            "architecture_overlaps": chapter_overlaps,
            "checks": {
                "citations_exist": not citation_audit["invalid_citations"],
                "citations_present": bool(citation_audit["valid_citations"]),
                "required_sections": not structure["missing_sections"],
                "word_target": structure["word_count"] >= structure["min_words"],
                "format_compliant": format_ok,
                "statistics_supported": statistics_supported,
                "claims_supported": claims_supported,
                "no_critical_unsupported_numeric": int(claim_summary.get("unsupported_numeric_count") or 0) == 0,
                "repetition_ok": repetition_ok,
                "unique_purpose": unique_purpose,
                "no_repetition": repetition_ok,
            },
        }

    @classmethod
    def final_status(cls, deterministic: Dict[str, Any], model_requires_human: bool = False) -> tuple[str, int, bool]:
        issues = deterministic["issues"]
        counts = deterministic["counts"]
        claim_review_checks = {
            "unsupported_claim", "claim_human_review", "statistic_context_mismatch",
            "claim_source_relevance", "chapter_purpose_overlap", "repetition_threshold",
        }
        claim_review = any(
            i.get("check") in claim_review_checks and str(i.get("severity")).upper() in {"CRITICAL", "HIGH", "MEDIUM"}
            for i in issues
        )
        human_review = bool(counts.get("CRITICAL")) or model_requires_human or claim_review
        if counts.get("CRITICAL"):
            status = "REQUIRES_HUMAN_VERIFICATION"
        elif claim_review:
            status = "REQUIRES_HUMAN_VERIFICATION"
        elif counts.get("HIGH") or counts.get("MEDIUM"):
            status = "NEEDS_IMPROVEMENT"
        elif model_requires_human:
            status = "REQUIRES_HUMAN_VERIFICATION"
        else:
            status = "PASS"

        score = deterministic["score"]
        # A model can never raise the deterministic score; a human-review flag
        # also caps it below a clean pass.
        if human_review:
            score = min(score, 60)
        elif status == "NEEDS_IMPROVEMENT":
            score = min(score, 75)
        return status, max(0, min(100, score)), human_review
