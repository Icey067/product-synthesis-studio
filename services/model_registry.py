"""Task-specific local model routing for Ollama.

The application never assumes that a particular model is installed.  It detects
what Ollama actually has, lets the operator assign a model to each content task,
and always resolves to an installed model (or explicitly reports that none is
available).

No cloud provider is used anywhere in this module.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from config.settings import DEFAULT_CONTEXT_WINDOW, DEFAULT_OLLAMA_URL
from database.db import get_connection, transaction

# Canonical task identifiers.  Keep these stable: they are stored in
# application_settings as ``model_for_<task>``.
TASKS: Dict[str, Dict[str, Any]] = {
    "market_research": {
        "label": "Market research",
        "description": "Opportunity discovery and evidence-linked market analysis.",
    },
    "evidence_synthesis": {
        "label": "Evidence synthesis",
        "description": "Cross-source synthesis, conflicts and proven methods.",
    },
    "customer_transformation": {
        "label": "Customer transformation",
        "description": "Before/after transformation maps and milestones.",
    },
    "product_charter": {
        "label": "Product charter",
        "description": "Product charter, branding and sales positioning.",
    },
    "outline_generation": {
        "label": "Outline generation",
        "description": "Niche-specific chapter outline and learning objectives.",
    },
    "long_form_writing": {
        "label": "Long-form chapter writing",
        "description": "Chapter planning, section drafting and continuation.",
    },
    "content_editing": {
        "label": "Content editing",
        "description": "Expansion, transition smoothing and revision passes.",
    },
    "quality_review": {
        "label": "Quality review",
        "description": "Advisory editorial assessment; never authoritative for facts.",
    },
}

TASK_SETTING_PREFIX = "model_for_"


def _format_size(size_bytes: Optional[int]) -> str:
    try:
        value = float(size_bytes or 0)
    except (TypeError, ValueError):
        return "Unknown"
    if value <= 0:
        return "Unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


class ModelRegistry:
    """Detect installed Ollama models and route tasks to them."""

    _detail_cache: Dict[str, Dict[str, Any]] = {}
    _tags_cache: Dict[str, Any] = {"at": 0.0, "models": []}
    _tags_ttl_seconds = 15.0

    # ------------------------------------------------------------------
    # Connection / settings helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _base_url() -> str:
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM application_settings WHERE key = 'ollama_url'"
                ).fetchone()
                if row and row["value"]:
                    return str(row["value"])
        except Exception:
            pass
        return DEFAULT_OLLAMA_URL

    @staticmethod
    def _get_setting(key: str, default: str = "") -> str:
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM application_settings WHERE key = ?", (key,)
                ).fetchone()
                if row and row["value"] is not None:
                    return str(row["value"])
        except Exception:
            pass
        return default

    @staticmethod
    def _set_setting(key: str, value: str) -> None:
        with transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO application_settings (key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, value),
            )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @classmethod
    def detect_models(cls, refresh: bool = False) -> List[Dict[str, Any]]:
        """Return installed models with size and availability metadata.

        A failed or offline Ollama call returns an empty list rather than an
        invented model.
        """
        now = time.monotonic()
        if (
            not refresh
            and cls._tags_cache["models"]
            and now - cls._tags_cache["at"] < cls._tags_ttl_seconds
        ):
            return [dict(m) for m in cls._tags_cache["models"]]

        base_url = cls._base_url()
        models: List[Dict[str, Any]] = []
        try:
            resp = requests.get(f"{base_url}/api/tags", timeout=4.0)
            if resp.status_code == 200:
                for item in resp.json().get("models", []) or []:
                    details = item.get("details") or {}
                    size = item.get("size")
                    name = item.get("name") or item.get("model") or ""
                    if not name:
                        continue
                    models.append(
                        {
                            "name": name,
                            "available": True,
                            "size_bytes": size,
                            "size_label": _format_size(size),
                            "parameter_size": details.get("parameter_size") or "Unknown",
                            "quantization": details.get("quantization_level") or "Unknown",
                            "family": details.get("family") or "Unknown",
                            "modified_at": item.get("modified_at") or "",
                            "digest": item.get("digest") or "",
                        }
                    )
        except Exception:
            models = []

        models.sort(key=lambda m: m["name"].lower())
        cls._tags_cache = {"at": now, "models": models}
        return [dict(m) for m in models]

    @classmethod
    def installed_names(cls) -> List[str]:
        return [m["name"] for m in cls.detect_models()]

    @classmethod
    def model_details(cls, name: str, refresh: bool = False) -> Dict[str, Any]:
        """Fetch context length and family details from Ollama ``/api/show``."""
        if not name:
            return {}
        if not refresh and name in cls._detail_cache:
            return dict(cls._detail_cache[name])

        result: Dict[str, Any] = {
            "name": name,
            "context_length": int(DEFAULT_CONTEXT_WINDOW),
            "parameter_size": "Unknown",
            "quantization": "Unknown",
            "family": "Unknown",
        }
        try:
            resp = requests.post(
                f"{cls._base_url()}/api/show", json={"model": name}, timeout=6.0
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                info = data.get("model_info") or {}
                for key, value in info.items():
                    if key.endswith(".context_length") or key == "context_length":
                        try:
                            result["context_length"] = int(value)
                        except (TypeError, ValueError):
                            pass
                details = data.get("details") or {}
                result["parameter_size"] = details.get("parameter_size") or result["parameter_size"]
                result["quantization"] = details.get("quantization_level") or result["quantization"]
                result["family"] = details.get("family") or result["family"]
        except Exception:
            pass

        cls._detail_cache[name] = result
        return dict(result)

    @classmethod
    def connection_status(cls) -> Dict[str, Any]:
        address = cls._base_url()
        try:
            resp = requests.get(f"{address}/api/tags", timeout=3.0)
            if resp.status_code == 200:
                return {
                    "connected": True,
                    "address": address,
                    "message": "Connected to local Ollama.",
                }
            return {
                "connected": False,
                "address": address,
                "message": f"Ollama returned HTTP {resp.status_code}.",
            }
        except Exception as exc:
            return {
                "connected": False,
                "address": address,
                "message": f"Cannot connect to Ollama: {exc}",
            }

    # ------------------------------------------------------------------
    # Task routing
    # ------------------------------------------------------------------
    @staticmethod
    def task_label(task: str) -> str:
        return TASKS.get(task, {}).get("label", task.replace("_", " ").title())

    @classmethod
    def configured_model(cls, task: Optional[str] = None) -> str:
        """Return the operator's explicit choice, which may be unavailable."""
        if task:
            explicit = cls._get_setting(f"{TASK_SETTING_PREFIX}{task}")
            if explicit:
                return explicit
        return cls._get_setting("selected_model", "")

    @classmethod
    def resolve_model(cls, task: Optional[str] = None) -> Optional[str]:
        """Resolve a task to an *installed* model, or ``None``.

        Resolution order: the task-specific override, the global default, then
        any installed model.  A configured but missing model is skipped rather
        than sent to Ollama.
        """
        installed = cls.installed_names()
        if not installed:
            return None

        candidates = []
        if task:
            candidates.append(cls._get_setting(f"{TASK_SETTING_PREFIX}{task}"))
        candidates.append(cls._get_setting("selected_model", ""))
        for candidate in candidates:
            if candidate and candidate in installed:
                return candidate
        return installed[0]

    @classmethod
    def set_task_model(cls, task: str, model: str) -> None:
        if task not in TASKS:
            raise ValueError(f"Unknown model task: {task}")
        cls._set_setting(f"{TASK_SETTING_PREFIX}{task}", (model or "").strip())

    @classmethod
    def set_global_model(cls, model: str) -> None:
        cls._set_setting("selected_model", (model or "").strip())

    @classmethod
    def routing_table(cls) -> List[Dict[str, Any]]:
        models = cls.detect_models()
        installed = {m["name"] for m in models}
        global_choice = cls._get_setting("selected_model", "")
        rows = []
        for task, meta in TASKS.items():
            explicit = cls._get_setting(f"{TASK_SETTING_PREFIX}{task}", "")
            resolved = cls.resolve_model(task)
            rows.append(
                {
                    "task": task,
                    "label": meta["label"],
                    "description": meta["description"],
                    "explicit_model": explicit,
                    "global_model": global_choice,
                    "resolved_model": resolved,
                    "available": bool(resolved),
                    "using_fallback": bool(resolved and explicit and resolved != explicit),
                    "global_model_available": bool(global_choice and global_choice in installed),
                    "source": (
                        "task override"
                        if explicit and resolved == explicit
                        else (
                            "global default"
                            if global_choice and global_choice in installed and resolved == global_choice
                            else "installed fallback"
                        )
                    ),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Generation metrics
    # ------------------------------------------------------------------
    @classmethod
    def record_generation_metrics(cls, payload: Dict[str, Any]) -> None:
        data = dict(payload or {})
        data.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        try:
            cls._set_setting("last_generation_metrics", json.dumps(data))
        except Exception:
            pass

    @classmethod
    def last_generation_metrics(cls) -> Dict[str, Any]:
        raw = cls._get_setting("last_generation_metrics", "")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}
