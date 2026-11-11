from typing import List, Optional, Dict, Any
from database.db import get_connection, transaction
from models.schemas import ProjectCreate, Project
from config.settings import (
    OUTPUT_DIR,
    DEFAULT_CHAPTER_TARGET_WORDS,
    DEFAULT_MIN_CHAPTER_WORDS,
    DEFAULT_REQUIRED_CHAPTER_COUNT,
)
import json
import shutil
from pathlib import Path


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    )
    return cur.fetchone() is not None


def _table_columns(cur, table: str) -> List[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def _insert_copy(cur, table: str, row: Dict[str, Any], overrides: Dict[str, Any]) -> int:
    """Copy one row, substituting explicit values for foreign keys and the name.

    The column list is read from SQLite rather than hard-coded so a duplicated
    project keeps every current field (including P1 trust/coverage columns) and
    remains forward compatible if more columns are added later.
    """
    values: Dict[str, Any] = {}
    for column in _table_columns(cur, table):
        if column == "id":
            continue
        if column in overrides:
            values[column] = overrides[column]
        else:
            values[column] = row.get(column)
    placeholders = ", ".join("?" for _ in values)
    cur.execute(
        f"INSERT INTO {table} ({', '.join(values.keys())}) VALUES ({placeholders})",
        list(values.values()),
    )
    return cur.lastrowid


def _duplicate_export_file(file_path: Optional[str], new_project_id: int) -> Optional[str]:
    """Physically copy an exported artifact so the duplicate is independent."""
    if not file_path:
        return None
    source = Path(file_path)
    if not source.exists() or not source.is_file():
        return None
    try:
        target_dir = Path(OUTPUT_DIR)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{source.stem}_copy_project{new_project_id}{source.suffix}"
        if target.exists():
            target = target_dir / f"{source.stem}_copy_project{new_project_id}_{source.stat().st_size}{source.suffix}"
        shutil.copy2(source, target)
        return str(target)
    except OSError:
        return None

class ProjectService:
    @staticmethod
    def create_project(data: ProjectCreate) -> int:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO projects (
                    name, country, language, target_customer, expertise_mode,
                    product_category, product_format, budget, creator_interests, status,
                    required_chapter_count, target_chapter_words, min_chapter_words
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    data.name,
                    data.country,
                    data.language,
                    data.target_customer,
                    data.expertise_mode.value,
                    data.product_category,
                    data.product_format.value,
                    data.budget,
                    data.creator_interests,
                    int(data.required_chapter_count),
                    int(data.target_chapter_words),
                    int(data.min_chapter_words),
                ),
            )
            project_id = cur.lastrowid
            # Log activity
            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (project_id, "PROJECT_CREATED", f"Initialized project '{data.name}'"),
            )
            return project_id

    @staticmethod
    def get_project(project_id: int) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    @staticmethod
    def list_projects() -> List[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM projects ORDER BY updated_at DESC")
            return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def update_project(project_id: int, updates: Dict[str, Any]) -> bool:
        if not updates:
            return True
        fields = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [project_id]
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE projects SET {fields}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (project_id, "PROJECT_UPDATED", f"Updated fields: {list(updates.keys())}"),
            )
            return cur.rowcount > 0

    @staticmethod
    def get_chapter_settings(project_id: int) -> Dict[str, int]:
        """Canonical per-project manuscript settings.

        Falls back to the global defaults only when a project predates the
        per-project columns.  Values are normalised so callers never receive an
        impossible minimum (e.g. a minimum larger than the target).
        """
        project = ProjectService.get_project(project_id) or {}
        try:
            target = int(project.get("target_chapter_words") or DEFAULT_CHAPTER_TARGET_WORDS)
        except (TypeError, ValueError):
            target = DEFAULT_CHAPTER_TARGET_WORDS
        try:
            minimum = int(project.get("min_chapter_words") or DEFAULT_MIN_CHAPTER_WORDS)
        except (TypeError, ValueError):
            minimum = DEFAULT_MIN_CHAPTER_WORDS
        try:
            required = int(project.get("required_chapter_count") or DEFAULT_REQUIRED_CHAPTER_COUNT)
        except (TypeError, ValueError):
            required = DEFAULT_REQUIRED_CHAPTER_COUNT

        target = max(200, min(10000, target))
        minimum = max(100, minimum)
        if minimum > target:
            minimum = target
        required = max(1, min(40, required))

        return {
            "target_words": target,
            "min_words": minimum,
            "required_chapters": required,
        }

    @staticmethod
    def update_chapter_settings(
        project_id: int,
        target_words: Optional[int] = None,
        min_words: Optional[int] = None,
        required_chapters: Optional[int] = None,
    ) -> Dict[str, int]:
        """Persist per-project settings; isolated from every other project."""
        current = ProjectService.get_chapter_settings(project_id)
        target = int(target_words if target_words is not None else current["target_words"])
        minimum = int(min_words if min_words is not None else current["min_words"])
        required = int(
            required_chapters if required_chapters is not None else current["required_chapters"]
        )

        target = max(200, min(10000, target))
        minimum = max(100, min(10000, minimum))
        if minimum > target:
            raise ValueError("Minimum words cannot exceed the target word count.")
        required = max(1, min(40, required))

        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE projects
                SET target_chapter_words = ?, min_chapter_words = ?,
                    required_chapter_count = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (target, minimum, required, project_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Project {project_id} does not exist.")
            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (
                    project_id,
                    "MANUSCRIPT_SETTINGS_UPDATED",
                    f"target={target} words, minimum={minimum} words, required chapters={required}",
                ),
            )
        return {"target_words": target, "min_words": minimum, "required_chapters": required}

    @staticmethod
    def duplicate_project(project_id: int) -> Optional[int]:
        """Deep-copy a project and its entire evidence/content graph.

        Every project-scoped table is copied through explicit old->new ID maps so
        the duplicate is independently editable: research batches, sources,
        chunks, findings, opportunities, evidence links, scores, charters,
        transformation maps, outlines, chapters, revisions, autosaves, audits,
        competitors, brands, exports, sales pages, distribution plans, analytics
        and activity logs.

        ``projects.selected_opportunity_id`` is remapped only after the copied
        opportunities receive their new IDs.
        """
        original = ProjectService.get_project(project_id)
        if not original:
            return None

        new_name = f"{original['name']} (Copy)"
        original_row = dict(original)

        with transaction() as conn:
            cur = conn.cursor()

            new_id = _insert_copy(
                cur,
                "projects",
                original_row,
                {
                    "name": new_name,
                    "status": "ACTIVE",
                    "selected_opportunity_id": None,
                },
            )

            # ---- Research batches and sources ---------------------------
            batch_map: Dict[int, int] = {}
            if _table_exists(cur, "research_batches"):
                cur.execute("SELECT * FROM research_batches WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    record = dict(row)
                    batch_map[record["id"]] = _insert_copy(
                        cur, "research_batches", record, {"project_id": new_id}
                    )

            source_map: Dict[int, int] = {}
            if _table_exists(cur, "research_sources"):
                cur.execute("SELECT * FROM research_sources WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    record = dict(row)
                    old_batch = record.get("batch_id")
                    source_map[record["id"]] = _insert_copy(
                        cur,
                        "research_sources",
                        record,
                        {
                            "project_id": new_id,
                            "batch_id": batch_map.get(old_batch) if old_batch else None,
                        },
                    )

            if _table_exists(cur, "research_chunks"):
                cur.execute("SELECT * FROM research_chunks WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    record = dict(row)
                    mapped_source = source_map.get(record.get("source_id"))
                    if mapped_source is None:
                        continue
                    _insert_copy(
                        cur,
                        "research_chunks",
                        record,
                        {"project_id": new_id, "source_id": mapped_source},
                    )

            if _table_exists(cur, "research_findings"):
                cur.execute("SELECT * FROM research_findings WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    record = dict(row)
                    mapped_source = source_map.get(record.get("source_id"))
                    if mapped_source is None:
                        continue
                    _insert_copy(
                        cur,
                        "research_findings",
                        record,
                        {"project_id": new_id, "source_id": mapped_source},
                    )

            # ---- Opportunities, evidence links and scores ---------------
            opportunity_map: Dict[int, int] = {}
            cur.execute("SELECT * FROM opportunities WHERE project_id = ?", (project_id,))
            for row in cur.fetchall():
                record = dict(row)
                opportunity_map[record["id"]] = _insert_copy(
                    cur, "opportunities", record, {"project_id": new_id}
                )

            if opportunity_map:
                placeholders = ", ".join("?" for _ in opportunity_map)
                old_opportunity_ids = list(opportunity_map.keys())

                if _table_exists(cur, "opportunity_evidence"):
                    cur.execute(
                        f"SELECT * FROM opportunity_evidence WHERE opportunity_id IN ({placeholders})",
                        old_opportunity_ids,
                    )
                    for row in cur.fetchall():
                        record = dict(row)
                        mapped_opportunity = opportunity_map.get(record.get("opportunity_id"))
                        if mapped_opportunity is None:
                            continue
                        _insert_copy(
                            cur,
                            "opportunity_evidence",
                            record,
                            {
                                "opportunity_id": mapped_opportunity,
                                "source_id": source_map.get(record.get("source_id")),
                            },
                        )

                if _table_exists(cur, "opportunity_scores"):
                    cur.execute(
                        f"SELECT * FROM opportunity_scores WHERE opportunity_id IN ({placeholders})",
                        old_opportunity_ids,
                    )
                    for row in cur.fetchall():
                        record = dict(row)
                        mapped_opportunity = opportunity_map.get(record.get("opportunity_id"))
                        if mapped_opportunity is None:
                            continue
                        _insert_copy(
                            cur,
                            "opportunity_scores",
                            record,
                            {"opportunity_id": mapped_opportunity},
                        )

            # ---- Simple project-scoped artifacts ------------------------
            for table in (
                "competitors",
                "transformation_maps",
                "product_charters",
                "product_outlines",
                "brands",
                "sales_pages",
                "distribution_plans",
                "analytics_records",
            ):
                if not _table_exists(cur, table):
                    continue
                cur.execute(f"SELECT * FROM {table} WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    _insert_copy(cur, table, dict(row), {"project_id": new_id})

            # ---- Chapters, revisions, autosaves and audits --------------
            chapter_map: Dict[int, int] = {}
            if _table_exists(cur, "chapters"):
                cur.execute("SELECT * FROM chapters WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    record = dict(row)
                    chapter_map[record["id"]] = _insert_copy(
                        cur, "chapters", record, {"project_id": new_id}
                    )

            if chapter_map:
                chapter_placeholders = ", ".join("?" for _ in chapter_map)
                old_chapter_ids = list(chapter_map.keys())

                for table in ("chapter_plans", "chapter_sections"):
                    if not _table_exists(cur, table):
                        continue
                    cur.execute(
                        f"SELECT * FROM {table} WHERE chapter_id IN ({chapter_placeholders})",
                        old_chapter_ids,
                    )
                    for row in cur.fetchall():
                        record = dict(row)
                        mapped_chapter = chapter_map.get(record.get("chapter_id"))
                        if mapped_chapter is None:
                            continue
                        _insert_copy(
                            cur,
                            table,
                            record,
                            {"chapter_id": mapped_chapter, "project_id": new_id},
                        )

                if _table_exists(cur, "chapter_sources"):
                    cur.execute(
                        f"SELECT * FROM chapter_sources WHERE chapter_id IN ({chapter_placeholders})",
                        old_chapter_ids,
                    )
                    for row in cur.fetchall():
                        record = dict(row)
                        mapped_chapter = chapter_map.get(record.get("chapter_id"))
                        if mapped_chapter is None:
                            continue
                        _insert_copy(
                            cur,
                            "chapter_sources",
                            record,
                            {
                                "chapter_id": mapped_chapter,
                                "project_id": new_id,
                                "source_id": source_map.get(record.get("source_id")),
                            },
                        )

                if _table_exists(cur, "chapter_revisions"):
                    cur.execute(
                        f"SELECT * FROM chapter_revisions WHERE chapter_id IN ({chapter_placeholders})",
                        old_chapter_ids,
                    )
                    for row in cur.fetchall():
                        record = dict(row)
                        mapped_chapter = chapter_map.get(record.get("chapter_id"))
                        if mapped_chapter is None:
                            continue
                        _insert_copy(
                            cur,
                            "chapter_revisions",
                            record,
                            {"chapter_id": mapped_chapter},
                        )

                if _table_exists(cur, "chapter_autosaves"):
                    cur.execute(
                        f"SELECT * FROM chapter_autosaves WHERE chapter_id IN ({chapter_placeholders})",
                        old_chapter_ids,
                    )
                    for row in cur.fetchall():
                        record = dict(row)
                        mapped_chapter = chapter_map.get(record.get("chapter_id"))
                        if mapped_chapter is None:
                            continue
                        _insert_copy(
                            cur,
                            "chapter_autosaves",
                            record,
                            {"chapter_id": mapped_chapter, "project_id": new_id},
                        )

            report_map: Dict[int, int] = {}
            if _table_exists(cur, "quality_reports"):
                cur.execute("SELECT * FROM quality_reports WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    record = dict(row)
                    old_chapter = record.get("chapter_id")
                    report_map[record["id"]] = _insert_copy(
                        cur,
                        "quality_reports",
                        record,
                        {
                            "project_id": new_id,
                            "chapter_id": chapter_map.get(old_chapter) if old_chapter else None,
                        },
                    )

            if _table_exists(cur, "editorial_recommendations"):
                cur.execute(
                    "SELECT * FROM editorial_recommendations WHERE project_id = ?", (project_id,)
                )
                for row in cur.fetchall():
                    record = dict(row)
                    old_chapter = record.get("chapter_id")
                    old_report = record.get("report_id")
                    _insert_copy(
                        cur,
                        "editorial_recommendations",
                        record,
                        {
                            "project_id": new_id,
                            "chapter_id": chapter_map.get(old_chapter) if old_chapter else None,
                            "report_id": report_map.get(old_report) if old_report else None,
                        },
                    )

            if _table_exists(cur, "batch_sessions"):
                cur.execute("SELECT * FROM batch_sessions WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    _insert_copy(
                        cur,
                        "batch_sessions",
                        dict(row),
                        {"project_id": new_id, "status": "PAUSED"},
                    )

            if _table_exists(cur, "model_evaluations"):
                cur.execute("SELECT * FROM model_evaluations WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    _insert_copy(cur, "model_evaluations", dict(row), {"project_id": new_id})

            # ---- Exports (rows + physical artifacts) --------------------
            if _table_exists(cur, "exports"):
                cur.execute("SELECT * FROM exports WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    record = dict(row)
                    overrides: Dict[str, Any] = {"project_id": new_id}
                    copied_path = _duplicate_export_file(record.get("file_path"), new_id)
                    if copied_path:
                        overrides["file_path"] = copied_path
                    _insert_copy(cur, "exports", record, overrides)

            # ---- Activity log (duplicate's own history) -----------------
            if _table_exists(cur, "activity_logs"):
                cur.execute("SELECT * FROM activity_logs WHERE project_id = ?", (project_id,))
                for row in cur.fetchall():
                    _insert_copy(cur, "activity_logs", dict(row), {"project_id": new_id})

            # Remap the project's selected opportunity only once the new
            # opportunity IDs are known.
            selected = original_row.get("selected_opportunity_id")
            if selected and selected in opportunity_map:
                cur.execute(
                    "UPDATE projects SET selected_opportunity_id = ? WHERE id = ?",
                    (opportunity_map[selected], new_id),
                )

            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (
                    new_id,
                    "PROJECT_DUPLICATED",
                    f"Deep-duplicated from project {project_id} "
                    f"(sources={len(source_map)}, opportunities={len(opportunity_map)}, "
                    f"chapters={len(chapter_map)})",
                ),
            )
            return new_id

    @staticmethod
    def delete_project(project_id: int) -> bool:
        if ProjectService.get_project(project_id) is None:
            return False
        # Destructive action: keep a verified snapshot so a mistaken deletion can
        # be recovered from Settings > Backup & Recovery.
        try:
            from services.backup_service import create_backup

            create_backup("pre_delete")
        except Exception:
            # A failed safety backup must not block an otherwise valid delete,
            # but it is logged by the backup service.
            pass
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cur.rowcount > 0

    @staticmethod
    def get_dashboard_metrics() -> Dict[str, Any]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM projects")
            total_projects = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM projects WHERE status = 'ACTIVE'")
            active_projects = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM exports WHERE export_type = 'PDF'")
            completed_products = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM research_sources")
            total_sources = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM opportunities")
            total_opportunities = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM chapters WHERE content IS NOT NULL AND length(content) > 100")
            total_chapters = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM exports")
            total_exports = cur.fetchone()[0]

            cur.execute("SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT 8")
            recent_activities = [dict(row) for row in cur.fetchall()]

            return {
                "total_projects": total_projects,
                "active_projects": active_projects,
                "completed_products": completed_products,
                "total_sources": total_sources,
                "total_opportunities": total_opportunities,
                "total_chapters": total_chapters,
                "total_exports": total_exports,
                "recent_activities": recent_activities,
            }

