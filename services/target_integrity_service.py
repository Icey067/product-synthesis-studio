"""Integrity checks for measurable transformation targets.

Targets are useful planning tools, but a product must never present an
unsupported target as a promised result.  This service keeps the distinction
explicit and deterministic: evidence-backed findings, reader-selected goals,
illustrative scenarios, and guarantees are separate categories.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List

from database.db import get_connection, transaction


TARGET_CATEGORIES = {
    "EVIDENCE_SUPPORTED_OUTCOME",
    "ASPIRATIONAL_GOAL",
    "USER_DEFINED_OBJECTIVE",
    "ILLUSTRATIVE_OUTCOME",
    "GUARANTEED_CLAIM",
}

_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|percent|x|times|days?|weeks?|months?)\b", re.I)
_GUARANTEE = re.compile(
    r"\b(will|guarantee(?:s|d)?|ensure(?:s|d)?|deliver(?:s|ed)?|"
    r"increase(?:s|d)?|improve(?:s|d)?|boost(?:s|ed)?)\b",
    re.I,
)
_ILLUSTRATIVE = re.compile(r"\b(example|illustrative|hypothetical|scenario|may|might|could)\b", re.I)
_GOAL = re.compile(r"\b(goal|target|aim|objective|hope|intend|plan to)\b", re.I)


class TargetIntegrityService:
    @staticmethod
    def classify(text: str, category: str = "") -> str:
        requested = str(category or "").upper()
        if requested in TARGET_CATEGORIES:
            return requested
        if _GUARANTEE.search(text or ""):
            return "GUARANTEED_CLAIM"
        if _ILLUSTRATIVE.search(text or ""):
            return "ILLUSTRATIVE_OUTCOME"
        if _GOAL.search(text or ""):
            return "ASPIRATIONAL_GOAL"
        return "USER_DEFINED_OBJECTIVE"

    @classmethod
    def normalize(cls, item: Any) -> Dict[str, Any] | None:
        if isinstance(item, str):
            text = item.strip()
            raw: Dict[str, Any] = {"target_text": text}
        elif isinstance(item, dict):
            raw = dict(item)
            text = str(
                raw.get("target_text")
                or raw.get("text")
                or raw.get("measurable_progress")
                or raw.get("result")
                or ""
            ).strip()
        else:
            return None
        if not text:
            return None
        category = cls.classify(text, str(raw.get("target_category") or raw.get("category") or ""))
        source_ids = raw.get("source_ids") or raw.get("source_id") or []
        if not isinstance(source_ids, list):
            source_ids = [source_ids]
        source_ids = [int(v) for v in source_ids if str(v).isdigit()]
        measurement = str(raw.get("measurement_method") or raw.get("measurement") or "").strip()
        metric = str(raw.get("metric") or "").strip()
        value = str(raw.get("target_value") or "").strip()
        if not value:
            match = _NUMBER.search(text)
            value = match.group(0) if match else ""
        evidence_status = "SUPPORTED" if category == "EVIDENCE_SUPPORTED_OUTCOME" and source_ids else "NOT_INDEPENDENTLY_ESTABLISHED"
        if category == "GUARANTEED_CLAIM":
            evidence_status = "REJECTED"
        label = ""
        if evidence_status == "NOT_INDEPENDENTLY_ESTABLISHED":
            label = "Illustrative target — not independently established."
        elif evidence_status == "REJECTED":
            label = "Unsupported guaranteed outcome — rewrite as an illustrative or user-defined target."
        return {
            "target_text": text,
            "target_value": value,
            "metric": metric,
            "measurement_method": measurement,
            "population": str(raw.get("population") or "").strip(),
            "time_period": str(raw.get("time_period") or "").strip(),
            "target_category": category,
            "source_ids": source_ids,
            "evidence_status": evidence_status,
            "display_label": label,
        }

    @classmethod
    def save(cls, project_id: int, targets: Iterable[Any]) -> List[Dict[str, Any]]:
        normalized = [record for value in targets if (record := cls.normalize(value))]
        with transaction() as conn:
            conn.execute("DELETE FROM transformation_targets WHERE project_id = ?", (project_id,))
            for target in normalized:
                conn.execute(
                    """
                    INSERT INTO transformation_targets (
                        project_id, target_text, target_value, metric, measurement_method,
                        population, time_period, target_category, source_ids_json,
                        evidence_status, display_label
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, target["target_text"], target["target_value"], target["metric"],
                        target["measurement_method"], target["population"], target["time_period"],
                        target["target_category"], json.dumps(target["source_ids"]),
                        target["evidence_status"], target["display_label"],
                    ),
                )
        return cls.list(project_id)

    @staticmethod
    def list(project_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM transformation_targets WHERE project_id = ? ORDER BY id", (project_id,)
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            target = dict(row)
            try:
                target["source_ids"] = json.loads(target.pop("source_ids_json") or "[]")
            except (TypeError, ValueError):
                target["source_ids"] = []
            result.append(target)
        return result

    @classmethod
    def assessment(cls, project_id: int) -> Dict[str, Any]:
        targets = cls.list(project_id)
        rejected = [t for t in targets if t["evidence_status"] == "REJECTED"]
        unsupported = [t for t in targets if t["evidence_status"] == "NOT_INDEPENDENTLY_ESTABLISHED"]
        return {
            "targets": targets,
            "guaranteed_claim_count": len(rejected),
            "unestablished_target_count": len(unsupported),
            "gate_ok": not rejected,
        }
