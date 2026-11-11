from typing import List, Dict, Any, Optional, Iterable
import hashlib
import json
import re
from datetime import datetime

from database.db import get_connection, transaction
from services.source_trust_service import (
    SourceTrustService,
    UNASSESSED,
    assess_source,
    save_assessment,
)
from utils.file_handler import compute_hash
from utils.security import wrap_untrusted_content

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "their",
    "about", "after", "before", "have", "has", "are", "was", "were", "will",
    "can", "could", "should", "would", "not", "but", "you", "our", "its", "how",
    "what", "when", "where", "who", "why", "all", "any", "each", "more", "most",
    "other", "some", "such", "than", "then", "them", "these", "those", "very",
    "just", "also", "only", "over", "under", "between", "through", "during",
    "india", "indian", "united", "states", "new", "using", "use", "used",
}

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]{2,}")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_NUMBER_RE = re.compile(
    r"(?:₹|rs\.?|\$|us\$)\s?\d[\d,.]*|\d[\d,.]*\s?%|\b\d[\d,.]*\s?(?:billion|million|crore|lakh|trillion)\b",
    re.I,
)

DEFAULT_CHUNK_CHARS = 1200
DEFAULT_CHUNK_OVERLAP = 120
DEFAULT_MAX_CHUNKS = 16
DEFAULT_MAX_CONTEXT_CHARS = 14000


def tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return {
        t.lower()
        for t in _TOKEN_RE.findall(text)
        if t.lower() not in _STOPWORDS and len(t) >= 3
    }


