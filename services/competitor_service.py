from typing import List, Dict, Any, Optional
from database.db import get_connection, transaction

class CompetitorService:
    @staticmethod
    def add_competitor(
        project_id: int,
        product_name: str,
        brand: str = "",
        website: str = "",
        product_format: str = "Ebook",
        observed_price: str = "",
        main_features: str = "",
        customer_reviews: str = "",
        customer_complaints: str = "",
        strengths: str = "",
        weaknesses: str = "",
        evidence_sources: str = "",
    ) -> int:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO competitors (
                    project_id, product_name, brand, website, product_format,
                    observed_price, main_features, customer_reviews, customer_complaints,
                    strengths, weaknesses, evidence_sources
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    product_name,
                    brand,
                    website,
                    product_format,
                    observed_price,
                    main_features,
                    customer_reviews,
                    customer_complaints,
                    strengths,
                    weaknesses,
                    evidence_sources,
                ),
            )
            comp_id = cur.lastrowid
            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (project_id, "COMPETITOR_ADDED", f"Added competitor record for '{product_name}'"),
            )
            return comp_id

    @staticmethod
    def get_competitors(project_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM competitors WHERE project_id = ? ORDER BY id DESC", (project_id,))
            return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def delete_competitor(comp_id: int) -> bool:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM competitors WHERE id = ?", (comp_id,))
            return cur.rowcount > 0

