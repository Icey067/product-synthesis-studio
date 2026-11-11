"""Shared generation helper: validate a structured response against a contract,
then regenerate with corrective instructions when the model violates it.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel

from services.ollama_client import OllamaClient
from services.product_format_service import corrective_instructions


def generate_with_validation(
    ai: OllamaClient,
    prompt: str,
    schema: Type[BaseModel],
    validator: Callable[[str, Dict[str, Any]], Tuple[bool, List[Dict[str, str]]]],
    product_format: str,
    stage: str,
    temperature: float = 0.5,
    max_attempts: int = 2,
    prefill: Optional[Dict[str, Any]] = None,
    task: Optional[str] = None,
) -> Tuple[bool, Optional[BaseModel], str, List[Dict[str, str]]]:
    """Generate, validate, and retry with explicit correction instructions."""
    current_prompt = prompt
    last_issues: List[Dict[str, str]] = []
    last_error = ""

    for attempt in range(max_attempts):
        ok, validated, msg = ai.generate_structured(
            prompt=current_prompt,
            schema=schema,
            temperature=temperature,
            task=task,
        )
        if not ok or validated is None:
            last_error = msg
            continue

        data = validated.model_dump()
        if prefill:
            for key, value in prefill.items():
                data.setdefault(key, value)
        valid, issues = validator(product_format, data)
        if valid:
            return True, validated, msg, []

        last_issues = issues
        last_error = "Product-format contract violated."
        current_prompt = (
            prompt
            + "\n\n"
            + corrective_instructions(product_format, issues, stage)
        )

    detail = "; ".join(i["message"] for i in last_issues) or last_error
    return False, None, f"{stage} failed format validation: {detail}", last_issues
