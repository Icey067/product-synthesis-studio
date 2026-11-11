import json
from typing import Dict, Any, Optional
from database.db import get_connection, transaction
from services.ollama_client import OllamaClient
from config.prompts import PROMPT_BRAND_STUDIO
from models.schemas import BrandIdentity

class BrandingService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    def generate_branding(
        self,
        project_id: int,
        product_title: str,
        target_audience: str,
        category: str,
        preferences: str = "Modern, authoritative, clean aesthetic",
    ) -> tuple[bool, Optional[Dict[str, Any]], str]:
        prompt = PROMPT_BRAND_STUDIO.format(
            product_title=product_title,
            target_audience=target_audience,
            category=category,
            preferences=preferences,
        )

        success, validated, msg = self.ai.generate_structured(
            prompt=prompt,
            schema=BrandIdentity,
            temperature=0.6,
        )
        if not success or not validated:
            return False, None, f"Branding generation failed: {msg}"

        data = validated.model_dump()
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO brands (
                    project_id, brand_name, brand_story, color_palette_json, font_pairing_json,
                    cover_concept, interior_guidelines, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_id) DO UPDATE SET
                    brand_name = excluded.brand_name,
                    brand_story = excluded.brand_story,
                    color_palette_json = excluded.color_palette_json,
                    font_pairing_json = excluded.font_pairing_json,
                    cover_concept = excluded.cover_concept,
                    interior_guidelines = excluded.interior_guidelines,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project_id,
                    data["product_name_options"][0] if data["product_name_options"] else product_title,
                    data["brand_story"],
                    json.dumps(data["color_palettes"]),
                    json.dumps(data["font_pairings"]),
                    data["cover_concept"],
                    data["interior_design_guidelines"],
                ),
            )
        return True, data, "Branding suite generated."

    @staticmethod
    def get_brand(project_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM brands WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
            if row:
                d = dict(row)
                d["color_palettes"] = json.loads(d["color_palette_json"]) if d.get("color_palette_json") else []
                d["font_pairings"] = json.loads(d["font_pairing_json"]) if d.get("font_pairing_json") else []
                return d
        return None

