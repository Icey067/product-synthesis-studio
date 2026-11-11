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

        # 3-4. Claim/source relevance and support; unsupported statistics
        for sentence in split_sentences(content):
            cited = CitationService.extract_citations(sentence)
            stats = _numbers(sentence)
            if stats and not cited:
                issues.append(_issue(
                    "unsupported_statistic", "HIGH",
                    f"Statistic without a citation: {', '.join(sorted(stats))}",
                    "Attach a [Source-ID] whose text contains this exact number, or remove the statistic.",
                    claim=sentence,
                ))
                continue

            for cid in cited:
                source = source_map.get(cid)
                if not source:
                    continue  # already reported as critical above
                source_text = source.get("raw_content") or ""
                source_tokens = tokenize(source_text)
                claim_tokens = tokenize(sentence)
                overlap = claim_tokens & source_tokens

                if stats:
                    missing_numbers = {n for n in stats if n not in normalize_text(source_text).replace(" ", "")}
                    if missing_numbers:
                        issues.append(_issue(
                            "statistic_not_in_source", "HIGH",
                            f"[Source-{cid}] does not contain the stated number(s): {', '.join(sorted(missing_numbers))}.",
                            "Correct the number or cite a source that actually contains it.",
                            claim=sentence, source_id=cid,
                        ))
                    elif len(overlap) < 2:
                        issues.append(_issue(
                            "weak_claim_support", "MEDIUM",
                            f"[Source-{cid}] contains the number but shares little context with the claim.",
                            "Cite the specific passage or narrow the claim.",
                            claim=sentence, source_id=cid,
                        ))
                else:
                    if len(overlap) < 2:
                        issues.append(_issue(
                            "claim_source_relevance", "MEDIUM",
                            f"[Source-{cid}] has little lexical overlap with this claim.",
                            "Verify manually or cite a more directly relevant passage.",
                            claim=sentence, source_id=cid,
                        ))

        # 6. Required format
        format_ok, format_issues = validate_chapter_format(product_format, content)
        for fi in format_issues:
            issues.append(_issue(
                fi["code"], fi["severity"], fi["message"], fi["correction"]
            ))

        # 10. Repetition
        sentences = [s for s in split_sentences(content) if len(s.split()) >= 8]
        seen: Dict[str, int] = {}
        for s in sentences:
            key = normalize_text(s)
            seen[key] = seen.get(key, 0) + 1
        repeated = [s for s, count in seen.items() if count > 1]
        if repeated:
            issues.append(_issue(
                "repetition", "MEDIUM",
                f"{len(repeated)} sentence(s) are repeated verbatim.",
                "Remove duplicate passages and replace them with new substance.",
            ))
        elif len(sentences) >= 10:
            words = normalize_text(content).split()
            shingles = [" ".join(words[i:i + 10]) for i in range(max(0, len(words) - 9))]
            if shingles:
                unique_ratio = len(set(shingles)) / len(shingles)
                if unique_ratio < 0.75:
                    issues.append(_issue(
                        "repetition", "MEDIUM",
                        f"Low unique-phrase ratio ({unique_ratio:.0%}); the chapter repeats itself.",
                        "Rewrite repeated paragraphs with distinct examples and actions.",
                    ))

        counts = {level: sum(1 for i in issues if i["severity"] == level) for level in SEVERITY_WEIGHT}
        score = max(0, 100 - sum(SEVERITY_WEIGHT[i["severity"]] for i in issues))

        return {
            "structure": structure,
            "citation_audit": citation_audit,
            "issues": issues,
            "counts": counts,
            "score": score,
            "checks": {
                "citations_exist": not citation_audit["invalid_citations"],
                "citations_present": bool(citation_audit["valid_citations"]),
                "required_sections": not structure["missing_sections"],
                "word_target": structure["word_count"] >= structure["min_words"],
                "format_compliant": format_ok,
                "statistics_supported": not any(i["check"] == "unsupported_statistic" for i in issues),
                "no_repetition": not any(i["check"] == "repetition" for i in issues),
            },
        }

    @classmethod
    def final_status(cls, deterministic: Dict[str, Any], model_requires_human: bool = False) -> tuple[str, int, bool]:
        issues = deterministic["issues"]
        counts = deterministic["counts"]
        human_review = bool(counts.get("CRITICAL")) or model_requires_human
        if counts.get("CRITICAL"):
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
