from typing import Dict, Any, List, Optional
import json

from services.ollama_client import OllamaClient
from config.prompts import PROMPT_SYNTHESIS
from utils.security import wrap_untrusted_content

DEFAULT_SYNTHESIS_BATCH_SIZE = 6


class SynthesisService:
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ai = ollama_client or OllamaClient()

    @staticmethod
    def _source_block(source: Dict[str, Any]) -> str:
        declared = source.get("declared_reliability") or "UNASSESSED"
        assessed = source.get("assessed_reliability") or "UNASSESSED"
        return wrap_untrusted_content(
            (
                f"[Source-{source['id']}] Title: {source.get('title', 'Untitled')}\n"
                f"Publisher: {source.get('publisher', 'Unknown')} | "
                f"Declared reliability: {declared} (self-reported, not evidence) | "
                f"Independently assessed: {assessed}\n"
                f"Content excerpt:\n{(source.get('raw_content') or '')[:1600]}"
            ),
            label="RESEARCH_SOURCE",
        )

    def _synthesize_batch(self, sources: List[Dict[str, Any]]) -> tuple[bool, Dict[str, Any], str]:
        research_text = "\n---\n".join(self._source_block(s) for s in sources)
        prompt = PROMPT_SYNTHESIS.format(research_text=research_text)
        try:
            success, raw_resp = self.ai.generate(
                prompt=prompt, format_json=True, temperature=0.3, task="evidence_synthesis"
            )
        except TypeError:
            # Backwards compatibility with simple injected clients that do not
            # accept the newer task keyword.
            success, raw_resp = self.ai.generate(
                prompt=prompt, format_json=True, temperature=0.3
            )
        if not success:
            return False, {}, f"Synthesis generation error: {raw_resp}"
        try:
            cleaned = raw_resp.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.splitlines()[1:-1]).strip()
            return True, json.loads(cleaned), ""
        except Exception as e:
            return False, {}, f"Failed to parse synthesis response: {str(e)}"

    @staticmethod
    def _merge_reports(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
        def _merge_list(key: str, identity: str) -> List[Dict[str, Any]]:
            merged: List[Dict[str, Any]] = []
            seen: set[str] = set()
            for report in reports:
                for item in report.get(key, []) or []:
                    if not isinstance(item, dict):
                        continue
                    value = " ".join(str(item.get(k, "")) for k in identity.split("|")).strip().lower()
                    if value and value in seen:
                        continue
                    seen.add(value)
                    merged.append(item)
            return merged

        summaries = []
        for report in reports:
            summary = str(report.get("actionable_framework_summary", "") or "").strip()
            if summary and summary not in summaries:
                summaries.append(summary)

        return {
            "consensus_findings": _merge_list("consensus_findings", "finding"),
            "conflicts_and_uncertainties": _merge_list("conflicts_and_uncertainties", "topic|conflict_description"),
            "proven_methods": _merge_list("proven_methods", "method_name"),
            "common_failures": _merge_list("common_failures", "failure_point"),
            "actionable_framework_summary": " ".join(summaries),
            "batch_count": len(reports),
            "consolidated": True,
        }

    def synthesize_research(
        self,
        sources: List[Dict[str, Any]],
        batch_size: int = DEFAULT_SYNTHESIS_BATCH_SIZE,
    ) -> tuple[bool, Dict[str, Any], str]:
        """Combine research sources into a source-grounded synthesis report.

        Large libraries are processed in bounded batches so no single prompt
        contains the entire library.  Batch outputs are consolidated with
        deduplication, preserving source IDs.
        """
        if not sources:
            return False, {}, (
                "No research sources available to synthesize. "
                "Please add sources to the Research Library."
            )

        batch_size = max(1, int(batch_size or DEFAULT_SYNTHESIS_BATCH_SIZE))
        batches = [sources[i:i + batch_size] for i in range(0, len(sources), batch_size)]

        reports: List[Dict[str, Any]] = []
        failures: List[str] = []
        for index, batch in enumerate(batches, start=1):
            ok, report, message = self._synthesize_batch(batch)
            if ok:
                reports.append(report)
            else:
                failures.append(f"Batch {index}: {message}")

        if not reports:
            return False, {}, "Synthesis failed for every batch. " + " ".join(failures)

        if len(reports) == 1:
            result = reports[0]
            result.setdefault("consolidated", False)
            result.setdefault("batch_count", 1)
            return True, result, "Synthesis completed successfully."

        result = self._merge_reports(reports)
        message = (
            f"Synthesis completed across {len(batches)} batches "
            f"({len(sources)} sources, max {batch_size} per prompt)."
        )
        if failures:
            message += " Some batches failed: " + " ".join(failures)
        return True, result, message
