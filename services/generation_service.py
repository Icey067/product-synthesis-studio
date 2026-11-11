import json
import re
from typing import Dict, Any, List, Optional, Callable
from database.db import get_connection, transaction
from services.ollama_client import OllamaClient
from services.product_format_service import contract_text
from services.research_service import ResearchService
from services.chapter_contract import (
    analyze_chapter,
    count_words,
    deficiencies_for_expansion,
    save_validation,
    target_words as get_target_words,
    min_publication_words,
)
from config.prompts import PROMPT_CHAPTER_GENERATION
from config.prompts_p0 import PROMPT_CHAPTER_EXPANSION
from utils.security import escape_untrusted_text, wrap_untrusted_content

REQUIRED_STRUCTURE = """1. ## Introduction
2. ## Step-by-Step Implementation
3. ## Practical Example
4. ## Common Mistakes & High-Cost Traps
5. ## Worksheet / Exercise
6. ## Chapter Summary & 24-Hour Action Step"""


class GenerationService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    @staticmethod
    def _project_format(chapter: Dict[str, Any]) -> str:
        project_id = chapter.get("project_id")
        if not project_id:
            return "Ebook"
        with get_connection() as conn:
            row = conn.execute(
                "SELECT product_format FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            return row["product_format"] if row else "Ebook"

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str) and value:
            try:
                parsed = json.loads(value)
                return [str(v) for v in parsed] if isinstance(parsed, list) else [value]
            except Exception:
                return [value]
        return []

    def _build_prompt(
        self,
        chapter: Dict[str, Any],
        charter: Dict[str, Any],
        product_format: str,
        target: int,
        evidence_context: str,
    ) -> str:
        learning_objs = ", ".join(self._as_list(chapter.get("learning_objectives")))
        key_topics = ", ".join(self._as_list(chapter.get("key_topics")))
        return PROMPT_CHAPTER_GENERATION.format(
            chapter_number=chapter["chapter_number"],
            chapter_title=chapter["title"],
            book_title=charter.get("product_title", "Digital Playbook"),
            book_subtitle=charter.get("subtitle", ""),
            core_promise=charter.get("core_promise", ""),
            target_audience=charter.get("target_audience", ""),
            chapter_purpose=chapter.get("purpose", ""),
            learning_objectives=learning_objs,
            key_topics=key_topics,
            evidence_context=evidence_context,
            product_format=product_format,
            format_contract=contract_text(product_format),
            target_words=target,
            min_words=int(target * 0.85),
        )

    def generate_chapter_validated(
        self,
        chapter: Dict[str, Any],
        charter: Dict[str, Any],
        evidence_sources: List[Dict[str, Any]],
        custom_instructions: str = "",
        product_format: Optional[str] = None,
        target: Optional[int] = None,
        max_expansions: int = 2,
    ) -> tuple[bool, str, Optional[Dict[str, Any]], str]:
        """
        Generate a chapter and validate its real word count, sections, citations
        and format compliance. Deficient sections are expanded, never faked.
        """
        if chapter.get("is_locked"):
            return False, "", None, "Chapter is locked. Unlock it in the interface before regenerating."

        project_id = chapter.get("project_id")
        settings = None
        if project_id:
            from services.project_service import ProjectService

            settings = ProjectService.get_chapter_settings(project_id)

        product_format = product_format or chapter.get("product_format") or self._project_format(chapter)
        target = int(
            target
            or chapter.get("target_word_count")
            or (settings["target_words"] if settings else get_target_words())
        )
        project_min = settings["min_words"] if settings else None
        source_ids = [int(s["id"]) for s in evidence_sources]
        source_ids_set = set(source_ids)

        query = " ".join([
            str(chapter.get("title", "")),
            str(chapter.get("purpose", "")),
            " ".join(self._as_list(chapter.get("key_topics"))),
        ])
        project_stub = {
            "name": charter.get("product_title", ""),
            "target_customer": charter.get("target_audience", ""),
            "creator_interests": " ".join(self._as_list(chapter.get("key_topics"))),
            "product_category": charter.get("product_category", ""),
            "country": charter.get("country", ""),
        }
        selected = ResearchService.select_relevant_sources(
            project_stub, evidence_sources, query_extra=query, limit=8
        )
        from services.retrieval_service import RetrievalService

        retrieval_project_id = chapter.get("project_id")
        if retrieval_project_id:
            evidence_context, retrieval_evidence = RetrievalService.build_context(
                int(retrieval_project_id),
                query,
                sources=evidence_sources,
                limit=8,
                max_chars_per_chunk=1100,
            )
            if chapter.get("id"):
                try:
                    RetrievalService.store_chapter_sources(
                        int(retrieval_project_id), int(chapter["id"]), retrieval_evidence
                    )
                except Exception:
                    pass
        else:
            evidence_context, _ = ResearchService.build_evidence_context(
                project_stub,
                selected,
                query_extra=query,
                limit=8,
                max_chars_per_source=1100,
            )

        prompt = self._build_prompt(chapter, charter, product_format, target, evidence_context)
        if custom_instructions:
            prompt += (
                "\n\nADDITIONAL USER INSTRUCTIONS (trusted operator input):\n"
                + escape_untrusted_text(custom_instructions)
            )

        ok, content = self.ai.generate(
            prompt=prompt, temperature=0.7, timeout=300, task="long_form_writing"
        )
        if not ok:
            return False, "", None, f"Generation error: {content}"

        report = analyze_chapter(
            content,
            target=target,
            min_words=project_min,
            source_ids=source_ids,
            product_format=product_format,
        )

        attempts = 0
        while report["status"] != "COMPLETE" and attempts < max_expansions:
            deficiencies = deficiencies_for_expansion(report)
            expansion_prompt = PROMPT_CHAPTER_EXPANSION.format(
                chapter_number=chapter["chapter_number"],
                chapter_title=chapter["title"],
                actual_words=report["word_count"],
                target_words=target,
                min_words=report["min_words"],
                deficiencies=deficiencies,
                current_content=wrap_untrusted_content(content, label="CURRENT_CHAPTER"),
                format_contract=contract_text(product_format),
            )
            exp_ok, expanded = self.ai.generate(
                prompt=expansion_prompt,
                temperature=0.55,
                timeout=360,
                task="content_editing",
            )
            if not exp_ok:
                break
            # If the model returned only the missing sections, append them
            # rather than replacing good existing prose.
            if len(expanded.split()) < len(content.split()) * 0.65:
                expanded = content.rstrip() + "\n\n" + expanded.strip()
            content = expanded.strip()
            report = analyze_chapter(
                content,
                target=target,
                min_words=project_min,
                source_ids=source_ids,
                product_format=product_format,
            )
            attempts += 1

        # A chapter that explicitly promises software in a book project is
        # rejected outright; other shortfalls are reported honestly.
        if not report["format_ok"]:
            return False, "", report, (
                "Generated content violates the product-format contract: "
                + "; ".join(i["message"] for i in report["issues"] if i["code"].startswith("chapter_software"))
            )

        if report["status"] == "COMPLETE":
            msg = (
                f"Generated and validated {report['word_count']} words "
                f"({report['completion_pct']}% of target) with all required sections."
            )
        else:
            missing = ", ".join(s.replace("_", " ") for s in report["missing_sections"])
            msg = (
                f"Generated {report['word_count']} words (target {target}). "
                f"Still incomplete after {attempts} expansion(s)."
                + (f" Missing: {missing}." if missing else "")
            )

        return True, content, report, msg

    def generate_chapter_content(
        self,
        chapter: Dict[str, Any],
        charter: Dict[str, Any],
        evidence_sources: List[Dict[str, Any]],
        custom_instructions: str = "",
    ) -> tuple[bool, str, str]:
        """Backwards-compatible wrapper returning (ok, content, message)."""
        ok, content, _report, msg = self.generate_chapter_validated(
            chapter, charter, evidence_sources, custom_instructions=custom_instructions
        )
        return ok, content, msg

    @staticmethod
    def save_chapter_content(
        chapter_id: int,
        content: str,
        note: str = "Manual edit",
        validation: Optional[Dict[str, Any]] = None,
        allow_approved_overwrite: bool = False,
    ) -> bool:
        """Save content and a revision without ever silently overwriting a locked
        or independently approved chapter.

        A verified PASS audit is a publication decision.  Overwriting such a
        chapter requires the caller to pass ``allow_approved_overwrite=True``
        after an explicit user confirmation.
        """
        word_count = len(content.split())
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute("SELECT is_locked, content FROM chapters WHERE id = ?", (chapter_id,))
            row = cur.fetchone()
            if row is None:
                return False
            if row["is_locked"]:
                return False
            if not allow_approved_overwrite and (row["content"] or "") != content:
                from services.manuscript_service import ManuscriptService

                if ManuscriptService.chapter_is_approved(chapter_id):
                    return False

            cur.execute("SELECT COUNT(*) FROM chapter_revisions WHERE chapter_id = ?", (chapter_id,))
            rev_num = cur.fetchone()[0] + 1

            if validation is not None:
                cur.execute(
                    """
                    UPDATE chapters
                    SET content = ?, word_count = ?, validation_status = ?, validation_json = ?,
                        target_word_count = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND is_locked = 0
                    """,
                    (
                        content, word_count, validation["status"], json.dumps(validation),
                        validation.get("target_words"), chapter_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE chapters SET content = ?, word_count = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND is_locked = 0
                    """,
                    (content, word_count, chapter_id),
                )
            if cur.rowcount == 0:
                return False

            cur.execute(
                """
                INSERT INTO chapter_revisions (chapter_id, revision_number, content, word_count, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chapter_id, rev_num, content, word_count, note),
            )
            return True

    @staticmethod
    def set_lock_status(chapter_id: int, locked: bool) -> bool:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE chapters SET is_locked = ? WHERE id = ?", (1 if locked else 0, chapter_id))
            return True

    def generate_book_sequentially(
        self,
        project_id: int,
        charter: Dict[str, Any],
        progress_cb: Optional[Callable[[int, int, Dict[str, Any], str], None]] = None,
        custom_instructions: str = "",
        force: bool = False,
    ) -> tuple[bool, List[Dict[str, Any]], str]:
        """Resumable batch generation.

        Chapters that are locked or already meet the contract are skipped, so
        calling this again after an interruption resumes where it stopped.
        """
        from services.outline_service import OutlineService

        from services.project_service import ProjectService

        settings = ProjectService.get_chapter_settings(project_id)
        chapters = OutlineService.get_chapters(project_id)
        sources = ResearchService.get_sources(project_id)
        if not chapters:
            return False, [], "No outline exists. Generate an outline first."

        from services.manuscript_service import ManuscriptService

        results: List[Dict[str, Any]] = []
        produced = 0
        for idx, chapter in enumerate(chapters):
            report = analyze_chapter(
                chapter.get("content", ""),
                target=chapter.get("target_word_count") or settings["target_words"],
                min_words=settings["min_words"],
                source_ids=[int(s["id"]) for s in sources],
                product_format=charter.get("product_format", "Ebook"),
            )
            if chapter.get("is_locked"):
                results.append({"chapter": chapter["chapter_number"], "status": "SKIPPED_LOCKED"})
                if progress_cb:
                    progress_cb(idx + 1, len(chapters), chapter, "Skipped (locked)")
                continue
            if not force and ManuscriptService.chapter_is_approved(chapter["id"]):
                results.append({"chapter": chapter["chapter_number"], "status": "SKIPPED_APPROVED"})
                if progress_cb:
                    progress_cb(idx + 1, len(chapters), chapter, "Skipped (approved publication)")
                continue
            if not force and report["status"] == "COMPLETE":
                results.append({"chapter": chapter["chapter_number"], "status": "ALREADY_COMPLETE"})
                if progress_cb:
                    progress_cb(idx + 1, len(chapters), chapter, "Already complete")
                continue

            ok, content, new_report, msg = self.generate_chapter_validated(
                chapter, charter, sources, custom_instructions=custom_instructions
            )
            if ok and new_report is not None:
                saved = self.save_chapter_content(
                    chapter["id"], content,
                    note="Batch generation (validated)",
                    validation=new_report,
                )
                produced += 1
                results.append({
                    "chapter": chapter["chapter_number"],
                    "status": new_report["status"] if saved else "SAVE_FAILED",
                    "words": new_report["word_count"],
                    "target": new_report["target_words"],
                    "missing_sections": new_report["missing_sections"],
                })
            else:
                results.append({
                    "chapter": chapter["chapter_number"],
                    "status": "FAILED",
                    "message": msg,
                })
            if progress_cb:
                progress_cb(idx + 1, len(chapters), chapter, msg)

        remaining = sum(
            1
            for c in OutlineService.get_chapters(project_id)
            if count_words(c.get("content", "")) < settings["min_words"]
        )
        return True, results, f"Batch generation finished. {produced} chapter(s) processed; {remaining} still below publication length."
