"""Local model comparison on identical, reproducible tasks.

The tool measures observable outputs — JSON validity, citation compliance,
chapter structure, word count, topic relevance, instruction compliance and
generation time — for whichever models are actually installed.  It uses a
clearly labelled temporary benchmark project.  It does **not** claim that the
winning model is generally superior; results are benchmark-specific and the
human rubric column stays empty until a person records a score.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from config.prompts_p2 import PROMPT_BENCHMARK_CHAPTER, PROMPT_BENCHMARK_JSON
from config.settings import APP_VERSION
from database.db import get_connection, transaction
from models.schemas import ProjectCreate
from services.chapter_contract import CITATION_RE, detect_sections
from services.model_registry import ModelRegistry
from services.ollama_client import OllamaClient
from services.product_format_service import contract_text
from services.project_service import ProjectService
from services.research_service import ResearchService, tokenize

BENCHMARK_PROJECT_NAME = "MODEL BENCHMARK — temporary evaluation project"
BENCHMARK_TOPIC = "Practical digital growth for local micro-businesses"
BENCHMARK_AUDIENCE = "local micro-business owners in India"
BENCHMARK_TASK = "long_form_writing"

REQUIRED_HEADINGS = [
    "## Introduction",
    "## Step-by-Step Implementation",
    "## Practical Example",
    "## Common Mistakes & Troubleshooting",
    "## Worksheet / Exercise",
    "## Chapter Summary & 24-Hour Action Step",
]


class ModelEvaluationService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    # ------------------------------------------------------------------
    # Temporary benchmark project
    # ------------------------------------------------------------------
    @staticmethod
    def ensure_benchmark_project() -> int:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM projects WHERE name = ? ORDER BY id DESC LIMIT 1",
                (BENCHMARK_PROJECT_NAME,),
            ).fetchone()
            if row:
                return int(row["id"])
        return ProjectService.create_project(
            ProjectCreate(
                name=BENCHMARK_PROJECT_NAME,
                country="India",
                language="English",
                target_customer=BENCHMARK_AUDIENCE,
                expertise_mode="NON_EXPERT",
                product_category="Business & Productivity",
                product_format="Ebook",
            )
        )

    @staticmethod
    def citation_fixture_project(project_id: int) -> Dict[str, Any]:
        """A clearly-marked synthetic citation fixture.

        It exists only to test whether a model copies the exact label it was
        given.  It is never used as evidence in a real product project.
        """
        title = "BENCHMARK CITATION FIXTURE — synthetic test text, not evidence"
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM research_sources WHERE project_id = ? AND title = ?",
                (project_id, title),
            ).fetchone()
            if row:
                return {"id": int(row["id"]), "label": f"[Source-{int(row['id'])}]", "title": title}
        content = (
            "This is a synthetic benchmark fixture. It contains no real statistic "
            "and must never be presented as evidence. Its only purpose is to test "
            "that a model copies the supplied citation label exactly."
        )
        ok, source_id, message = ResearchService.add_source(
            project_id,
            title,
            "MANUAL_NOTE",
            content,
            publisher="PSS benchmark harness",
        )
        if not ok:
            raise RuntimeError(f"Could not create benchmark citation fixture: {message}")
        return {"id": int(source_id), "label": f"[Source-{int(source_id)}]", "title": title}

    # ------------------------------------------------------------------
    # Metric helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _json_metric(raw: str) -> Dict[str, Any]:
        cleaned = (raw or "").strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
        try:
            data = json.loads(cleaned)
            valid = isinstance(data, dict)
        except Exception:
            data, valid = None, False
        quality = 0.0
        if valid:
            quality = 1.0
            for key in ("title", "learning_objectives", "key_topics", "practical_exercise"):
                if key not in data:
                    quality -= 0.15
            if isinstance(data.get("learning_objectives"), list) and len(data["learning_objectives"]) >= 3:
                quality = min(1.0, quality)
            else:
                quality -= 0.1
        return {"valid": valid, "quality": round(max(0.0, quality), 3), "data": data}

    @staticmethod
    def _citation_metric(content: str, allowed: Dict[str, Any]) -> Dict[str, Any]:
        found = [int(m) for m in CITATION_RE.findall(content or "")]
        allowed_id = int(allowed["id"])
        invalid = sorted({c for c in found if c != allowed_id})
        copies_label = allowed_id in found
        # A fabricated label is the failure this metric exists to catch.
        score = 0.0
        if found and not invalid:
            score = 1.0
        elif found and invalid:
            score = 0.0
        elif not found:
            score = 0.5  # not citing is honest but does not demonstrate compliance
        return {
            "score": score,
            "found": found,
            "invalid": invalid,
            "copied_supplied_label": copies_label,
        }

    @staticmethod
    def _structure_metric(content: str) -> Dict[str, Any]:
        sections = detect_sections(content or "")
        lower = (content or "").lower()
        present = sum(1 for heading in REQUIRED_HEADINGS if heading.lower() in lower)
        return {
            "score": round(present / len(REQUIRED_HEADINGS), 3),
            "headings_present": present,
            "headings_required": len(REQUIRED_HEADINGS),
            "sections": sections,
        }

    @staticmethod
    def _topic_metric(content: str) -> float:
        query = tokenize(BENCHMARK_TOPIC + " " + BENCHMARK_AUDIENCE)
        content_tokens = tokenize(content or "")
        if not query:
            return 0.0
        overlap = len(query & content_tokens) / len(query)
        return round(min(1.0, overlap * 2.5), 3)

    @staticmethod
    def _instruction_metric(content: str, target_words: int = 900) -> Dict[str, Any]:
        words = len((content or "").split())
        checks = {
            "has_all_headings": all(h.lower() in (content or "").lower() for h in REQUIRED_HEADINGS),
            "no_markdown_fence": "```" not in (content or ""),
            "length_reasonable": words >= int(target_words * 0.45),
        }
        return {
            "score": round(sum(1 for v in checks.values() if v) / len(checks), 3),
            "checks": checks,
            "words": words,
        }

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def evaluate_model(
        self,
        model: str,
        project_id: Optional[int] = None,
        target_words: int = 900,
        timeout: int = 420,
    ) -> Dict[str, Any]:
        project_id = project_id or self.ensure_benchmark_project()
        fixture = self.citation_fixture_project(project_id)

        json_prompt = PROMPT_BENCHMARK_JSON.format(
            topic=BENCHMARK_TOPIC, target_audience=BENCHMARK_AUDIENCE
        )
        json_started = time.time()
        json_ok, json_raw = self.ai.generate(
            prompt=json_prompt,
            model=model,
            temperature=0.2,
            format_json=True,
            timeout=timeout,
        )
        json_ms = round((time.time() - json_started) * 1000, 1)
        json_result = self._json_metric(json_raw if json_ok else "")

        chapter_prompt = PROMPT_BENCHMARK_CHAPTER.format(
            topic=BENCHMARK_TOPIC,
            chapter_title="Build a simple digital presence that brings repeat customers",
            target_audience=BENCHMARK_AUDIENCE,
            country="India",
            language="English",
            product_format="Ebook",
            target_words=target_words,
            citation_labels=fixture["label"],
        )
        chapter_prompt += "\n\nFORMAT CONTRACT:\n" + contract_text("Ebook")
        chapter_started = time.time()
        chapter_ok, chapter_raw = self.ai.generate(
            prompt=chapter_prompt,
            model=model,
            temperature=0.6,
            timeout=timeout,
        )
        generation_ms = round((time.time() - chapter_started) * 1000, 1)
        content = chapter_raw if chapter_ok else ""

        structure = self._structure_metric(content)
        citation = self._citation_metric(content, fixture)
        instruction = self._instruction_metric(content, target_words)
        relevance = self._topic_metric(content)

        metrics = {
            "json_valid": bool(json_result["valid"]),
            "json_quality": json_result["quality"],
            "json_generation_ms": json_ms,
            "chapter_generation_ok": bool(chapter_ok),
            "chapter_generation_ms": generation_ms,
            "structure": structure,
            "citation": citation,
            "instruction": instruction,
            "topic_relevance": relevance,
            "actual_word_count": instruction["words"],
            "model_registry": ModelRegistry.model_details(model),
        }

        rubric = {
            "human_score": None,
            "human_notes": None,
            "human_rubric_pending": True,
            "advisory_note": (
                "Automated metrics are benchmark-specific. They do not prove that "
                "one model is generally superior. A human must review the samples."
            ),
            "sample_excerpt": content[:1200],
            "project_id": project_id,
            "benchmark_topic": BENCHMARK_TOPIC,
            "citation_fixture": fixture["label"],
        }

        status = "COMPLETED" if (json_ok or chapter_ok) else "FAILED"
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO model_evaluations (
                    project_id, model, task, status, generation_ms, metrics_json, rubric_json,
                    json_valid, citation_compliance, structure_score, word_count,
                    topic_relevance, instruction_compliance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    model,
                    BENCHMARK_TASK,
                    status,
                    generation_ms,
                    json.dumps(metrics),
                    json.dumps(rubric),
                    1 if json_result["valid"] else 0,
                    citation["score"],
                    structure["score"],
                    instruction["words"],
                    relevance,
                    instruction["score"],
                ),
            )
            evaluation_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (
                    project_id,
                    "MODEL_EVALUATED",
                    f"Benchmark evaluated {model} (JSON {json_result['valid']}, "
                    f"structure {structure['score']}, {instruction['words']} words).",
                ),
            )

        return {
            "evaluation_id": evaluation_id,
            "model": model,
            "project_id": project_id,
            "status": status,
            "generation_ms": generation_ms,
            "metrics": metrics,
            "scores": {
                "json_valid": 1.0 if json_result["valid"] else 0.0,
                "citation_compliance": citation["score"],
                "structure": structure["score"],
                "topic_relevance": relevance,
                "instruction_compliance": instruction["score"],
            },
            "rubric": rubric,
        }

    def evaluate_installed(
        self,
        models: Optional[List[str]] = None,
        target_words: int = 900,
        timeout: int = 420,
    ) -> Dict[str, Any]:
        models = models or ModelRegistry.installed_names()
        if not models:
            return {
                "ok": False,
                "message": "No installed Ollama models were detected; nothing was evaluated.",
                "results": [],
            }
        project_id = self.ensure_benchmark_project()
        results = []
        for model in models:
            try:
                results.append(self.evaluate_model(model, project_id=project_id, target_words=target_words, timeout=timeout))
            except Exception as exc:
                results.append({"model": model, "status": "FAILED", "error": str(exc)})
        return {
            "ok": True,
            "project_id": project_id,
            "results": results,
            "note": (
                "Scores are limited to this benchmark task. No general superiority "
                "claim is made, and human review is still required."
            ),
            "app_version": APP_VERSION,
        }

    # ------------------------------------------------------------------
    # Human rubric and reporting
    # ------------------------------------------------------------------
    @staticmethod
    def set_human_score(evaluation_id: int, score: float, notes: str = "") -> bool:
        if not 0.0 <= float(score) <= 5.0:
            return False
        with transaction() as conn:
            row = conn.execute(
                "SELECT rubric_json FROM model_evaluations WHERE id = ?", (evaluation_id,)
            ).fetchone()
            if not row:
                return False
            try:
                rubric = json.loads(row["rubric_json"] or "{}")
            except (TypeError, ValueError):
                rubric = {}
            rubric["human_score"] = float(score)
            rubric["human_notes"] = notes
            rubric["human_rubric_pending"] = False
            conn.execute(
                "UPDATE model_evaluations SET rubric_json = ?, human_score = ? WHERE id = ?",
                (json.dumps(rubric), float(score), evaluation_id),
            )
            return True

    @staticmethod
    def list_evaluations(project_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
        query = "SELECT * FROM model_evaluations"
        params: List[Any] = []
        if project_id is not None:
            query += " WHERE project_id = ?"
            params.append(project_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            for key in ("metrics_json", "rubric_json"):
                try:
                    item[key.replace("_json", "")] = json.loads(item.get(key) or "{}")
                except (TypeError, ValueError):
                    item[key.replace("_json", "")] = {}
            results.append(item)
        return results
