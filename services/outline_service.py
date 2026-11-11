import json
from functools import partial
from typing import Dict, Any, List, Optional
from database.db import get_connection, transaction
from services.ollama_client import OllamaClient
from services.product_format_service import contract_text, validate_outline
from services.generation_utils import generate_with_validation
from services.research_service import ResearchService
from config.prompts import PROMPT_OUTLINE
from config.settings import DEFAULT_REQUIRED_CHAPTER_COUNT
from models.schemas import OutlineResponse

class OutlineService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    @staticmethod
    def _project(project_id: int) -> Dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return dict(row) if row else {}

    def generate_outline(
        self,
        project_id: int,
        charter: Dict[str, Any],
        transformation_map: Optional[Dict[str, Any]] = None,
        chapter_count: Optional[int] = None,
    ) -> tuple[bool, List[Dict[str, Any]], str]:
        project = self._project(project_id)
        settings = self._settings(project_id)
        chapter_count = int(chapter_count or settings["required_chapters"])
        product_format = project.get("product_format") or charter.get("product_format") or "Ebook"
        sources = ResearchService.get_sources(project_id)
        research_context, _ = ResearchService.build_evidence_context(
            project,
            sources,
            query_extra=f"{charter.get('product_title', '')} {charter.get('customer_problem', '')}",
            limit=6,
            max_chars_per_source=450,
        )

        milestones = []
        if transformation_map:
            for m in transformation_map.get("milestones", [])[:6]:
                milestones.append(f"Phase {m.get('phase_number', '?')}: {m.get('title', '')} — {m.get('measurable_progress', '')}")
        transformation_summary = "; ".join(milestones) or "Phased implementation milestones"

        prompt = PROMPT_OUTLINE.format(
            chapter_count=chapter_count,
            product_format=product_format,
            product_title=charter.get("product_title", "Digital Playbook"),
            subtitle=charter.get("subtitle", "Practical Implementation Guide"),
            target_audience=charter.get("target_audience", project.get("target_customer", "Target reader")),
            customer_problem=charter.get("customer_problem", ""),
            core_promise=charter.get("core_promise", ""),
            unique_mechanism=charter.get("unique_mechanism", ""),
            transformation_summary=transformation_summary,
            research_findings=research_context,
            format_contract=contract_text(product_format),
        )

        ok, validated, msg, issues = generate_with_validation(
            self.ai,
            prompt,
            OutlineResponse,
            partial(validate_outline, required_chapters=chapter_count),
            product_format,
            stage="Product Outline",
            temperature=0.6,
            max_attempts=3,
            task="outline_generation",
        )
        if not ok or validated is None:
            return False, [], msg

        saved_chapters = []
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO product_outlines (project_id, product_format, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_id) DO UPDATE SET
                    product_format = excluded.product_format,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (project_id, product_format),
            )
            for ch in validated.chapters:
                cur.execute(
                    """
                    INSERT INTO chapters (
                        project_id, chapter_number, title, purpose, learning_objectives_json,
                        key_topics_json, research_references_json, practical_exercises, expected_outcome,
                        target_word_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, chapter_number) DO UPDATE SET
                        title = excluded.title,
                        purpose = excluded.purpose,
                        learning_objectives_json = excluded.learning_objectives_json,
                        key_topics_json = excluded.key_topics_json,
                        research_references_json = excluded.research_references_json,
                        practical_exercises = excluded.practical_exercises,
                        expected_outcome = excluded.expected_outcome,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        project_id,
                        ch.chapter_number,
                        ch.title,
                        ch.purpose,
                        json.dumps(ch.learning_objectives),
                        json.dumps(ch.key_topics),
                        json.dumps(ch.research_references),
                        ch.practical_exercises,
                        ch.expected_outcome,
                        settings["target_words"],
                    ),
                )
                saved_chapters.append(ch.model_dump())

            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (project_id, "OUTLINE_GENERATED", f"Created {len(saved_chapters)}-chapter niche outline for {product_format}"),
            )

        return True, saved_chapters, "Product outline generated successfully."

    @staticmethod
    def _settings(project_id: int) -> Dict[str, int]:
        from services.project_service import ProjectService

        return ProjectService.get_chapter_settings(project_id)

    @staticmethod
    def _target_words(project_id: Optional[int] = None) -> int:
        if project_id is not None:
            return OutlineService._settings(project_id)["target_words"]
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM application_settings WHERE key = 'chapter_target_words'"
                ).fetchone()
                return int(row["value"]) if row else 2200
        except Exception:
            return 2200

    @staticmethod
    def get_chapters(project_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM chapters WHERE project_id = ? ORDER BY chapter_number ASC", (project_id,))
            rows = cur.fetchall()
            chapters = []
            for r in rows:
                ch = dict(r)
                ch["learning_objectives"] = json.loads(ch["learning_objectives_json"]) if ch.get("learning_objectives_json") else []
                ch["key_topics"] = json.loads(ch["key_topics_json"]) if ch.get("key_topics_json") else []
                ch["research_references"] = json.loads(ch["research_references_json"]) if ch.get("research_references_json") else []
                if ch.get("validation_json"):
                    try:
                        ch["validation"] = json.loads(ch["validation_json"])
                    except Exception:
                        ch["validation"] = None
                chapters.append(ch)
            return chapters

    @staticmethod
    def update_chapter_meta(chapter_id: int, title: str, purpose: str) -> bool:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE chapters SET title = ?, purpose = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (title, purpose, chapter_id),
            )
            return True
