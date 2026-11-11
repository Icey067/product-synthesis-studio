from typing import Dict, Any, List
from database.db import get_connection, transaction

class AnalyticsService:
    @staticmethod
    def record_metric(
        project_id: int,
        record_date: str,
        visits: int,
        sales: int,
        revenue: float,
        customer_questions: int = 0,
        refunds: int = 0,
        feedback_notes: str = "",
    ) -> int:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO analytics_records (
                    project_id, record_date, visits, sales, revenue,
                    customer_questions, refunds, feedback_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    record_date,
                    visits,
                    sales,
                    revenue,
                    customer_questions,
                    refunds,
                    feedback_notes,
                ),
            )
            return cur.lastrowid

    @staticmethod
    def get_metrics(project_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM analytics_records WHERE project_id = ? ORDER BY record_date ASC", (project_id,))
            return [dict(row) for row in cur.fetchall()]

