import re
from typing import List, Dict, Any, Optional
from database.db import get_connection

class CitationService:
    """
    Dedicated citation integrity and verification engine (Priority 2).
    - Extracts all [Source-X] citations from chapter text.
    - Verifies each Source-ID against real SQLite research_sources records for the project.
    - Cross-references claim text with actual source content keywords to detect unsupported claims.
    - Flags invalid IDs, hallucinated/fabricated citations, and missing evidence.
    """

    CITATION_PATTERN = re.compile(r"\[(?:Source|Ref)-(\d+)\]", re.IGNORECASE)

    @classmethod
    def extract_citations(cls, text: str) -> List[int]:
        """Finds all referenced integer Source IDs in the text."""
        if not text:
            return []
        matches = cls.CITATION_PATTERN.findall(text)
        # Return unique IDs in order of appearance
        seen = set()
        result = []
        for m in matches:
            val = int(m)
            if val not in seen:
                seen.add(val)
                result.append(val)
        return result

    @classmethod
    def verify_chapter_citations(cls, project_id: int, chapter_text: str) -> Dict[str, Any]:
        """
        Runs comprehensive citation validation:
        1. Valid citations: Source IDs that actually exist in research_sources for this project.
        2. Invalid citations: Source IDs cited in text that DO NOT exist in research_sources.
        3. Fabricated citations: Detection of invented citations, fake URLs, or non-existent studies.
        4. Claim alignment: Checks if content near the citation aligns with the source text.
        """
        cited_ids = cls.extract_citations(chapter_text)

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM research_sources WHERE project_id = ?", (project_id,))
            rows = [dict(r) for r in cur.fetchall()]

        valid_sources_map = {r["id"]: r for r in rows}
        valid_citations = []
        invalid_citations = []
        claim_checks = []

        for cid in cited_ids:
            if cid in valid_sources_map:
                source = valid_sources_map[cid]
                valid_citations.append({
                    "source_id": cid,
                    "title": source["title"],
                    "publisher": source.get("publisher", "Unknown"),
                    "reliability": source.get("reliability_assessment", "MEDIUM"),
                    "url": source.get("url")
                })
                # Check claim alignment: search for text around the citation
                pattern = rf"([^.\n]*?\[(?:Source|Ref)-{cid}\][^.\n]*)"
                matches = re.findall(pattern, chapter_text, re.IGNORECASE)
                for sentence in matches[:2]:
                    # Extract sample keywords from source raw content
                    source_words = set(re.findall(r"\w{4,}", source.get("raw_content", "").lower()))
                    sentence_words = set(re.findall(r"\w{4,}", sentence.lower()))
                    overlap = len(source_words.intersection(sentence_words))
                    claim_checks.append({
                        "source_id": cid,
                        "claim_sentence": sentence.strip(),
                        "keyword_overlap": overlap,
                        "supported": overlap >= 2
                    })
            else:
                invalid_citations.append(cid)

        # Detect suspicious fake URLs in text (e.g., example.com/fake, fakeurl, or non-project URLs)
        fake_url_pattern = re.compile(r"https?://(?:www\.)?(?:fake\w+|example\.com/\w+|invented\w+)\S*", re.IGNORECASE)
        suspicious_urls = fake_url_pattern.findall(chapter_text)

        total_citations = len(cited_ids)
        is_clean = (len(invalid_citations) == 0 and len(suspicious_urls) == 0)
        
        return {
            "total_citations": total_citations,
            "valid_citations": valid_citations,
            "invalid_citations": invalid_citations,
            "suspicious_urls": suspicious_urls,
            "claim_checks": claim_checks,
            "is_clean": is_clean,
            "status": "PASS" if is_clean and total_citations > 0 else ("WARNING" if total_citations == 0 else "FAIL"),
            "summary": (
                f"Verified {len(valid_citations)} valid source citations. "
                + (f"FLAGGED {len(invalid_citations)} invalid/unregistered Source IDs ({invalid_citations}). " if invalid_citations else "")
                + (f"FLAGGED {len(suspicious_urls)} suspicious URLs. " if suspicious_urls else "")
                + ("Zero citations detected in chapter text." if total_citations == 0 else "")
            )
        }

