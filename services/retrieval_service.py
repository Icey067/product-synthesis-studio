"""Context-aware research retrieval for chapter generation.

The application cannot assume a local embedding model exists, so this module
does **not** pretend to be semantic search.  It implements a deterministic
hybrid ranking:

* BM25-style lexical relevance over ``research_chunks``.
* Independent source-quality weighting (assessed reliability first).
* Publication-date recency with a neutral score for unknown dates.
* Near-duplicate suppression so one repeated passage cannot dominate a prompt.
* Conservative, advisory contradiction flags between related excerpts.
* Explicit citation traceability (``[Source-<id>]`` + chunk index + excerpt).

The caller receives both the prompt context and a structured evidence record so
the UI can show exactly which research was used and it can be stored per
chapter in ``chapter_sources``.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from database.db import get_connection, transaction
from services.research_service import ResearchService, tokenize
from utils.security import wrap_untrusted_content

_NUMBER_RE = re.compile(r"\d[\d.,]*")
_NEGATIONS = {
    "not", "no", "never", "none", "cannot", "can't", "dont", "don't", "doesn't",
    "isn't", "aren't", "without", "unlikely", "fails", "ineffective",
}

QUALITY_WEIGHTS = {
    "HIGH": 1.0,
    "MEDIUM": 0.7,
    "LOW": 0.35,
    "UNASSESSED": 0.5,
}

DEFAULT_LIMIT = 8
DEFAULT_MAX_CHARS = 900


class RetrievalService:
    # ------------------------------------------------------------------
    # Scoring primitives
    # ------------------------------------------------------------------
    @staticmethod
    def _source_quality(source: Dict[str, Any]) -> float:
        assessed = str(source.get("assessed_reliability") or "UNASSESSED").upper()
        declared = str(source.get("declared_reliability") or "UNASSESSED").upper()
        base = QUALITY_WEIGHTS.get(assessed, 0.5)
        # A self-declared label is a weak signal only; it can never equal an
        # independent assessment.
        if assessed == "UNASSESSED" and declared in QUALITY_WEIGHTS:
            base = max(0.25, QUALITY_WEIGHTS[declared] * 0.6)
        try:
            score = float(source.get("assessment_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if score > 0:
            base = max(base, min(1.0, score if score <= 1.0 else score / 100.0))
        return round(base, 4)

    @staticmethod
    def _recency(source: Dict[str, Any]) -> float:
        """Return 0..1 where newer published material ranks higher.

        Unknown dates get a neutral 0.4 instead of being treated as current.
        """
        raw = (source.get("publication_date") or "").strip()
        year: Optional[int] = None
        match = re.search(r"(19|20)\d{2}", raw)
        if match:
            try:
                year = int(match.group(0))
            except ValueError:
                year = None
        if year is None:
            created = str(source.get("created_at") or "")
            created_match = re.search(r"(19|20)\d{2}", created)
            if created_match:
                try:
                    year = int(created_match.group(0))
                except ValueError:
                    year = None
        if year is None:
            return 0.4
        current_year = datetime.now().year
        age = max(0, current_year - year)
        # 0 years -> 1.0, 10+ years -> 0.1
        return round(max(0.1, 1.0 - (age / 11.0)), 4)

    @staticmethod
    def _normalized(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").lower()).strip()

    @classmethod
    def _is_duplicate(cls, tokens: set[str], kept: List[set[str]], threshold: float = 0.9) -> bool:
        if not tokens:
            return False
        for existing in kept:
            union = tokens | existing
            if not union:
                continue
            if len(tokens & existing) / len(union) >= threshold:
                return True
        return False

    @staticmethod
    def _bm25_scores(token_sets: List[set[str]], query_tokens: set[str]) -> List[float]:
        if not token_sets or not query_tokens:
            return [0.0 for _ in token_sets]
        n_docs = len(token_sets)
        avg_len = sum(len(t) for t in token_sets) / max(1, n_docs) or 1.0
        df: Dict[str, int] = {}
        for tokens in token_sets:
            for term in query_tokens & tokens:
                df[term] = df.get(term, 0) + 1
        k1, b = 1.5, 0.75
        scores: List[float] = []
        for tokens in token_sets:
            dl = len(tokens) or 1
            score = 0.0
            for term in query_tokens:
                tf = 1.0 if term in tokens else 0.0
                if not tf:
                    continue
                doc_freq = df.get(term, 0)
                idf = math.log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))
                denom = tf + k1 * (1 - b + b * (dl / avg_len))
                score += idf * ((tf * (k1 + 1)) / denom)
            scores.append(score)
        return scores

    # ------------------------------------------------------------------
    # Candidate collection
    # ------------------------------------------------------------------
    @classmethod
    def _candidates(cls, project_id: int, sources: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        sources = sources if sources is not None else ResearchService.get_sources(project_id)
        candidates: List[Dict[str, Any]] = []
        for source in sources:
            try:
                source_id = int(source["id"])
            except (KeyError, TypeError, ValueError):
                continue
            for chunk in ResearchService._chunks_for_source(source):
                content = (chunk.get("content") or "").strip()
                if not content:
                    continue
                candidates.append(
                    {
                        "source": source,
                        "source_id": source_id,
                        "chunk_id": chunk.get("id"),
                        "chunk_index": int(chunk.get("chunk_index") or 0),
                        "content": content,
                    }
                )
        return candidates

    # ------------------------------------------------------------------
    # Main retrieval
    # ------------------------------------------------------------------
    @classmethod
    def retrieve(
        cls,
        project_id: int,
        query: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        limit: int = DEFAULT_LIMIT,
        max_chars_per_chunk: int = DEFAULT_MAX_CHARS,
    ) -> Dict[str, Any]:
        """Rank research chunks for a chapter query.

        Returns a dict with ``items`` (ranked evidence), ``sources`` (the
        distinct sources selected), ``contradictions`` and ``query``.
        """
        sources = sources if sources is not None else ResearchService.get_sources(project_id)
        candidates = cls._candidates(project_id, sources)
        query_tokens = tokenize(query or "")

        token_sets = [tokenize(c["content"]) for c in candidates]
        lexical = cls._bm25_scores(token_sets, query_tokens)
        max_lexical = max(lexical) if lexical else 0.0

        ranked: List[Dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            source = candidate["source"]
            tokens = token_sets[index]
            title_tokens = tokenize(str(source.get("title", "")))
            title_overlap = len(title_tokens & query_tokens) / max(1, len(query_tokens))
            numeric_count = len(_NUMBER_RE.findall(candidate["content"]))
            lexical_norm = (lexical[index] / max_lexical) if max_lexical else 0.0
            quality = cls._source_quality(source)
            recency = cls._recency(source)
            combined = (
                0.60 * lexical_norm
                + 0.18 * quality
                + 0.10 * recency
                + 0.08 * min(1.0, title_overlap)
                + 0.04 * min(1.0, numeric_count / 5.0)
            )
            ranked.append(
                {
                    **candidate,
                    "tokens": tokens,
                    "lexical_score": round(lexical[index], 4),
                    "relevance_score": round(combined, 4),
                    "quality_score": quality,
                    "recency_score": recency,
                    "citation_label": f"[Source-{candidate['source_id']}]",
                    "excerpt": candidate["content"][:max_chars_per_chunk],
                }
            )

        ranked.sort(
            key=lambda item: (
                item["relevance_score"],
                item["quality_score"],
                -item["source_id"],
                -item["chunk_index"],
            ),
            reverse=True,
        )

        # Duplicate suppression (keeps the highest-scoring copy).
        deduped: List[Dict[str, Any]] = []
        kept_tokens: List[set[str]] = []
        for item in ranked:
            if cls._is_duplicate(item["tokens"], kept_tokens):
                item = {**item, "duplicate_of": True}
                continue
            kept_tokens.append(item["tokens"])
            deduped.append(item)

        # Ensure each source with a relevant chunk gets at most one guaranteed
        # slot, then fill by score.  This avoids one verbose source dominating.
        selected: List[Dict[str, Any]] = []
        seen_sources: set[int] = set()
        for item in deduped:
            if len(selected) >= limit:
                break
            if item["source_id"] not in seen_sources:
                selected.append(item)
                seen_sources.add(item["source_id"])
        for item in deduped:
            if len(selected) >= limit:
                break
            if item not in selected:
                selected.append(item)

        for item in selected:
            item.pop("tokens", None)

        selected_sources: Dict[int, Dict[str, Any]] = {}
        for item in selected:
            selected_sources[item["source_id"]] = {
                "id": item["source_id"],
                "title": item["source"].get("title", "Untitled"),
                "publisher": item["source"].get("publisher", "Unknown"),
                "url": item["source"].get("url"),
                "publication_date": item["source"].get("publication_date", "Unknown"),
                "assessed_reliability": item["source"].get("assessed_reliability", "UNASSESSED"),
                "quality_score": item["quality_score"],
                "recency_score": item["recency_score"],
            }

        return {
            "project_id": project_id,
            "query": query,
            "items": selected,
            "sources": list(selected_sources.values()),
            "contradictions": cls.identify_contradictions(selected),
            "candidate_count": len(candidates),
            "duplicates_suppressed": len(ranked) - len(deduped),
        }

    # ------------------------------------------------------------------
    # Contradiction detection (advisory only)
    # ------------------------------------------------------------------
    @classmethod
    def identify_contradictions(cls, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Flag related excerpts whose numbers or polarity disagree.

        This is a heuristic screening aid, never proof of a factual conflict.
        """
        flags: List[Dict[str, Any]] = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if a["source_id"] == b["source_id"]:
                    continue
                ta, tb = tokenize(a["content"]), tokenize(b["content"])
                if not ta or not tb:
                    continue
                overlap = len(ta & tb) / max(1, len(ta | tb))
                if overlap < 0.18:
                    continue
                neg_a = bool(ta & _NEGATIONS)
                neg_b = bool(tb & _NEGATIONS)
                num_a = set(_NUMBER_RE.findall(a["content"]))
                num_b = set(_NUMBER_RE.findall(b["content"]))
                reasons = []
                if neg_a != neg_b:
                    reasons.append("opposite polarity")
                if num_a and num_b and num_a != num_b:
                    reasons.append("different numeric values")
                if not reasons:
                    continue
                flags.append(
                    {
                        "advisory": True,
                        "sources": [a["source_id"], b["source_id"]],
                        "labels": [a["citation_label"], b["citation_label"]],
                        "reason": " and ".join(reasons),
                        "overlap": round(overlap, 3),
                        "message": (
                            "Related excerpts from "
                            f"{a['citation_label']} and {b['citation_label']} appear to "
                            f"disagree ({', '.join(reasons)}). Verify before citing."
                        ),
                    }
                )
        return flags

    # ------------------------------------------------------------------
    # Prompt context
    # ------------------------------------------------------------------
    @classmethod
    def build_context(
        cls,
        project_id: int,
        query: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        limit: int = DEFAULT_LIMIT,
        max_chars_per_chunk: int = DEFAULT_MAX_CHARS,
        max_total_chars: int = 9000,
        wrap_untrusted: bool = True,
    ) -> Tuple[str, Dict[str, Any]]:
        evidence = cls.retrieve(
            project_id,
            query,
            sources=sources,
            limit=limit,
            max_chars_per_chunk=max_chars_per_chunk,
        )
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for item in evidence["items"]:
            grouped.setdefault(item["source_id"], []).append(item)

        blocks: List[str] = []
        total = 0
        for source in evidence["sources"]:
            source_items = grouped.get(source["id"], [])
            if not source_items:
                continue
            header = (
                f"[Source-{source['id']}] {source['title']}\n"
                f"Publisher: {source['publisher']} | "
                f"Published: {source['publication_date']} | "
                f"Assessed reliability: {source['assessed_reliability']} | "
                f"URL: {source['url'] or 'Not provided'}"
            )
            excerpts = "\n...\n".join(
                f"(chunk {item['chunk_index']})\n{item['excerpt']}" for item in source_items
            )
            block = header + "\nExcerpts:\n" + excerpts + "\n"
            if total + len(block) > max_total_chars and blocks:
                break
            blocks.append(block)
            total += len(block)

        context = "\n---\n".join(blocks) if blocks else "NO RELEVANT RESEARCH SOURCES AVAILABLE."
        if len(context) > max_total_chars:
            context = context[:max_total_chars].rstrip()
        if wrap_untrusted:
            context = wrap_untrusted_content(context, label="RESEARCH_SOURCE")
        evidence["context"] = context
        return context, evidence

    # ------------------------------------------------------------------
    # Traceability persistence
    # ------------------------------------------------------------------
    @classmethod
    def store_chapter_sources(
        cls,
        project_id: int,
        chapter_id: int,
        evidence: Dict[str, Any],
    ) -> int:
        """Replace the stored source trace for a chapter.

        Returns the number of excerpt rows written.
        """
        items = evidence.get("items", []) if evidence else []
        with transaction() as conn:
            conn.execute(
                "DELETE FROM chapter_sources WHERE chapter_id = ?", (chapter_id,)
            )
            count = 0
            for item in items:
                conn.execute(
                    """
                    INSERT INTO chapter_sources (
                        project_id, chapter_id, source_id, chunk_id, chunk_index,
                        relevance_score, quality_score, citation_label, excerpt, used
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        project_id,
                        chapter_id,
                        item.get("source_id"),
                        item.get("chunk_id"),
                        item.get("chunk_index"),
                        float(item.get("relevance_score") or 0.0),
                        float(item.get("quality_score") or 0.0),
                        item.get("citation_label") or f"[Source-{item.get('source_id')}]",
                        (item.get("excerpt") or "")[:2000],
                    ),
                )
                count += 1
        return count

    @classmethod
    def get_chapter_sources(cls, chapter_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT cs.*, rs.title, rs.publisher, rs.assessed_reliability
                FROM chapter_sources cs
                LEFT JOIN research_sources rs ON rs.id = cs.source_id
                WHERE cs.chapter_id = ?
                ORDER BY cs.relevance_score DESC, cs.id ASC
                """,
                (chapter_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    @classmethod
    def used_source_ids(cls, chapter_id: int) -> List[int]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source_id FROM chapter_sources "
                "WHERE chapter_id = ? AND source_id IS NOT NULL AND used = 1",
                (chapter_id,),
            ).fetchall()
            return [int(r["source_id"]) for r in rows]

    @classmethod
    def chapter_evidence_summary(cls, chapter_id: int) -> Dict[str, Any]:
        rows = cls.get_chapter_sources(chapter_id)
        source_ids = sorted({int(r["source_id"]) for r in rows if r.get("source_id")})
        return {
            "excerpt_count": len(rows),
            "source_ids": source_ids,
            "source_count": len(source_ids),
            "has_evidence": bool(source_ids),
            "sources": rows,
        }
