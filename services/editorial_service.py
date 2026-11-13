"""Structured editorial rubric with a strict deterministic/advisory split.

Measurable dimensions (length, structure, citation validity, repetition,
heading order) are computed in code and are authoritative.  A language model may
add a clearly-labelled advisory opinion, which can never change a publication
gate, override the deterministic auditor, or claim factual verification.

Recommendations are persisted so a human can review, approve, dismiss or mark
one as applied.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from config.prompts_p2 import PROMPT_EDITORIAL_REVIEW
from database.db import get_connection, transaction
from services.chapter_contract import count_words, detect_sections
from services.ollama_client import OllamaClient
from services.research_service import tokenize
from utils.security import wrap_untrusted_content

RUBRIC_DIMENSIONS: Dict[str, str] = {
    "accuracy": "Accuracy",
    "practical_usefulness": "Practical usefulness",
    "depth": "Depth",
    "clarity": "Clarity",
    "originality_risk": "Originality risk (lower is better)",
    "relevance": "Relevance",
    "logical_flow": "Logical flow",
    "reader_engagement": "Reader engagement",
    "evidence_support": "Evidence support",
    "chapter_consistency": "Chapter consistency",
    "citation_correctness": "Citation correctness",
    "repetition": "Repetition (lower is better)",
    "chapter_uniqueness": "Chapter uniqueness",
    "reader_transformation": "Reader transformation",
    "instruction_completeness": "Instruction completeness",
}

# Kept as the stable P2 response shape for integrations that consume the
# original editorial rubric.  P2.2 additions are returned separately and are
# merged into the persisted audit report by AuditService.
CORE_RUBRIC_DIMENSIONS = (
    "accuracy", "practical_usefulness", "depth", "clarity", "originality_risk",
    "relevance", "logical_flow", "reader_engagement", "evidence_support",
    "chapter_consistency",
)

RECOMMENDATION_STATUSES = {"PENDING", "APPROVED", "DISMISSED", "APPLIED"}


def _clamp(value: float) -> float:
    return round(max(0.0, min(5.0, float(value))), 1)


class EditorialService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    # ------------------------------------------------------------------
    # Deterministic proxies
    # ------------------------------------------------------------------
    @staticmethod
    def _issue_count(issues: List[Dict[str, Any]], checks: set[str]) -> int:
        return sum(1 for issue in issues if issue.get("check") in checks)

    @classmethod
    def deterministic_rubric(
        cls,
        chapter: Dict[str, Any],
        deterministic: Dict[str, Any],
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        content = chapter.get("content") or ""
        structure = deterministic.get("structure") or {}
        issues = deterministic.get("issues") or []
        words = int(structure.get("word_count") or count_words(content))
        target = int(structure.get("target_words") or chapter.get("target_word_count") or 0)
        sections = detect_sections(content)
        source_count = len(sources or [])

        critical_issues = cls._issue_count(
            issues, {
                "citation_exists", "citation_required", "unsupported_statistic",
                "statistic_not_in_source", "statistic_context_mismatch",
                "unsupported_claim", "claim_human_review",
            }
        )
        accuracy = _clamp(5.0 - critical_issues * 1.5)

        useful_sections = sum(
            1 for key in ("step_by_step", "practical_example", "worksheet", "common_mistakes")
            if sections.get(key)
        )
        practical_usefulness = _clamp(useful_sections * 1.25)

        depth = _clamp(5.0 * (words / target)) if target else 0.0

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", content) if len(s.strip()) > 20]
        avg_sentence = (sum(len(s.split()) for s in sentences) / len(sentences)) if sentences else 0.0
        clarity = 5.0
        if avg_sentence > 28:
            clarity -= min(2.5, (avg_sentence - 28) / 6.0)
        elif avg_sentence and avg_sentence < 7:
            clarity -= 0.8
        clarity -= cls._issue_count(issues, {"repetition"}) * 0.8
        clarity = _clamp(clarity)

        paragraphs = [p.strip().lower() for p in re.split(r"\n{2,}", content) if len(p.split()) >= 25]
        duplicate_ratio = 0.0
        if paragraphs:
            duplicate_ratio = 1.0 - (len(set(paragraphs)) / len(paragraphs))
        originality_risk = _clamp(0.5 + duplicate_ratio * 9.0 + cls._issue_count(issues, {"repetition"}) * 0.7)

        query_tokens = tokenize(
            " ".join(
                [
                    str(chapter.get("title", "")),
                    str(chapter.get("purpose", "")),
                    " ".join(chapter.get("key_topics") or []) if isinstance(chapter.get("key_topics"), list) else str(chapter.get("key_topics") or ""),
                ]
            )
        )
        content_tokens = tokenize(content)
        overlap = (len(query_tokens & content_tokens) / max(1, len(query_tokens))) if query_tokens else 0.0
        relevance = _clamp(1.5 + overlap * 7.0)

        required_order = ["introduction", "step_by_step", "practical_example", "common_mistakes", "worksheet", "summary"]
        positions = []
        lower = content.lower()
        for key in required_order:
            pattern = {
                "introduction": r"##\s*introduction",
                "step_by_step": r"##\s*step[- ]by[- ]step",
                "practical_example": r"##\s*practical example",
                "common_mistakes": r"##\s*common mistakes",
                "worksheet": r"##\s*worksheet",
                "summary": r"##\s*chapter summary",
            }[key]
            match = re.search(pattern, lower)
            positions.append(match.start() if match else -1)
        present_positions = [p for p in positions if p >= 0]
        ordering_ok = present_positions == sorted(present_positions) and len(present_positions) >= 4
        logical_flow = _clamp(2.5 + (1.5 if ordering_ok else 0.0) + (1.0 if len(present_positions) >= 5 else 0.0))

        engagement_signals = sum(
            1
            for pattern in (r"\?", r"-\s*\[\s?\]", r"\bstep \d", r"\bexercise\b", r"\bworksheet\b", r"\bchecklist\b")
            if re.search(pattern, lower)
        )
        reader_engagement = _clamp(engagement_signals * 0.9)

        citation_ids = structure.get("citation_ids") or []
        invalid = structure.get("invalid_citations") or []
        if invalid:
            evidence_support = 0.0
        elif source_count and not citation_ids:
            evidence_support = 1.0
        elif citation_ids:
            evidence_support = _clamp(2.5 + min(2.5, len(citation_ids) * 0.5))
        else:
            evidence_support = 2.5

        chapter_consistency = _clamp(5.0 - duplicate_ratio * 5.0)

        # P2.2 explicit dimensions.
        claim_summary = deterministic.get("claim_summary") or {}
        repetition_summary = deterministic.get("repetition") or {}
        unsupported_numeric = int(claim_summary.get("unsupported_numeric_count") or 0)
        unsupported_claims = int(claim_summary.get("unsupported_count") or 0)
        partial_claims = int(claim_summary.get("partially_verified_count") or 0)
        citation_correctness = 5.0
        if invalid:
            citation_correctness = 0.0
        elif source_count and not citation_ids:
            citation_correctness = 1.0
        else:
            citation_correctness -= min(3.0, unsupported_claims * 0.75 + partial_claims * 0.35)
        citation_correctness = _clamp(citation_correctness)

        repetition_findings = int(repetition_summary.get("finding_count") or 0)
        repetition_score = _clamp(
            5.0 - repetition_findings * 0.6
            - cls._issue_count(issues, {"repetition"}) * 0.4
            - (2.0 if not repetition_summary.get("gate_ok", True) else 0.0)
        )

        overlaps = deterministic.get("architecture_overlaps") or []
        chapter_uniqueness = _clamp(5.0 - len(overlaps) * 2.5)

        all_sections = all(sections.get(k) for k in ("introduction", "step_by_step", "practical_example", "common_mistakes", "worksheet", "summary"))
        action_step = bool(re.search(r"24[- ]hour|action step|next step", lower))
        transformation = 1.5 + (1.5 if all_sections else 0.0) + (1.0 if action_step else 0.0)
        if "after_state" in (chapter.get("purpose") or "").lower():
            transformation += 0.5
        reader_transformation = _clamp(transformation)

        instruction_completeness = _clamp(
            1.0
            + (1.0 if all_sections else 0.0)
            + (1.0 if sections.get("worksheet") else 0.0)
            + (1.0 if action_step else 0.0)
            + (1.0 if re.search(r"-\s*\[\s?\]|checklist|template|script", lower) else 0.0)
        )

        scores = {
            "accuracy": accuracy,
            "practical_usefulness": practical_usefulness,
            "depth": depth,
            "clarity": clarity,
            "originality_risk": originality_risk,
            "relevance": relevance,
            "logical_flow": logical_flow,
            "reader_engagement": reader_engagement,
            "evidence_support": evidence_support,
            "chapter_consistency": chapter_consistency,
            "citation_correctness": citation_correctness,
            "repetition": repetition_score,
            "chapter_uniqueness": chapter_uniqueness,
            "reader_transformation": reader_transformation,
            "instruction_completeness": instruction_completeness,
        }
        return {
            key: {
                "score": value,
                "source": "deterministic_proxy",
                "authoritative": True,
            }
            for key, value in scores.items()
        }

    # ------------------------------------------------------------------
    # Advisory model review
    # ------------------------------------------------------------------
    def advisory_review(
        self,
        chapter: Dict[str, Any],
        charter: Dict[str, Any],
        deterministic: Dict[str, Any],
    ) -> Dict[str, Any]:
        content = chapter.get("content") or ""
        findings = json.dumps(
            {
                "score": deterministic.get("score"),
                "checks": deterministic.get("checks"),
                "issues": deterministic.get("issues"),
            },
            indent=1,
        )
        prompt = PROMPT_EDITORIAL_REVIEW.format(
            product_title=charter.get("product_title", ""),
            target_audience=charter.get("target_audience", ""),
            chapter_number=chapter.get("chapter_number", 1),
            chapter_title=chapter.get("title", ""),
            deterministic_findings=findings,
            chapter_content=wrap_untrusted_content(content[:14000], label="CHAPTER_CONTENT"),
        )
        ok, response = False, "Advisory review unavailable."
        try:
            ok, response = self.ai.generate(
                prompt=prompt,
                temperature=0.2,
                format_json=True,
                timeout=240,
                task="quality_review",
            )
        except Exception as exc:  # a mock/offline client must not break an audit
            return {"available": False, "message": f"Advisory review unavailable: {exc}", "dimensions": {}, "recommendations": []}
        if not ok:
            return {"available": False, "message": f"Advisory review unavailable: {response}", "dimensions": {}, "recommendations": []}
        try:
            start, end = response.find("{"), response.rfind("}")
            data = json.loads(response[start : end + 1])
        except Exception:
            return {"available": False, "message": "Advisory review returned unparsable JSON.", "dimensions": {}, "recommendations": []}
        raw_dimensions = data.get("dimensions") or {}
        dimensions: Dict[str, Any] = {}
        for key in RUBRIC_DIMENSIONS:
            if key in raw_dimensions:
                try:
                    dimensions[key] = {
                        "score": _clamp(float(raw_dimensions[key])),
                        "source": "model_advisory",
                        "authoritative": False,
                    }
                except (TypeError, ValueError):
                    continue
        recommendations = []
        for item in data.get("recommendations") or []:
            if not isinstance(item, dict):
                continue
            message = str(item.get("message") or "").strip()
            if not message:
                continue
            recommendations.append(
                {
                    "category": str(item.get("category") or "editorial")[:60],
                    "severity": str(item.get("severity") or "MEDIUM").upper()[:10],
                    "message": message,
                    "source": "MODEL_ADVISORY",
                }
            )
        return {
            "available": bool(dimensions),
            "message": str(data.get("summary") or "Advisory assessment recorded."),
            "dimensions": dimensions,
            "recommendations": recommendations,
            "advisory": True,
        }

    # ------------------------------------------------------------------
    # Combined assessment
    # ------------------------------------------------------------------
    def assess(
        self,
        chapter: Dict[str, Any],
        charter: Dict[str, Any],
        deterministic: Dict[str, Any],
        sources: Optional[List[Dict[str, Any]]] = None,
        include_model: bool = True,
    ) -> Dict[str, Any]:
        deterministic_rubric = self.deterministic_rubric(chapter, deterministic, sources)
        advisory = (
            self.advisory_review(chapter, charter, deterministic)
            if include_model
            else {"available": False, "message": "Model review disabled.", "dimensions": {}, "recommendations": []}
        )

        dimensions: Dict[str, Any] = {}
        publication_quality_dimensions: Dict[str, Any] = {}
        for key in RUBRIC_DIMENSIONS:
            entry = dict(deterministic_rubric[key])
            if key in advisory.get("dimensions", {}):
                entry["advisory_score"] = advisory["dimensions"][key]["score"]
                entry["advisory_source"] = "model_advisory"
            if key in CORE_RUBRIC_DIMENSIONS:
                dimensions[key] = entry
            else:
                publication_quality_dimensions[key] = entry

        deterministic_scores = [e["score"] for e in deterministic_rubric.values()]
        deterministic_average = round(sum(deterministic_scores) / len(deterministic_scores), 2) if deterministic_scores else 0.0

        recommendations: List[Dict[str, Any]] = []
        for issue in deterministic.get("issues") or []:
            message = issue.get("correction") or issue.get("message")
            if not message:
                continue
            recommendations.append(
                {
                    "code": str(issue.get("check") or "deterministic_issue"),
                    "category": "deterministic",
                    "severity": str(issue.get("severity") or "MEDIUM"),
                    "message": str(message),
                    "source": "DETERMINISTIC",
                }
            )
        recommendations.extend(advisory.get("recommendations", []))

        status = "NEEDS_REVISION" if any(
            str(i.get("severity", "")).upper() in {"CRITICAL", "HIGH"} for i in deterministic.get("issues") or []
        ) or not (deterministic.get("repetition") or {}).get("gate_ok", True) else "REVIEWED"

        return {
            "dimensions": dimensions,
            "publication_quality_dimensions": publication_quality_dimensions,
            "deterministic_rubric": deterministic_rubric,
            "deterministic_average": deterministic_average,
            "advisory": advisory,
            "advisory_available": bool(advisory.get("available")),
            "advisory_note": (
                "Model scores are advisory only. Deterministic checks remain authoritative "
                "for citations, statistics, structure and length."
            ),
            "recommendations": recommendations,
            "status": status,
        }

    # ------------------------------------------------------------------
    # Persistence / user review
    # ------------------------------------------------------------------
    @staticmethod
    def save_recommendations(
        project_id: int,
        chapter_id: Optional[int],
        report_id: Optional[int],
        recommendations: List[Dict[str, Any]],
    ) -> int:
        if not recommendations:
            return 0
        with transaction() as conn:
            # Replace only this report's pending recommendations so approved
            # decisions made by a human are not silently discarded.
            if report_id is not None:
                conn.execute(
                    "DELETE FROM editorial_recommendations WHERE report_id = ? AND status = 'PENDING'",
                    (report_id,),
                )
            count = 0
            for item in recommendations:
                conn.execute(
                    """
                    INSERT INTO editorial_recommendations (
                        project_id, chapter_id, report_id, code, category, severity,
                        message, source, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
                    """,
                    (
                        project_id,
                        chapter_id,
                        report_id,
                        str(item.get("code") or item.get("category") or "editorial")[:80],
                        str(item.get("category") or "editorial")[:60],
                        str(item.get("severity") or "MEDIUM").upper()[:10],
                        str(item.get("message") or "")[:2000],
                        str(item.get("source") or "DETERMINISTIC")[:30],
                    ),
                )
                count += 1
        return count

    @staticmethod
    def list_recommendations(
        project_id: int,
        chapter_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM editorial_recommendations WHERE project_id = ?"
        params: List[Any] = [project_id]
        if chapter_id is not None:
            query += " AND chapter_id = ?"
            params.append(chapter_id)
        if status:
            query += " AND status = ?"
            params.append(status.upper())
        query += " ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END, id"
        with get_connection() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    @staticmethod
    def set_recommendation_status(recommendation_id: int, status: str) -> bool:
        status = (status or "").upper()
        if status not in RECOMMENDATION_STATUSES:
            return False
        with transaction() as conn:
            cur = conn.execute(
                "UPDATE editorial_recommendations SET status = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (status, recommendation_id),
            )
            return cur.rowcount > 0

    @staticmethod
    def set_chapter_editorial_status(chapter_id: int, status: str) -> bool:
        with transaction() as conn:
            cur = conn.execute(
                "UPDATE chapters SET editorial_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, chapter_id),
            )
            return cur.rowcount > 0

    @staticmethod
    def get_latest_editorial(chapter_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, editorial_rubric_json, recommendations_json, editorial_advisory "
                "FROM quality_reports WHERE chapter_id = ? ORDER BY id DESC LIMIT 1",
                (chapter_id,),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["rubric"] = json.loads(data.get("editorial_rubric_json") or "{}")
            except (TypeError, ValueError):
                data["rubric"] = {}
            try:
                data["recommendations"] = json.loads(data.get("recommendations_json") or "[]")
            except (TypeError, ValueError):
                data["recommendations"] = []
            return data
