import json
from typing import Dict, Any, Optional
from database.db import get_connection, transaction
from services.ollama_client import OllamaClient
from config.prompts import PROMPT_SALES_OFFER
from models.schemas import SalesOfferPackage

class SalesService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    def generate_sales_package(
        self,
        project_id: int,
        charter: Dict[str, Any],
        chapters: list[Dict[str, Any]],
        price_suggestion: str = "$27 - $47",
    ) -> tuple[bool, Optional[Dict[str, Any]], str]:
        chap_overview = ", ".join([f"Ch {c['chapter_number']}: {c['title']}" for c in chapters[:6]])
        deliverables_str = ", ".join(charter.get("deliverables", []))

        prompt = PROMPT_SALES_OFFER.format(
            product_title=charter.get("product_title", ""),
            subtitle=charter.get("subtitle", ""),
            target_audience=charter.get("target_audience", ""),
            problem=charter.get("customer_problem", ""),
            promise=charter.get("core_promise", ""),
            chapters_overview=chap_overview,
            deliverables=deliverables_str,
            price_suggestion=price_suggestion,
        )

        success, validated, msg = self.ai.generate_structured(
            prompt=prompt,
            schema=SalesOfferPackage,
            temperature=0.6,
        )
        if not success or not validated:
            return False, None, f"Sales package generation failed: {msg}"

        data = validated.model_dump()
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO sales_pages (
                    project_id, headline, subheadline, problem_statement, pain_points_json,
                    solution_introduction, core_benefits_json, whats_included_json,
                    chapter_teasers_json, unique_differentiation, faqs_json, call_to_action,
                    price_testing_hypotheses_json, ethical_refund_policy,
                    marketplace_short_description, promotional_social_posts_json,
                    email_announcement_draft, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_id) DO UPDATE SET
                    headline = excluded.headline,
                    subheadline = excluded.subheadline,
                    problem_statement = excluded.problem_statement,
                    pain_points_json = excluded.pain_points_json,
                    solution_introduction = excluded.solution_introduction,
                    core_benefits_json = excluded.core_benefits_json,
                    whats_included_json = excluded.whats_included_json,
                    chapter_teasers_json = excluded.chapter_teasers_json,
                    unique_differentiation = excluded.unique_differentiation,
                    faqs_json = excluded.faqs_json,
                    call_to_action = excluded.call_to_action,
                    price_testing_hypotheses_json = excluded.price_testing_hypotheses_json,
                    ethical_refund_policy = excluded.ethical_refund_policy,
                    marketplace_short_description = excluded.marketplace_short_description,
                    promotional_social_posts_json = excluded.promotional_social_posts_json,
                    email_announcement_draft = excluded.email_announcement_draft,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project_id,
                    data["headline"],
                    data["subheadline"],
                    data["problem_statement"],
                    json.dumps(data["pain_points"]),
                    data["solution_introduction"],
                    json.dumps(data["core_benefits"]),
                    json.dumps(data["whats_included"]),
                    json.dumps(data["chapter_teasers"]),
                    data["unique_differentiation"],
                    json.dumps(data["faqs"]),
                    data["call_to_action"],
                    json.dumps(data["price_testing_hypotheses"]),
                    data["ethical_refund_policy"],
                    data["marketplace_short_description"],
                    json.dumps(data["promotional_social_posts"]),
                    data["email_announcement_draft"],
                ),
            )
        return True, data, "Sales offer package generated."

    @staticmethod
    def get_sales_page(project_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sales_pages WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
            if row:
                d = dict(row)
                d["pain_points"] = json.loads(d["pain_points_json"]) if d.get("pain_points_json") else []
                d["core_benefits"] = json.loads(d["core_benefits_json"]) if d.get("core_benefits_json") else []
                d["whats_included"] = json.loads(d["whats_included_json"]) if d.get("whats_included_json") else []
                d["chapter_teasers"] = json.loads(d["chapter_teasers_json"]) if d.get("chapter_teasers_json") else []
                d["faqs"] = json.loads(d["faqs_json"]) if d.get("faqs_json") else []
                d["price_testing_hypotheses"] = json.loads(d["price_testing_hypotheses_json"]) if d.get("price_testing_hypotheses_json") else []
                d["promotional_social_posts"] = json.loads(d["promotional_social_posts_json"]) if d.get("promotional_social_posts_json") else []
                return d
        return None

