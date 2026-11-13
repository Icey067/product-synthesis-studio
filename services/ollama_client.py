import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Type, TypeVar
import requests
from pydantic import BaseModel
from config.settings import DEFAULT_OLLAMA_URL, DEFAULT_TIMEOUT_SECONDS
from database.db import get_connection
from services.model_registry import ModelRegistry

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

class OllamaClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or self._get_configured_url()
        self.last_metrics: Dict[str, Any] = {}

    def _get_configured_url(self) -> str:
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT value FROM application_settings WHERE key = 'ollama_url'")
                row = cur.fetchone()
                if row and row["value"]:
                    return row["value"]
        except Exception:
            pass
        return DEFAULT_OLLAMA_URL

    def _get_configured_context_window(self) -> int:
        """Return the operator's saved context window, falling back to 8192."""
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM application_settings WHERE key = 'context_window'"
                ).fetchone()
                if row and row["value"]:
                    value = int(row["value"])
                    if 512 <= value <= 131072:
                        return value
        except Exception:
            pass
        return 8192

    def check_connection(self) -> tuple[bool, str]:
        """Verify if Ollama service is reachable."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3.0)
            if resp.status_code == 200:
                return True, "Connected to Ollama service."
            return False, f"Ollama returned HTTP status {resp.status_code}."
        except requests.exceptions.ConnectionError:
            return False, f"Cannot connect to Ollama at {self.base_url}. Ensure 'ollama serve' is running."
        except Exception as e:
            return False, f"Connection error: {str(e)}"

    def list_models(self) -> list[str]:
        """Return list of models downloaded and available in Ollama."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=4.0)
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.warning(f"Failed to fetch models from Ollama: {e}")
        return []

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        context_window: Optional[int] = None,
        format_json: bool = False,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        task: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        Send a generation request to Ollama.
        Returns: (success: bool, response_text_or_error: str)

        ``task`` selects the operator's task-specific model.  If no installed
        model exists the call fails explicitly; it never falls back to a cloud
        model or to a hard-coded unavailable name.
        """
        selected_model = model or self._get_selected_model(task=task)
        if not selected_model:
            return (
                False,
                "Generation failed: no installed Ollama model is available. Pull a local model "
                "(for example: ollama pull <model>) and select it in Settings.",
            )
        url = f"{self.base_url}/api/generate"
        num_ctx = int(context_window or self._get_configured_context_window())

        payload = {
            "model": selected_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }
        if system:
            payload["system"] = system
        if format_json:
            payload["format"] = "json"

        started = time.time()
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            elapsed_ms = round((time.time() - started) * 1000, 1)
            if resp.status_code == 200:
                data = resp.json()
                metrics = {
                    "task": task or "",
                    "model": selected_model,
                    "wall_time_ms": elapsed_ms,
                    "total_duration_ms": round(
                        float(data.get("total_duration") or 0) / 1_000_000, 1
                    ),
                    "load_duration_ms": round(
                        float(data.get("load_duration") or 0) / 1_000_000, 1
                    ),
                    "prompt_eval_count": data.get("prompt_eval_count"),
                    "eval_count": data.get("eval_count"),
                    "eval_duration_ms": round(
                        float(data.get("eval_duration") or 0) / 1_000_000, 1
                    ),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self.last_metrics = metrics
                try:
                    ModelRegistry.record_generation_metrics(metrics)
                except Exception:
                    pass
                return True, data.get("response", "").strip()
            elif resp.status_code == 404:
                return False, f"Model '{selected_model}' not found in Ollama. Run: ollama run {selected_model}"
            else:
                return False, f"Ollama error (HTTP {resp.status_code}): {resp.text}"
        except requests.exceptions.Timeout:
            return False, f"Generation timed out after {timeout} seconds. Consider using smaller sections or faster model."
        except requests.exceptions.ConnectionError:
            return False, f"Lost connection to Ollama at {self.base_url}. Verify that the service is running."
        except Exception as e:
            return False, f"Generation failed: {str(e)}"

    def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        system: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.4,
        max_retries: int = 2,
        task: Optional[str] = None,
    ) -> tuple[bool, Optional[T], str]:
        """
        Generate and validate a structured response using Pydantic.
        Retries safely if JSON is malformed without overwriting existing data.
        """
        system_instruction = (system or "") + "\nYou MUST return valid JSON adhering strictly to the requested schema."
        current_prompt = prompt

        for attempt in range(max_retries + 1):
            success, raw_resp = self.generate(
                prompt=current_prompt,
                system=system_instruction,
                model=model,
                temperature=temperature,
                format_json=True,
                task=task,
            )
            if not success:
                return False, None, raw_resp

            try:
                # Clean potential markdown fences ```json ... ```
                cleaned = raw_resp.strip()
                if "```json" in cleaned:
                    cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
                elif "```" in cleaned:
                    cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
                else:
                    # In case the model outputs introductory or concluding chatter around JSON
                    start_idx = cleaned.find("{")
                    end_idx = cleaned.rfind("}")
                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                        cleaned = cleaned[start_idx : end_idx + 1]

                parsed_json = json.loads(cleaned)
                validated = schema.model_validate(parsed_json)
                return True, validated, "Success"
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Structured validation attempt {attempt+1} failed: {e}")
                if attempt < max_retries:
                    current_prompt = (
                        f"{prompt}\n\nPREVIOUS RESPONSE HAD PARSING/SCHEMA ERRORS: {str(e)}.\n"
                        "Please correct the JSON and ensure ALL required keys match the schema."
                    )
                else:
                    return False, None, f"Failed to validate response against schema after {max_retries+1} attempts: {str(e)}"

        return False, None, "Unknown error during structured generation."

    def _get_selected_model(self, task: Optional[str] = None) -> Optional[str]:
        """Resolve a task to an actually installed local model.

        Returns ``None`` when Ollama is offline or has no models.  The previous
        hard-coded default (``qwen3.5:9b``) is deliberately gone: sending a
        request for a model that is not installed produced opaque failures.
        """
        try:
            return ModelRegistry.resolve_model(task)
        except Exception as exc:
            logger.warning("Model resolution failed: %s", exc)
            return None

