"""Deterministic cross-chapter repetition engine (P2.2).

The engine compares real chapter text, not just instructions.  It detects:

* exact repeated sentences;
* normalised repeated sentences;
* near-identical paragraphs;
* repeated explanations;
* reused examples;
* reused frameworks;
* repeated introductions and conclusions.

Sentence fingerprints are used for exact matches and token-set Jaccard
similarity for near matches.  No embedding model is required, so this works on
any local installation.  Headings, approved definition/glossary sections,
copyright, disclaimers and navigation boilerplate are excluded.

The report identifies the original and duplicate text, both chapters, the
similarity and a correction.  The writer is expected to rewrite the duplicate
with new substance rather than delete useful information.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from database.db import get_connection, transaction
from services.evidence_service import normalize_text
from services.research_service import tokenize

DEFAULT_SIMILARITY_THRESHOLD = 0.82
DEFAULT_MAX_FINDINGS_PER_CHAPTER = 3
DEFAULT_MAX_PROJECT_FINDINGS = 15
MIN_SENTENCE_WORDS = 7
MIN_PARAGRAPH_WORDS = 30

MATCH_TYPES = {
    "exact_sentence": "Exact repeated sentence",
    "normalized_sentence": "Normalised repeated sentence",
    "near_sentence": "Near-identical sentence",
    "near_paragraph": "Near-identical paragraph",
    "duplicate_paragraph": "Duplicate paragraph",
    "repeated_explanation": "Repeated explanation",
    "reused_example": "Reused example",
    "reused_framework": "Reused framework",
    "repeated_introduction": "Repeated introduction",
    "repeated_conclusion": "Repeated conclusion",
}

EXCLUDED_SECTION_PATTERNS = re.compile(
    r"^(copyright|disclaimer|table of contents|contents|glossary|definitions|"
    r"references|bibliography|index|about the author|front matter|"
    r"legal notice|terms of use)$",
    re.IGNORECASE,
)
BOILERPLATE = re.compile(
    r"(all rights reserved|copyright\s*©|isbn|no part of this (?:book|publication)|"
    r"this (?:book|publication) is (?:for )?(?:educational|informational) purposes|"
    r"legal disclaimer|affiliate disclosure|table of contents|"
    r"click (?:here|the link)|subscribe to|follow us on)",
    re.IGNORECASE,
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?!\[)|\n+")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
CITATION_RE = re.compile(r"\[(?:Source|Ref)-\d+\]", re.IGNORECASE)
EXAMPLE_MARKERS = re.compile(
    r"\b(for example|for instance|imagine|suppose|consider a|consider an|"
    r"say you|let's say|scenario|case study|illustration)\b",
    re.IGNORECASE,
)
FRAMEWORK_MARKERS = re.compile(
    r"\b(framework|model|method|methodology|system|blueprint|playbook|"
    r"the \w+ (?:loop|cycle|matrix|canvas|rule|principle))\b",
    re.IGNORECASE,
)
INTRO_MARKERS = re.compile(r"^(introduction|overview|why this matters)", re.IGNORECASE)
CONCLUSION_MARKERS = re.compile(
    r"^(chapter summary|summary|conclusion|key takeaways|recap|next steps)",
    re.IGNORECASE,
)


def _fingerprint(text: str) -> str:
    return hashlib.sha1(normalize_text(text).encode("utf-8")).hexdigest()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _section_key(heading: str) -> str:
    key = normalize_text(heading).replace(" ", "_")
    return key or "body"


def _paragraphs(content: str) -> List[Tuple[str, str]]:
    """Return (paragraph, section_key) for substantive paragraphs."""
    result: List[Tuple[str, str]] = []
    section = "body"
    excluded = False
    for block in re.split(r"\n\s*\n", content or ""):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        heading = HEADING_RE.match(lines[0])
        if heading:
            title = heading.group(1).strip()
            section = _section_key(title)
            excluded = bool(EXCLUDED_SECTION_PATTERNS.match(title))
            lines = lines[1:]
        if excluded or not lines:
            continue
        text = " ".join(LIST_MARKER.sub("", line) for line in lines)
        if len(text.split()) < MIN_PARAGRAPH_WORDS or BOILERPLATE.search(text):
            continue
        result.append((text.strip(), section))
    return result


def _sentences(content: str) -> List[Dict[str, Any]]:
    """Return sentence records with section and position metadata."""
    records: List[Dict[str, Any]] = []
    section = "body"
    excluded = False
    position = 0
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            title = heading.group(1).strip()
            section = _section_key(title)
            excluded = bool(EXCLUDED_SECTION_PATTERNS.match(title))
            continue
        if excluded:
            continue
        line = LIST_MARKER.sub("", line)
        for sentence in SENTENCE_SPLIT.split(line):
            sentence = sentence.strip()
            if len(sentence.split()) < MIN_SENTENCE_WORDS:
                continue
            if BOILERPLATE.search(sentence):
                continue
            normalized = normalize_text(CITATION_RE.sub("", sentence))
            if len(normalized.split()) < MIN_SENTENCE_WORDS:
                continue
            records.append(
                {
                    "text": sentence,
                    "normalized": normalized,
                    "fingerprint": _fingerprint(normalized),
                    "tokens": tokenize(normalized),
                    "section": section,
                    "position": position,
                }
            )
            position += 1
    # Mark introduction/conclusion positions for the special match types.
    if records:
        first_section = records[0]["section"]
        last_section = records[-1]["section"]
        for record in records:
            record["intro"] = (
                record["section"] == first_section and record["position"] <= 2
            ) or bool(INTRO_MARKERS.match(record["section"].replace("_", " ")))
            record["conclusion"] = (
                record["section"] == last_section
                and record["position"] >= len(records) - 3
            ) or bool(CONCLUSION_MARKERS.match(record["section"].replace("_", " ")))
    return records


def _match_type(
    original: Dict[str, Any],
    duplicate: Dict[str, Any],
    similarity: float,
    kind: str,
) -> str:
    if kind == "paragraph":
        return "duplicate_paragraph" if similarity >= 0.95 else "near_paragraph"
    if similarity >= 0.999 and original["text"].strip() == duplicate["text"].strip():
        return "exact_sentence"
    if similarity >= 0.999:
        return "normalized_sentence"
    if original.get("intro") and duplicate.get("intro"):
        return "repeated_introduction"
    if original.get("conclusion") and duplicate.get("conclusion"):
        return "repeated_conclusion"
    if EXAMPLE_MARKERS.search(original["text"]) or EXAMPLE_MARKERS.search(duplicate["text"]):
        return "reused_example"
    if FRAMEWORK_MARKERS.search(original["text"]) or FRAMEWORK_MARKERS.search(duplicate["text"]):
        return "reused_framework"
    if len(original["text"].split()) >= 20 and len(duplicate["text"].split()) >= 20:
        return "repeated_explanation"
    return "near_sentence"


def _recommendation(match_type: str) -> str:
    return {
        "exact_sentence": "Rewrite this sentence in the later chapter with new substance, a different angle or a chapter-specific example. Do not simply delete it if the information is needed.",
        "normalized_sentence": "The sentence is identical after normalisation. Rewrite it for this chapter's unique purpose instead of restating it.",
        "near_sentence": "Rewrite the near-duplicate so the later chapter adds new information rather than paraphrasing the earlier passage.",
        "near_paragraph": "Replace the duplicated paragraph with chapter-specific detail, a new example or a different implementation step.",
        "duplicate_paragraph": "This paragraph repeats an earlier one. Rewrite it around this chapter's unique question and evidence.",
        "repeated_explanation": "The same explanation appears twice. Keep the fuller version once and replace this one with a forward-moving application.",
        "reused_example": "The example is reused. Create a new illustrative scenario grounded in this chapter's topic, or explicitly cross-reference the earlier example instead of retelling it.",
        "reused_framework": "The framework is repeated. Cross-reference the earlier chapter and apply the framework to a new decision instead of re-explaining it.",
        "repeated_introduction": "Each chapter needs a distinct opening. Replace this introduction with the specific problem and learning outcome of this chapter.",
        "repeated_conclusion": "Each chapter needs a distinct close. Replace this conclusion with this chapter's own takeaways and 24-hour action.",
    }.get(match_type, "Rewrite the duplicated material with chapter-specific substance.")


class RepetitionService:
    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @staticmethod
    def _setting(key: str, default: float) -> float:
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM application_settings WHERE key = ?", (key,)
                ).fetchone()
                return float(row["value"]) if row else float(default)
        except Exception:
            return float(default)

    @classmethod
    def thresholds(cls) -> Dict[str, float]:
        return {
            "similarity": cls._setting(
                "repetition_similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD
            ),
            "max_per_chapter": cls._setting(
                "repetition_max_findings_per_chapter", DEFAULT_MAX_FINDINGS_PER_CHAPTER
            ),
            "max_project": cls._setting(
                "repetition_max_project_findings", DEFAULT_MAX_PROJECT_FINDINGS
            ),
        }

    # ------------------------------------------------------------------
    # Core scan
    # ------------------------------------------------------------------
    @classmethod
    def scan(
        cls,
        project_id: int,
        chapters: List[Dict[str, Any]],
        threshold: Optional[float] = None,
        include_within_chapter: bool = True,
        persist: bool = True,
        scope: str = "PROJECT",
    ) -> Dict[str, Any]:
        resolved = cls.thresholds()
        threshold = float(threshold if threshold is not None else resolved["similarity"])
        threshold = max(0.3, min(0.99, threshold))

        prepared: List[Dict[str, Any]] = []
        paragraphs: List[Dict[str, Any]] = []
        for chapter in chapters:
            chapter_id = int(chapter.get("id") or 0)
            if not chapter_id:
                continue
            content = chapter.get("content") or ""
            if not content.strip():
                continue
            number = int(chapter.get("chapter_number") or 0)
            for record in _sentences(content):
                prepared.append(
                    {
                        **record,
                        "chapter_id": chapter_id,
                        "chapter_number": number,
                        "chapter_title": chapter.get("title", ""),
                    }
                )
            for paragraph, section in _paragraphs(content):
                paragraphs.append(
                    {
                        "text": paragraph,
                        "normalized": normalize_text(paragraph),
                        "tokens": tokenize(paragraph),
                        "section": section,
                        "chapter_id": chapter_id,
                        "chapter_number": number,
                        "chapter_title": chapter.get("title", ""),
                    }
                )

        findings: List[Dict[str, Any]] = []
        seen_pairs: set[Tuple[str, str, str]] = set()

        def add(
            original: Dict[str, Any],
            duplicate: Dict[str, Any],
            similarity: float,
            kind: str,
        ) -> None:
            if (
                original["chapter_id"] == duplicate["chapter_id"]
                and not include_within_chapter
            ):
                return
            match_type = _match_type(original, duplicate, similarity, kind)
            key = (
                _fingerprint(original["text"]),
                _fingerprint(duplicate["text"]),
                match_type,
            )
            if key in seen_pairs:
                return
            seen_pairs.add(key)
            severity = "HIGH" if match_type in {
                "exact_sentence", "duplicate_paragraph", "repeated_introduction",
                "repeated_conclusion", "reused_framework",
            } else "MEDIUM"
            findings.append(
                {
                    "chapter_id": duplicate["chapter_id"],
                    "original_chapter_id": original["chapter_id"],
                    "original_chapter_number": original["chapter_number"],
                    "duplicate_chapter_number": duplicate["chapter_number"],
                    "original_chapter_title": original.get("chapter_title", ""),
                    "duplicate_chapter_title": duplicate.get("chapter_title", ""),
                    "original_section": original.get("section", ""),
                    "duplicate_section": duplicate.get("section", ""),
                    "match_type": match_type,
                    "match_label": MATCH_TYPES.get(match_type, match_type),
                    "similarity": round(similarity, 3),
                    "original_text": original["text"],
                    "duplicate_text": duplicate["text"],
                    "recommendation": _recommendation(match_type),
                    "severity": severity,
                }
            )

        # Exact matches.
        exact: Dict[str, List[int]] = defaultdict(list)
        for index, record in enumerate(prepared):
            exact[record["fingerprint"]].append(index)
        for indices in exact.values():
            for left_pos in range(len(indices)):
                for right_pos in range(left_pos + 1, len(indices)):
                    original = prepared[indices[left_pos]]
                    duplicate = prepared[indices[right_pos]]
                    if (
                        original["chapter_number"], original["position"]
                    ) <= (duplicate["chapter_number"], duplicate["position"]):
                        first, second = original, duplicate
                    else:
                        first, second = duplicate, original
                    add(first, second, 1.0, "sentence")

        # Near-duplicate sentences via an inverted token index.
        inverted: Dict[str, set[int]] = defaultdict(set)
        for index, record in enumerate(prepared):
            for token in record["tokens"]:
                inverted[token].add(index)
        for index, record in enumerate(prepared):
            candidates: Counter[int] = Counter()
            for token in record["tokens"]:
                for other in inverted.get(token, ()):
                    if other > index:
                        candidates[other] += 1
            for other, shared in candidates.items():
                if shared < 3:
                    continue
                similarity = _jaccard(record["tokens"], prepared[other]["tokens"])
                if similarity < threshold:
                    continue
                first, second = record, prepared[other]
                if (
                    first["chapter_number"], first["position"]
                ) > (second["chapter_number"], second["position"]):
                    first, second = second, first
                add(first, second, similarity, "sentence")

        # Paragraph matches.
        para_inverted: Dict[str, set[int]] = defaultdict(set)
        for index, record in enumerate(paragraphs):
            for token in set(record["tokens"]):
                para_inverted[token].add(index)
        for index, record in enumerate(paragraphs):
            candidates: Counter[int] = Counter()
            for token in set(record["tokens"]):
                for other in para_inverted.get(token, ()):
                    if other > index:
                        candidates[other] += 1
            for other, shared in candidates.items():
                if shared < 6:
                    continue
                similarity = _jaccard(record["tokens"], paragraphs[other]["tokens"])
                if similarity < threshold:
                    continue
                first, second = record, paragraphs[other]
                if first["chapter_number"] > second["chapter_number"]:
                    first, second = second, first
                add(first, second, similarity, "paragraph")

        summary = cls._build_summary(findings, prepared, threshold, resolved)
        run_id = None
        if persist:
            run_id = cls._save_run(project_id, findings, summary, threshold, scope)
            summary["run_id"] = run_id
        summary["findings"] = findings
        return summary

    @classmethod
    def scan_project(
        cls,
        project_id: int,
        chapters: Optional[List[Dict[str, Any]]] = None,
        threshold: Optional[float] = None,
        persist: bool = True,
    ) -> Dict[str, Any]:
        if chapters is None:
            from services.outline_service import OutlineService

            chapters = OutlineService.get_chapters(project_id)
        return cls.scan(project_id, chapters, threshold=threshold, persist=persist)

    @staticmethod
    def _build_summary(
        findings: List[Dict[str, Any]],
        prepared: List[Dict[str, Any]],
        threshold: float,
        config: Dict[str, float],
    ) -> Dict[str, Any]:
        by_match: Dict[str, int] = {}
        by_chapter: Dict[int, int] = {}
        by_chapter_severity: Dict[int, List[str]] = defaultdict(list)
        for finding in findings:
            by_match[finding["match_type"]] = by_match.get(finding["match_type"], 0) + 1
            number = int(finding.get("duplicate_chapter_number") or 0)
            by_chapter[number] = by_chapter.get(number, 0) + 1
            by_chapter_severity[number].append(finding.get("severity", "MEDIUM"))
        exact_count = by_match.get("exact_sentence", 0) + by_match.get("normalized_sentence", 0)
        near_count = len(findings) - exact_count
        offending = sorted(
            number for number, count in by_chapter.items()
            if count > int(config["max_per_chapter"])
        )
        gate_ok = not offending and len(findings) <= int(config["max_project"])
        return {
            "threshold": threshold,
            "max_per_chapter": int(config["max_per_chapter"]),
            "max_project": int(config["max_project"]),
            "finding_count": len(findings),
            "exact_count": exact_count,
            "near_count": near_count,
            "by_match_type": by_match,
            "by_chapter": by_chapter,
            "critical_count": sum(1 for f in findings if f.get("severity") == "HIGH"),
            "offending_chapters": offending,
            "gate_ok": gate_ok,
            "resolved_sentence_count": sum(1 for f in findings if f.get("resolved")),
            "chapter_sentence_count": len({(p["chapter_id"], p["fingerprint"]) for p in prepared}),
        }

    @staticmethod
    def _save_run(
        project_id: int,
        findings: List[Dict[str, Any]],
        summary: Dict[str, Any],
        threshold: float,
        scope: str,
    ) -> int:
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO repetition_runs (
                    project_id, scope, similarity_threshold, exact_count, near_count,
                    finding_count, critical_count, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    scope,
                    threshold,
                    int(summary["exact_count"]),
                    int(summary["near_count"]),
                    int(summary["finding_count"]),
                    int(summary["critical_count"]),
                    json.dumps({k: v for k, v in summary.items() if k != "findings"}),
                ),
            )
            run_id = int(cur.lastrowid)
            for finding in findings:
                conn.execute(
                    """
                    INSERT INTO repetition_findings (
                        run_id, project_id, chapter_id, original_chapter_id,
                        original_chapter_number, duplicate_chapter_number,
                        original_section, duplicate_section, match_type,
                        similarity, original_text, duplicate_text,
                        recommendation, severity, resolved
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        project_id,
                        finding.get("chapter_id"),
                        finding.get("original_chapter_id"),
                        finding.get("original_chapter_number"),
                        finding.get("duplicate_chapter_number"),
                        finding.get("original_section"),
                        finding.get("duplicate_section"),
                        finding.get("match_type"),
                        float(finding.get("similarity") or 0.0),
                        finding.get("original_text"),
                        finding.get("duplicate_text"),
                        finding.get("recommendation"),
                        finding.get("severity", "MEDIUM"),
                        1 if finding.get("resolved") else 0,
                    ),
                )
        return run_id

    # ------------------------------------------------------------------
    # Reads and gates
    # ------------------------------------------------------------------
    @staticmethod
    def latest_run(project_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM repetition_runs WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["summary"] = json.loads(data.get("summary_json") or "{}")
            except (TypeError, ValueError):
                data["summary"] = {}
            return data

    @classmethod
    def list_findings(
        cls, run_id: int, chapter_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM repetition_findings WHERE run_id = ?"
        params: List[Any] = [run_id]
        if chapter_id is not None:
            query += " AND chapter_id = ?"
            params.append(chapter_id)
        query += " ORDER BY CASE severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END, id"
        with get_connection() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    @classmethod
    def chapter_summary(cls, chapter_id: int) -> Dict[str, Any]:
        """Findings for a chapter from the newest project-wide run."""
        with get_connection() as conn:
            chapter = conn.execute(
                "SELECT project_id FROM chapters WHERE id = ?", (chapter_id,)
            ).fetchone()
            if not chapter:
                return {"finding_count": 0, "gate_ok": True, "findings": []}
            project_id = int(chapter["project_id"])
            run = cls.latest_run(project_id)
            if not run:
                return {"finding_count": 0, "gate_ok": True, "findings": []}
            findings = cls.list_findings(int(run["id"]), chapter_id=chapter_id)
            config = cls.thresholds()
            return {
                "run_id": int(run["id"]),
                "finding_count": len(findings),
                "high_count": sum(1 for f in findings if f.get("severity") == "HIGH"),
                "max_per_chapter": int(config["max_per_chapter"]),
                "gate_ok": len(findings) <= int(config["max_per_chapter"]),
                "findings": findings,
            }

    @classmethod
    def chapter_gate(cls, chapter_id: int) -> Dict[str, Any]:
        summary = cls.chapter_summary(chapter_id)
        return {
            "ok": bool(summary.get("gate_ok")),
            "finding_count": int(summary.get("finding_count") or 0),
            "max_per_chapter": int(summary.get("max_per_chapter") or DEFAULT_MAX_FINDINGS_PER_CHAPTER),
            "findings": summary.get("findings") or [],
        }

    @classmethod
    def project_gate(cls, project_id: int) -> Dict[str, Any]:
        run = cls.latest_run(project_id)
        if not run:
            return {"ok": True, "finding_count": 0, "findings": [], "message": "No repetition scan has been run."}
        summary = run.get("summary") or {}
        return {
            "ok": bool(summary.get("gate_ok", True)),
            "finding_count": int(summary.get("finding_count") or 0),
            "offending_chapters": summary.get("offending_chapters") or [],
            "findings": cls.list_findings(int(run["id"])),
        }

    @staticmethod
    def build_rewrite_prompt(finding: Dict[str, Any], chapter_title: str) -> str:
        return (
            "Rewrite one duplicated passage so it serves the later chapter's unique purpose.\n"
            "Do not delete useful information and do not simply paraphrase. Add new, chapter-specific "
            "substance, an example or an action that is not in the original.\n"
            f"Chapter: {chapter_title}\n"
            f"Original chapter {finding.get('original_chapter_number')} "
            f"({finding.get('original_chapter_title', '')}):\n{finding.get('original_text', '')}\n\n"
            f"Duplicate in chapter {finding.get('duplicate_chapter_number')}:\n"
            f"{finding.get('duplicate_text', '')}\n\n"
            "Return only the replacement prose. Do not add a heading. Do not invent statistics, "
            "studies or citations."
        )
