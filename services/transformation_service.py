import json
from typing import Dict, Any, Optional
from database.db import get_connection, transaction
from services.ollama_client import OllamaClient
from services.product_format_service import contract_text, validate_transformation_map
from services.generation_utils import generate_with_validation
from config.prompts import PROMPT_TRANSFORMATION_MAP
from models.schemas import TransformationMap

class TransformationService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    @staticmethod
    def _project_format(project_id: int) -> str:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT product_format FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            return row["product_format"] if row else "Ebook"

    def generate_transformation_map(
        self,
        project_id: int,
        product_title: str,
        customer: str,
        problem: str,
        target_outcome: str,
    ) -> tuple[bool, Optional[Dict[str, Any]], str]:
        product_format = self._project_format(project_id)
        prompt = PROMPT_TRANSFORMATION_MAP.format(
            product_title=product_title,
            customer=customer,
            problem=problem,
            target_outcome=target_outcome,
            product_format=product_format,
            format_contract=contract_text(product_format),
        )

        ok, validated, msg, issues = generate_with_validation(
            self.ai,
            prompt,
            TransformationMap,
            validate_transformation_map,
            product_format,
            stage="Transformation Map",
            temperature=0.5,
            max_attempts=2,
            task="customer_transformation",
        )
        if not ok or validated is None:
            return False, None, msg

        data = validated.model_dump()
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO transformation_maps (project_id, before_json, after_json, milestones_json, product_format, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_id) DO UPDATE SET
                    before_json = excluded.before_json,
                    after_json = excluded.after_json,
                    milestones_json = excluded.milestones_json,
                    product_format = excluded.product_format,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project_id,
                    json.dumps(data["before_situation"]),
                    json.dumps(data["after_situation"]),
                    json.dumps(data["milestones"]),
                    product_format,
                ),
            )
            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (project_id, "TRANSFORMATION_MAP_SAVED", f"Generated {product_format} transformation map"),
            )
        return True, data, "Transformation Map generated successfully."

    @staticmethod
    def get_map(project_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM transformation_maps WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
            if row:
                return {
                    "before_situation": json.loads(row["before_json"]),
                    "after_situation": json.loads(row["after_json"]),
                    "milestones": json.loads(row["milestones_json"]),
                    "product_format": row["product_format"] if "product_format" in row.keys() else "Ebook",
                    "updated_at": row["updated_at"],
                }
        return None

    @staticmethod
    def save_map(project_id: int, map_data: Dict[str, Any]) -> bool:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO transformation_maps (project_id, before_json, after_json, milestones_json, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_id) DO UPDATE SET
                    before_json = excluded.before_json,
                    after_json = excluded.after_json,
                    milestones_json = excluded.milestones_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project_id,
                    json.dumps(map_data.get("before_situation", {})),
                    json.dumps(map_data.get("after_situation", {})),
                    json.dumps(map_data.get("milestones", [])),
                ),
            )
            return True
