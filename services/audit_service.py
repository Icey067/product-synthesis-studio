import json
from typing import Dict, Any, Optional
from database.db import get_connection, transaction
from services.ollama_client import OllamaClient
from services.product_format_service import contract_text
from services.research_service import ResearchService
from services.verification_service import VerificationService
from services.editorial_service import EditorialService
from config.prompts_p0 import PROMPT_AUDIT_ASSESSMENT
from models.schemas import QualityAuditReport
from utils.security import wrap_untrusted_content

class AuditService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()
        self.editorial = EditorialService(ollama_client=self.ai)

    @staticmethod
    def _project_format(project_id: int) -> str:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT product_format FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            return row["product_format"] if row else "Ebook"

    @staticmethod
    def _transformation_map(project_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM transformation_maps WHERE project_id = ?", (project_id,)
            ).fetchone()
            if not row:
                return None
            return {
                "before_situation": json.loads(row["before_json"]),
                "after_situation": json.loads(row["after_json"]),
                "milestones": json.loads(row["milestones_json"]),
            }

    def audit_chapter(
        self,
        project_id: int,
        chapter: Dict[str, Any],
        charter: Dict[str, Any],
    ) -> tuple[bool, Optional[Dict[str, Any]], str]:
        content = (chapter.get("content") or "").strip()
        if len(content) < 100:
            return False, None, "Chapter has insufficient content for a quality audit. Generate or write chapter content first."

        product_format = charter.get("product_format") or self._project_format(project_id)
        sources = ResearchService.get_sources(project_id)
        transformation = self._transformation_map(project_id)

        deterministic = VerificationService.deterministic_checks(
            project_id, chapter, charter, sources, product_format=product_format
        )
        structure = deterministic["structure"]

        project_stub = {
            "name": charter.get("product_title", ""),
            "target_customer": charter.get("target_audience", ""),
            "creator_interests": chapter.get("title", ""),
            "product_category": charter.get("product_category", ""),
            "country": charter.get("country", ""),
        }
        evidence_context, _ = ResearchService.build_evidence_context(
            project_stub, sources,
            query_extra=f"{chapter.get('title', '')} {charter.get('customer_problem', '')}",
            limit=10, max_chars_per_source=700,
        )
        deterministic_summary = json.dumps(
            {
                "score": deterministic["score"],
                "checks": deterministic["checks"],
                "issues": deterministic["issues"],
                "claim_summary": deterministic.get("claim_summary", {}),
                "repetition": deterministic.get("repetition", {}),
                "architecture_overlaps": deterministic.get("architecture_overlaps", []),
                "word_count": structure["word_count"],
                "target_words": structure["target_words"],
                "missing_sections": structure["missing_sections"],
                "citations": structure["citation_ids"],
                "invalid_citations": structure["invalid_citations"],
            },
            indent=1,
        )

        prompt = PROMPT_AUDIT_ASSESSMENT.format(
            chapter_number=chapter.get("chapter_number", 1),
            chapter_title=chapter.get("title", ""),
            chapter_content=wrap_untrusted_content(content, label="CHAPTER_CONTENT"),
            book_title=charter.get("product_title", ""),
            core_promise=charter.get("core_promise", ""),
            target_audience=charter.get("target_audience", ""),
            product_format=product_format,
            target_words=structure["target_words"],
            actual_words=structure["word_count"],
            evidence_context=evidence_context,
            required_structure=(
                "Introduction; Step-by-Step Implementation; Practical Example; "
                "Common Mistakes & High-Cost Traps; Worksheet / Exercise; "
                "Chapter Summary & 24-Hour Action Step"
            ),
            deterministic_checks=deterministic_summary,
        )

        model_ok, validated, model_msg = self.ai.generate_structured(
            prompt=prompt,
            schema=QualityAuditReport,
            temperature=0.2,
            max_retries=1,
            task="quality_review",
        )

        model = validated.model_dump() if (model_ok and validated is not None) else {}
        if not model:
            model = {
                "status": "NEEDS_IMPROVEMENT",
                "score": deterministic["score"],
                "factual_accuracy_assessment": "Model-assisted review unavailable; deterministic checks are authoritative.",
                "unsupported_statements": [],
                "repetition_issues": [],
                "readability_score": "Not assessed (model unavailable)",
                "practical_value_assessment": "Not assessed (model unavailable)",
                "recommended_corrections": [],
                "disclaimer_flags": [],
                "summary_verdict": "Deterministic verification completed without model assistance.",
            }

        model_corrections = [str(x) for x in model.get("recommended_corrections", [])]
        model_unsupported = [str(x) for x in model.get("unsupported_statements", [])]
        model_repetition = [str(x) for x in model.get("repetition_issues", [])]
        model_requires_human = (
            str(model.get("status", "")).upper() == "REQUIRES_HUMAN_VERIFICATION"
            or any(c.upper().startswith("HUMAN_REVIEW:") for c in model_corrections)
        )

        status, score, human_review = VerificationService.final_status(
            deterministic, model_requires_human=model_requires_human
        )

        # Advisory editorial rubric.  Deterministic proxies are authoritative;
        # model dimensions are stored separately and never change the status.
        try:
            editorial = self.editorial.assess(
                chapter, charter, deterministic, sources, include_model=bool(model_ok)
            )
        except Exception as exc:
            editorial = {
                "dimensions": {},
                "deterministic_rubric": {},
                "deterministic_average": 0.0,
                "advisory": {"available": False, "message": f"Editorial review failed: {exc}"},
                "advisory_available": False,
                "advisory_note": "Editorial rubric unavailable.",
                "recommendations": [],
                "status": "NOT_REVIEWED",
            }
        editorial_status = (
            "NEEDS_REVISION" if editorial.get("status") == "NEEDS_REVISION" else "REVIEWED"
        )

        issue_messages = [i["message"] for i in deterministic["issues"]]
        unsupported = [
            i["message"] for i in deterministic["issues"]
            if i["check"] in {
                "citation_exists", "citation_required", "unsupported_statistic",
                "statistic_not_in_source", "statistic_context_mismatch",
                "claim_source_relevance", "weak_claim_support", "unsupported_claim",
                "claim_human_review",
            }
        ] + model_unsupported

        corrections = [i["correction"] for i in deterministic["issues"] if i.get("correction")] + model_corrections
        if not corrections:
            corrections = ["No corrections required by deterministic checks."]

        technical_checks = deterministic["checks"]
        check_lines = "; ".join(f"{k}: {'PASS' if v else 'FAIL'}" for k, v in technical_checks.items())
        factual_assessment = (
            "Deterministic verification (authoritative): " + check_lines + ". "
            + "Model-assisted impression (not verification): " + str(model.get("factual_accuracy_assessment", ""))
        )

        data = {
            "status": status,
            "score": score,
            "factual_accuracy_assessment": factual_assessment,
            "unsupported_statements": unsupported,
            "repetition_issues": [
                i["message"] for i in deterministic["issues"]
                if i["check"] in {"repetition", "repetition_threshold"}
            ] + model_repetition,
            "readability_score": str(model.get("readability_score", "Not assessed")),
            "practical_value_assessment": str(model.get("practical_value_assessment", "Not assessed")),
            "recommended_corrections": corrections,
            "disclaimer_flags": [str(x) for x in model.get("disclaimer_flags", [])],
            "summary_verdict": (
                f"{status} — deterministic score {deterministic['score']}/100; final score {score}/100. "
                + str(model.get("summary_verdict", ""))
            ).strip(),
            "deterministic_checks": {
                **technical_checks,
                "structure": structure,
                "issues": deterministic["issues"],
                "counts": deterministic["counts"],
                "raw_score": deterministic["score"],
                "claim_summary": deterministic.get("claim_summary", {}),
                "repetition": deterministic.get("repetition", {}),
                "architecture_overlaps": deterministic.get("architecture_overlaps", []),
            },
            "issues": deterministic["issues"],
            "human_review_required": human_review,
            "model_assessment_available": bool(model_ok),
            "claim_summary": deterministic.get("claim_summary", {}),
            "claims": deterministic.get("claims", []),
            "repetition_summary": deterministic.get("repetition", {}),
            "repetition_findings": deterministic.get("repetition_findings", []),
            "architecture_summary": {
                "ok": bool(deterministic.get("architecture", {}).get("ok", True)),
                "overlap_count": int(deterministic.get("architecture", {}).get("overlap_count") or 0),
                "overlaps": deterministic.get("architecture_overlaps", []),
            },
            "gate_status": (
                "PASS" if status == "PASS"
                else ("HUMAN_REVIEW" if human_review else "BLOCKED")
            ),
            "editorial_rubric": {
                **(editorial.get("dimensions", {}) or {}),
                **(editorial.get("publication_quality_dimensions", {}) or {}),
            },
            "editorial_deterministic": editorial.get("deterministic_rubric", {}),
            "editorial_advisory": editorial.get("advisory", {}),
            "editorial_advisory_available": bool(editorial.get("advisory_available")),
            "editorial_note": editorial.get("advisory_note", ""),
            "editorial_status": editorial_status,
            "recommendations": editorial.get("recommendations", []),
        }

        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO quality_reports (
                    project_id, chapter_id, status, score, factual_accuracy_assessment,
                    unsupported_statements_json, repetition_issues_json, readability_score,
                    practical_value_assessment, recommended_corrections_json, disclaimer_flags_json,
                    summary_verdict, deterministic_checks_json, issues_json, human_review_required,
                    editorial_rubric_json, editorial_advisory, recommendations_json,
                    claim_summary_json, repetition_summary_json, architecture_summary_json, gate_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    chapter["id"],
                    data["status"],
                    data["score"],
                    data["factual_accuracy_assessment"],
                    json.dumps(data["unsupported_statements"]),
                    json.dumps(data["repetition_issues"]),
                    data["readability_score"],
                    data["practical_value_assessment"],
                    json.dumps(data["recommended_corrections"]),
                    json.dumps(data["disclaimer_flags"]),
                    data["summary_verdict"],
                    json.dumps(data["deterministic_checks"]),
                    json.dumps(data["issues"]),
                    1 if data["human_review_required"] else 0,
                    json.dumps(data["editorial_rubric"]),
                    1 if data["editorial_advisory_available"] else 0,
                    json.dumps(data["recommendations"]),
                    json.dumps(data["claim_summary"]),
                    json.dumps(data["repetition_summary"]),
                    json.dumps(data["architecture_summary"]),
                    data["gate_status"],
                ),
            )
            report_id = cur.lastrowid
            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (
                    project_id, "QUALITY_AUDIT_RUN",
                    f"Audited Chapter {chapter['chapter_number']} (Score: {data['score']}/100, Status: {data['status']})",
                ),
            )
            # Persist editorial recommendations for explicit human review.
            cur.execute(
                """
                UPDATE chapters
                SET editorial_status = ?, structural_complete = ?, evidence_complete = ?,
                    actual_word_count = ?, lifecycle_status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    editorial_status,
                    1 if not structure.get("missing_sections") else 0,
                    1 if structure.get("citation_ids") and not structure.get("invalid_citations") else 0,
                    int(structure.get("word_count") or 0),
                    (
                        "APPROVED" if status == "PASS"
                        else "HUMAN_REVIEW_REQUIRED" if human_review
                        else "NEEDS_REVISION"
                    ),
                    chapter["id"],
                ),
            )

        try:
            EditorialService.save_recommendations(
                project_id,
                chapter.get("id"),
                report_id,
                data["recommendations"],
            )
        except Exception:
            # A recommendation write failure must never discard a completed audit.
            pass

        if not model_ok:
            return True, data, f"Deterministic audit completed; model assessment unavailable ({model_msg})."
        return True, data, "Audit report generated."

    @staticmethod
    def get_latest_audit(chapter_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM quality_reports WHERE chapter_id = ? ORDER BY id DESC LIMIT 1",
                (chapter_id,),
            )
            row = cur.fetchone()
            if row:
                d = dict(row)
                d["unsupported_statements"] = json.loads(d["unsupported_statements_json"]) if d.get("unsupported_statements_json") else []
                d["repetition_issues"] = json.loads(d["repetition_issues_json"]) if d.get("repetition_issues_json") else []
                d["recommended_corrections"] = json.loads(d["recommended_corrections_json"]) if d.get("recommended_corrections_json") else []
                d["disclaimer_flags"] = json.loads(d["disclaimer_flags_json"]) if d.get("disclaimer_flags_json") else []
                d["deterministic_checks"] = json.loads(d["deterministic_checks_json"]) if d.get("deterministic_checks_json") else {}
                d["issues"] = json.loads(d["issues_json"]) if d.get("issues_json") else []
                d["editorial_rubric"] = json.loads(d["editorial_rubric_json"]) if d.get("editorial_rubric_json") else {}
                d["recommendations"] = json.loads(d["recommendations_json"]) if d.get("recommendations_json") else []
                d["claim_summary"] = json.loads(d["claim_summary_json"]) if d.get("claim_summary_json") else {}
                d["repetition_summary"] = json.loads(d["repetition_summary_json"]) if d.get("repetition_summary_json") else {}
                d["architecture_summary"] = json.loads(d["architecture_summary_json"]) if d.get("architecture_summary_json") else {}
                d["gate_status"] = d.get("gate_status") or ""
                d["editorial_advisory_available"] = bool(d.get("editorial_advisory"))
                d["human_review_required"] = bool(d.get("human_review_required"))
                return d
        return None
