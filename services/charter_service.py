import json
from typing import Dict, Any, Optional
from database.db import get_connection, transaction
from services.ollama_client import OllamaClient
from services.product_format_service import contract_text, validate_charter
from services.generation_utils import generate_with_validation
from config.prompts import PROMPT_PRODUCT_CHARTER
from models.schemas import ProductCharter
from utils.security import wrap_untrusted_content

class CharterService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    def generate_charter(
        self,
        project: Dict[str, Any],
        opportunity: Optional[Dict[str, Any]] = None,
        research_summary: str = "Empirical market signals and customer research notes",
    ) -> tuple[bool, Optional[Dict[str, Any]], str]:
        customer = (opportunity.get("exact_customer") if opportunity else project.get("target_customer")) or "Business Owners"
        problem = (opportunity.get("customer_problem") if opportunity else "Operational and digital bottlenecks")
        outcome = (opportunity.get("desired_outcome") if opportunity else "Measurable business transformation")
        mechanism = (opportunity.get("potential_solution") if opportunity else "Structured 30-day implementation framework")
        product_format = project.get("product_format", "Ebook")

        prompt = PROMPT_PRODUCT_CHARTER.format(
            project_name=project["name"],
            customer=customer,
            problem=problem,
            outcome=outcome,
            country=project.get("country", "United States"),
            research_summary=wrap_untrusted_content(research_summary[:5000], label="RESEARCH_SOURCE"),
            unique_mechanism=mechanism,
            format_contract=contract_text(product_format),
        )

        ok, validated, msg, issues = generate_with_validation(
            self.ai,
            prompt,
            ProductCharter,
            validate_charter,
            product_format,
            stage="Product Charter",
            temperature=0.5,
            max_attempts=2,
            task="product_charter",
        )
        if not ok or validated is None:
            return False, None, msg

        data = validated.model_dump()
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO product_charters (
                    project_id, product_title, subtitle, product_category, target_audience,
                    customer_problem, core_promise, desired_outcome, unique_mechanism,
                    differentiation, product_description, deliverables_json, evidence_base,
                    product_limitations_json, scope_exclusions_json, disclaimer_requirements_json,
                    product_format, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_id) DO UPDATE SET
                    product_title = excluded.product_title,
                    subtitle = excluded.subtitle,
                    product_category = excluded.product_category,
                    target_audience = excluded.target_audience,
                    customer_problem = excluded.customer_problem,
                    core_promise = excluded.core_promise,
                    desired_outcome = excluded.desired_outcome,
                    unique_mechanism = excluded.unique_mechanism,
                    differentiation = excluded.differentiation,
                    product_description = excluded.product_description,
                    deliverables_json = excluded.deliverables_json,
                    evidence_base = excluded.evidence_base,
                    product_limitations_json = excluded.product_limitations_json,
                    scope_exclusions_json = excluded.scope_exclusions_json,
                    disclaimer_requirements_json = excluded.disclaimer_requirements_json,
                    product_format = excluded.product_format,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project["id"],
                    data["product_title"],
                    data["subtitle"],
                    data["product_category"],
                    data["target_audience"],
                    data["customer_problem"],
                    data["core_promise"],
                    data["desired_outcome"],
                    data["unique_mechanism"],
                    data["differentiation"],
                    data["product_description"],
                    json.dumps(data["deliverables"]),
                    data["evidence_base"],
                    json.dumps(data["product_limitations"]),
                    json.dumps(data["scope_exclusions"]),
                    json.dumps(data["disclaimer_requirements"]),
                    product_format,
                ),
            )
            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (project["id"], "CHARTER_SAVED", f"Saved {product_format} charter: '{data['product_title']}'"),
            )

        return True, data, "Product Charter generated and saved."

    @staticmethod
    def get_charter(project_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM product_charters WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
            if row:
                d = dict(row)
                d["deliverables"] = json.loads(d["deliverables_json"])
                d["product_limitations"] = json.loads(d["product_limitations_json"])
                d["scope_exclusions"] = json.loads(d["scope_exclusions_json"])
                d["disclaimer_requirements"] = json.loads(d["disclaimer_requirements_json"])
                return d
        return None

    @staticmethod
    def update_charter(project_id: int, charter_data: Dict[str, Any]) -> bool:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE product_charters SET
                    product_title = ?, subtitle = ?, product_category = ?, target_audience = ?,
                    customer_problem = ?, core_promise = ?, desired_outcome = ?, unique_mechanism = ?,
                    differentiation = ?, product_description = ?, deliverables_json = ?, evidence_base = ?,
                    product_limitations_json = ?, scope_exclusions_json = ?, disclaimer_requirements_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE project_id = ?
                """,
                (
                    charter_data["product_title"],
                    charter_data["subtitle"],
                    charter_data["product_category"],
                    charter_data["target_audience"],
                    charter_data["customer_problem"],
                    charter_data["core_promise"],
                    charter_data["desired_outcome"],
                    charter_data["unique_mechanism"],
                    charter_data["differentiation"],
                    charter_data["product_description"],
                    json.dumps(charter_data.get("deliverables", [])),
                    charter_data.get("evidence_base", ""),
                    json.dumps(charter_data.get("product_limitations", [])),
                    json.dumps(charter_data.get("scope_exclusions", [])),
                    json.dumps(charter_data.get("disclaimer_requirements", [])),
                    project_id,
                ),
            )
            return True
