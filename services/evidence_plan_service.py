"""Chapter evidence plan built from ranked, exact research excerpts (P2.2).

The plan answers, before writing starts:

* which sources were retrieved and how they were ranked;
* which required chapter topics those excerpts can actually support;
* which topics have insufficient evidence and therefore need more research;
* which exact numbers the model may cite (and only with the matching label).

It distinguishes general explanatory material from externally verifiable factual
claims.  Missing evidence is reported as missing; the service never invents a
fact or a number.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from database.db import get_connection, transaction
from services.evidence_service import normalize_text
from services.research_service import tokenize
from services.retrieval_service import RetrievalService

SUPPORTED_COVERAGE = 0.2
PARTIAL_COVERAGE = 0.08
NUMBER_RE = re.compile(
    r"(?:₹|inr|rs\.?|\$|us\$|usd)\s?\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:%|percent|per\s+cent)"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:billion|million|crore|lakh|trillion|thousand)\b",
    re.IGNORECASE,
)


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
        return [value.strip()]
    return []


def _coverage(topic: str, excerpt: str) -> float:
    topic_tokens = tokenize(topic)
    if not topic_tokens:
        return 0.0
    return len(topic_tokens & tokenize(excerpt)) / len(topic_tokens)


def _chapter_query(chapter: Dict[str, Any]) -> str:
    return " ".join(
        [
            str(chapter.get("title", "")),
            str(chapter.get("purpose", "")),
            " ".join(_as_list(chapter.get("key_topics"))),
            " ".join(_as_list(chapter.get("learning_objectives"))),
            str(chapter.get("expected_outcome", "")),
        ]
    )


class EvidencePlanService:
    @classmethod
    def build(
        cls,
        project_id: int,
        chapter: Dict[str, Any],
        sources: Optional[List[Dict[str, Any]]] = None,
        limit: int = 8,
    ) -> Dict[str, Any]:
        query = _chapter_query(chapter)
        evidence = RetrievalService.retrieve(
            project_id, query, sources=sources, limit=limit
        )
        items = evidence.get("items") or []
        source_rows = evidence.get("sources") or []

        required_topics = _as_list(chapter.get("key_topics"))
        if not required_topics:
            required_topics = [str(chapter.get("title") or "Chapter topic")]
        objectives = _as_list(chapter.get("learning_objectives"))
        required_topics = list(dict.fromkeys(required_topics + objectives))

        supported_claims: List[Dict[str, Any]] = []
        missing_evidence: List[Dict[str, Any]] = []
        for topic in required_topics:
            ranked: List[Dict[str, Any]] = []
            for item in items:
                coverage = _coverage(topic, item.get("excerpt") or "")
                if coverage <= 0:
                    continue
                ranked.append(
                    {
                        "source_id": item.get("source_id"),
                        "citation_label": item.get("citation_label"),
                        "coverage": round(coverage, 3),
                        "reliability": str(
                            (item.get("source") or {}).get("assessed_reliability")
                            or "UNASSESSED"
                        ).upper(),
                        "excerpt": (item.get("excerpt") or "")[:700],
                    }
                )
            ranked.sort(key=lambda r: (r["coverage"], r["reliability"] == "HIGH"), reverse=True)
            best = ranked[0] if ranked else None
            if best and best["coverage"] >= SUPPORTED_COVERAGE:
                status = "SUPPORTED"
            elif best and best["coverage"] >= PARTIAL_COVERAGE:
                status = "PARTIAL"
            else:
                status = "MISSING"
            record = {
                "topic": topic,
                "status": status,
                "coverage": best["coverage"] if best else 0.0,
                "source_ids": sorted({r["source_id"] for r in ranked[:2] if r.get("source_id")}),
                "labels": [r["citation_label"] for r in ranked[:2]],
                "excerpts": [r["excerpt"] for r in ranked[:2]],
            }
            supported_claims.append(record)
            if status != "SUPPORTED":
                missing_evidence.append(
                    {
                        "topic": topic,
                        "reason": (
                            "Only partial excerpt overlap; the claim may need qualification."
                            if status == "PARTIAL"
                            else "No retrieved excerpt supports this topic."
                        ),
                        "research_query": f"{topic} {chapter.get('title', '')} evidence {chapter.get('country', '')}".strip(),
                    }
                )

        supported_numbers: List[Dict[str, Any]] = []
        for item in items:
            excerpt = item.get("excerpt") or ""
            for match in NUMBER_RE.finditer(excerpt):
                supported_numbers.append(
                    {
                        "number": match.group(0).strip(),
                        "source_id": item.get("source_id"),
                        "citation_label": item.get("citation_label"),
                        "excerpt": excerpt[:500],
                    }
                )

        plan = {
            "project_id": project_id,
            "chapter_id": chapter.get("id"),
            "chapter_number": chapter.get("chapter_number"),
            "title": chapter.get("title", ""),
            "query": query,
            "candidate_count": evidence.get("candidate_count", 0),
            "source_count": len(source_rows),
            "excerpt_count": len(items),
            "sources": [
                {
                    "source_id": source.get("id"),
                    "title": source.get("title", ""),
                    "publisher": source.get("publisher", ""),
                    "url": source.get("url", ""),
                    "reliability": source.get("assessed_reliability", "UNASSESSED"),
                    "quality_score": round(float(source.get("quality_score") or 0.0), 3),
                    "recency_score": round(float(source.get("recency_score") or 0.0), 3),
                }
                for source in source_rows
            ],
            "items": [
                {
                    "source_id": item.get("source_id"),
                    "citation_label": item.get("citation_label"),
                    "relevance_score": item.get("relevance_score", 0.0),
                    "quality_score": item.get("quality_score", 0.0),
                    "excerpt": (item.get("excerpt") or "")[:700],
                }
                for item in items
            ],
            "supported_claims": supported_claims,
            "missing_evidence": missing_evidence,
            "supported_numbers": supported_numbers[:40],
            "contradictions": evidence.get("contradictions", []),
            "has_evidence": bool(items),
            "supported_count": sum(1 for c in supported_claims if c["status"] == "SUPPORTED"),
            "partial_count": sum(1 for c in supported_claims if c["status"] == "PARTIAL"),
            "missing_count": len(missing_evidence),
            "evidence_ready": bool(items) and len(missing_evidence) == 0,
            "boundaries": [
                "General explanatory material may be written without a citation when it is plainly explanatory.",
                "Every externally verifiable factual claim needs a cited excerpt that supports it.",
                "A number may only be used when the cited excerpt contains the exact number and unit.",
                "If a topic is marked MISSING, identify the gap instead of inventing evidence.",
            ],
        }
        return plan

    @staticmethod
    def save(chapter_id: int, plan: Dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                "UPDATE chapter_plans SET evidence_plan_json = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE chapter_id = ?",
                (json.dumps(plan), chapter_id),
            )

    @staticmethod
    def get(chapter_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT evidence_plan_json FROM chapter_plans WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
            if not row or not row["evidence_plan_json"]:
                return None
            try:
                return json.loads(row["evidence_plan_json"])
            except (TypeError, ValueError):
                return None

    @staticmethod
    def prompt_block(plan: Optional[Dict[str, Any]]) -> str:
        if not plan:
            return "No evidence plan is available. Write general explanatory material only and do not assert external facts or numbers."
        sources = "\n".join(
            f"{s.get('citation_label')} - {((s.get('source') or {}).get('title') or '')}"
            for s in plan.get("items", [])
        )
        supported = "\n".join(
            f"- {c['topic']}: {c['status']} (labels: {', '.join(c.get('labels') or []) or 'none'})"
            for c in plan.get("supported_claims", [])
        )
        missing = "\n".join(
            f"- {m['topic']}: {m['reason']} Search: {m['research_query']}"
            for m in plan.get("missing_evidence", [])
        )
        numbers = ", ".join(
            f"{n['number']} {n['citation_label']}" for n in plan.get("supported_numbers", [])[:20]
        )
        return (
            "SOURCES AND LABELS\n" + (sources or "None") + "\n\n"
            "TOPIC SUPPORT\n" + (supported or "None") + "\n\n"
            "MISSING EVIDENCE (do not invent)\n" + (missing or "None") + "\n\n"
            "NUMBERS FOUND IN EXCERPTS (only these exact figures may be cited)\n"
            + (numbers or "None") + "\n\n"
            + "\n".join(f"- {b}" for b in plan.get("boundaries", []))
        )