def chunk_text(
    text: str,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP,
) -> List[str]:
    """Split source text into overlapping, paragraph-aware chunks.

    Chunks are built from whole sentences where possible so an excerpt remains
    quotable.  A small overlap preserves context across boundaries.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    if len(raw) <= max_chars:
        return [raw]

    sentences = [s.strip() for s in _SENTENCE_RE.split(raw) if s.strip()]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    def _flush():
        nonlocal current, current_len
        if current:
            chunks.append(" ".join(current).strip())
            current = []
            current_len = 0

    for sentence in sentences:
        # Very long sentence: hard-split on words.
        while len(sentence) > max_chars:
            if current:
                _flush()
            chunks.append(sentence[:max_chars].strip())
            sentence = sentence[max_chars - overlap_chars:].strip()
        add_len = len(sentence) + (1 if current else 0)
        if current and current_len + add_len > max_chars:
            _flush()
            # Seed the next chunk with a tail of the previous one.
            if chunks and overlap_chars > 0:
                tail = chunks[-1][-overlap_chars:]
                current = [tail]
                current_len = len(tail)
        current.append(sentence)
        current_len += add_len
    _flush()
    return [c for c in chunks if c]


def _chunk_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class ResearchService:
    @staticmethod
    def add_source(
        project_id: int,
        title: str,
        source_category: str,
        content_text: str,
        publisher: str = "Unknown",
        author: str = "Unknown",
        publication_date: str = "Unknown",
        url: Optional[str] = None,
        reliability_assessment: str = UNASSESSED,
        reliability_rationale: str = "",
        relevance_to_product: str = "",
        batch_id: Optional[int] = None,
        declared_reliability: Optional[str] = None,
    ) -> tuple[bool, Optional[int], str]:
        """Store a research source, independently assess it and index chunks.

        ``reliability_assessment`` is retained for backwards compatibility and is
        treated only as the user's *declaration*.  Credibility comes exclusively
        from the independent assessment stored in ``assessed_reliability``.
        """
        if not content_text or len(content_text.strip()) < 10:
            return False, None, "Source content is empty or too short."

        content_hash = compute_hash(content_text)
        declared = str(declared_reliability or reliability_assessment or UNASSESSED).strip().upper()
        if declared not in {"HIGH", "MEDIUM", "LOW", UNASSESSED}:
            declared = UNASSESSED

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM research_sources WHERE project_id = ? AND content_hash = ?",
                (project_id, content_hash),
            )
            existing = cur.fetchone()
            if existing:
                return False, existing[0], "Duplicate content detected: This source already exists in this project."

        assessment = assess_source(
            {
                "title": title,
                "publisher": publisher,
                "author": author,
                "publication_date": publication_date,
                "url": url or "",
                "source_category": source_category,
                "raw_content": content_text,
                "declared_reliability": declared,
            }
        )

        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO research_sources (
                    project_id, batch_id, title, publisher, author, publication_date,
                    url, content_hash, source_category, raw_content,
                    reliability_assessment, declared_reliability, assessed_reliability,
                    assessment_score, assessment_json, assessed_at,
                    reliability_rationale, relevance_to_product
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    batch_id,
                    title,
                    publisher,
                    author,
                    publication_date,
                    url,
                    content_hash,
                    source_category,
                    content_text,
                    assessment["assessed_reliability"],
                    declared,
                    assessment["assessed_reliability"],
                    assessment["assessment_score"],
                    json.dumps(assessment),
                    assessment["assessed_at"],
                    reliability_rationale or assessment["assessment_rationale"],
                    relevance_to_product,
                ),
            )
            source_id = cur.lastrowid
            ResearchService._index_source_with_conn(conn, project_id, source_id, content_text)

            # Update project research progress
            cur.execute("SELECT COUNT(*) FROM research_sources WHERE project_id = ?", (project_id,))
            cnt = cur.fetchone()[0]
            progress = min(100, cnt * 10)
            cur.execute("UPDATE projects SET research_progress = ? WHERE id = ?", (progress, project_id))

            cur.execute(
                "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                (
                    project_id, "SOURCE_ADDED",
                    f"Added source '{title}' [{source_category}] — declared {declared}, "
                    f"independently assessed {assessment['assessed_reliability']}",
                ),
            )
            return True, source_id, "Source saved successfully."

    # ------------------------------------------------------------------
    # Chunk indexing and retrieval
    # ------------------------------------------------------------------
    @staticmethod
    def _index_source_with_conn(conn, project_id: int, source_id: int, content: str) -> int:
        conn.execute("DELETE FROM research_chunks WHERE source_id = ?", (source_id,))
        chunks = chunk_text(content)
        for idx, chunk in enumerate(chunks):
            conn.execute(
                """
                INSERT INTO research_chunks (
                    project_id, source_id, chunk_index, content, content_hash, token_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, source_id, idx, chunk, _chunk_hash(chunk), len(tokenize(chunk))),
            )
        return len(chunks)

    @staticmethod
    def index_source(source_id: int) -> int:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM research_sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            return 0
        with transaction() as conn:
            return ResearchService._index_source_with_conn(
                conn, int(row["project_id"]), source_id, row["raw_content"] or ""
            )

    @staticmethod
    def reindex_project(project_id: int) -> int:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, raw_content FROM research_sources WHERE project_id = ?", (project_id,)
            ).fetchall()
        total = 0
        with transaction() as conn:
            for row in rows:
                total += ResearchService._index_source_with_conn(
                    conn, project_id, int(row["id"]), row["raw_content"] or ""
                )
        return total

    @staticmethod
    def get_sources(project_id: int, batch_ids: Optional[Iterable[int]] = None) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            cur = conn.cursor()
            params: list[Any] = [project_id]
            sql = "SELECT * FROM research_sources WHERE project_id = ?"
            if batch_ids:
                ids = [int(b) for b in batch_ids]
                placeholders = ",".join("?" for _ in ids)
                sql += f" AND (batch_id IS NULL OR batch_id IN ({placeholders}))"
                params.extend(ids)
            sql += " ORDER BY id ASC"
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def get_batches(project_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM research_batches WHERE project_id = ? ORDER BY id ASC", (project_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Relevance-ranked evidence retrieval
    # ------------------------------------------------------------------
    @classmethod
    def source_relevance_score(cls, query_tokens: set[str], source: Dict[str, Any]) -> float:
        if not query_tokens:
            return 0.0
        title_tokens = tokenize(source.get("title", ""))
        relevance_tokens = tokenize(source.get("relevance_to_product", ""))
        body_tokens = tokenize((source.get("raw_content") or "")[:20000])
        findings_tokens = tokenize(source.get("main_findings", ""))
        stats_tokens = tokenize(source.get("important_statistics", ""))

        score = 0.0
        score += 3.0 * len(query_tokens & title_tokens)
        score += 2.0 * len(query_tokens & relevance_tokens)
        score += 1.0 * len(query_tokens & findings_tokens)
        score += 1.0 * len(query_tokens & stats_tokens)
        score += 0.35 * len(query_tokens & body_tokens)
        # Reliability only orders candidates; it never creates evidence, and it
        # uses the independent assessment rather than the user's declaration.
        reliability = str(source.get("assessed_reliability") or source.get("reliability_assessment", "")).upper()
        if reliability == "HIGH":
            score += 0.05
        return round(score, 4)

    @classmethod
    def select_relevant_sources(
        cls,
        project: Dict[str, Any],
        sources: List[Dict[str, Any]],
        query_extra: str = "",
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        query = " ".join(
            str(project.get(k, ""))
            for k in ("name", "target_customer", "creator_interests", "product_category", "country")
        ) + " " + query_extra
        query_tokens = tokenize(query)
        ranked = sorted(
            sources,
            key=lambda s: cls._source_query_score(query_tokens, s, query),
            reverse=True,
        )
        return ranked[:limit]

    @classmethod
    def _source_query_score(cls, query_tokens: set[str], source: Dict[str, Any], query: str) -> float:
        return cls.source_relevance_score(query_tokens, source)

    @classmethod
    def extract_relevant_excerpt(
        cls,
        source: Dict[str, Any],
        query_tokens: set[str],
        max_chars: int = 1200,
    ) -> str:
        raw = (source.get("raw_content") or "").strip()
        if not raw:
            return ""
        paragraphs = [p.strip() for p in re.split(r"\n{2,}|(?<=[.!?])\s{2,}", raw) if len(p.strip()) >= 80]
        if not paragraphs:
            return raw[:max_chars]

        scored = []
        for idx, para in enumerate(paragraphs):
            tokens = tokenize(para)
            overlap = len(tokens & query_tokens)
            numeric_bonus = 1 if re.search(r"\d", para) else 0
            scored.append((overlap * 2 + numeric_bonus, idx, para))
        scored.sort(key=lambda x: (-x[0], x[1]))

        chosen_idx = sorted({item[1] for item in scored[:3]})
        excerpt = "\n".join(paragraphs[i] for i in chosen_idx)
        if len(excerpt) < 200:
            excerpt = raw[:max_chars]
        return excerpt[:max_chars]

    @classmethod
    def _chunks_for_source(cls, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        chunks = source.get("_chunks")
        if chunks is not None:
            return chunks
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM research_chunks WHERE source_id = ? ORDER BY chunk_index ASC",
                (int(source["id"]),),
            ).fetchall()
        result = [dict(r) for r in rows]
        if not result:
            # Legacy source not yet indexed: build chunks in memory so retrieval
            # still works without a migration pass.
            result = [
                {
                    "id": None,
                    "source_id": source["id"],
                    "chunk_index": idx,
                    "content": text,
                    "token_count": len(tokenize(text)),
                }
                for idx, text in enumerate(chunk_text(source.get("raw_content") or ""))
            ]
        source["_chunks"] = result
        return result

    @classmethod
    def retrieve_relevant_chunks(
        cls,
        project: Dict[str, Any],
        sources: List[Dict[str, Any]],
        query_extra: str = "",
        max_sources: int = 8,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        max_chars_per_chunk: int = 900,
    ) -> List[Dict[str, Any]]:
        """Return ranked chunks, not the first sources by ID.

        The caller receives a bounded set of excerpts.  This is what keeps the
        prompt from growing with the entire research library.
        """
        query = " ".join(
            str(project.get(k, ""))
            for k in ("name", "target_customer", "creator_interests", "product_category", "country")
        ) + " " + query_extra
        query_tokens = tokenize(query)
        selected_sources = cls.select_relevant_sources(project, sources, query_extra=query_extra, limit=max_sources)

        ranked: List[Dict[str, Any]] = []
        for source in selected_sources:
            for chunk in cls._chunks_for_source(source):
                content = chunk.get("content") or ""
                tokens = tokenize(content)
                overlap = len(tokens & query_tokens)
                numeric = len(_NUMBER_RE.findall(content))
                title_overlap = len(tokenize(source.get("title", "")) & query_tokens)
                score = overlap * 2.0 + title_overlap * 1.5 + min(numeric, 5) * 0.5
                ranked.append({
                    "source": source,
                    "source_id": int(source["id"]),
                    "chunk_index": int(chunk.get("chunk_index", 0)),
                    "content": content[:max_chars_per_chunk],
                    "score": round(score, 4),
                })
        ranked.sort(key=lambda c: (c["score"], -c["source_id"], -c["chunk_index"]), reverse=True)

        # Ensure at least one chunk per selected source before filling by score.
        chosen: List[Dict[str, Any]] = []
        seen_sources: set[int] = set()
        for item in ranked:
            if item["source_id"] not in seen_sources and len(chosen) < max_chunks:
                chosen.append(item)
                seen_sources.add(item["source_id"])
        for item in ranked:
            if len(chosen) >= max_chunks:
                break
            if item not in chosen:
                chosen.append(item)
        return chosen[:max_chunks]

    @classmethod
    def build_evidence_context(
        cls,
        project: Dict[str, Any],
        sources: List[Dict[str, Any]],
        query_extra: str = "",
        limit: int = 8,
        max_chars_per_source: int = 1200,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        max_total_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        batch_ids: Optional[Iterable[int]] = None,
        wrap_untrusted: bool = True,
    ) -> tuple[str, List[Dict[str, Any]]]:
        if batch_ids:
            sources = [s for s in sources if s.get("batch_id") in set(batch_ids) or s.get("batch_id") is None]
        chunks = cls.retrieve_relevant_chunks(
            project, sources,
            query_extra=query_extra,
            max_sources=limit,
            max_chunks=max_chunks,
            max_chars_per_chunk=min(max_chars_per_source, 900),
        )
        selected_ids = {c["source_id"] for c in chunks}
        selected = [s for s in sources if int(s["id"]) in selected_ids]

        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for chunk in chunks:
            grouped.setdefault(chunk["source_id"], []).append(chunk)

        blocks: List[str] = []
        total = 0
        for source in selected:
            source_id = int(source["id"])
            source_chunks = grouped.get(source_id, [])
            if not source_chunks:
                continue
            declared = source.get("declared_reliability") or UNASSESSED
            assessed = source.get("assessed_reliability") or UNASSESSED
            header = (
                f"[Source-{source_id}] {source.get('title', 'Untitled')}\n"
                f"Publisher: {source.get('publisher', 'Unknown')} | "
                f"Declared reliability: {declared} (self-reported — NOT evidence) | "
                f"Independently assessed reliability: {assessed} | "
                f"URL: {source.get('url') or 'Not provided'}"
            )
            excerpt_parts = []
            for chunk in source_chunks:
                excerpt_parts.append(f"(chunk {chunk['chunk_index']})\n{chunk['content']}")
            block = header + "\nExcerpts:\n" + "\n...\n".join(excerpt_parts) + "\n"
            if total + len(block) > max_total_chars and blocks:
                break
            blocks.append(block)
            total += len(block)

        context = "\n---\n".join(blocks) if blocks else "NO RESEARCH SOURCES AVAILABLE."
        # Hard cap: the block loop is approximate, so guarantee the prompt
        # never grows with the library even if a single block is oversized.
        if len(context) > max_total_chars:
            context = context[:max_total_chars].rstrip()
        if wrap_untrusted:
            context = wrap_untrusted_content(context, label="RESEARCH_SOURCE")
        return context, selected

    @staticmethod
    def delete_source(source_id: int) -> bool:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM research_sources WHERE id = ?", (source_id,))
            return cur.rowcount > 0

    @staticmethod
    def create_batch(project_id: int, batch_name: str, description: str = "") -> int:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO research_batches (project_id, batch_name, description) VALUES (?, ?, ?)",
                (project_id, batch_name, description),
            )
            return cur.lastrowid

    @staticmethod
    def assess_all_sources(project_id: int) -> int:
        return SourceTrustService.assess_project_sources(project_id)
