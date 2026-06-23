"""Swappable LLM layer.

One async entry point, ``chat()``, routed entirely by env vars
(``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL``). Speaks the
OpenAI-compatible ``/chat/completions`` protocol, so OpenCode (MiniMax M3 Free
or any other hosted model), OpenAI, and local Ollama all work with no code change.

Structured output: pass ``json_schema=`` (a dict JSON-Schema or a Pydantic model
class) and ``chat()`` returns a parsed ``dict``. It sets ``response_format`` json
mode AND injects a strict-JSON system instruction, then parses tolerantly
(strips code fences, extracts the first ``{...}``) and retries once with a
corrective prompt if parsing fails.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Union

import httpx
from pydantic import BaseModel

from src.config import settings

log = logging.getLogger(__name__)

JSONSchema = Union[dict, type[BaseModel]]


class LLMError(RuntimeError):
    """Raised when the LLM is mis-configured or returns an unrecoverable error."""


def _endpoint() -> str:
    if not settings.llm_base_url:
        raise LLMError(
            "LLM_BASE_URL is not set. Configure config/.env "
            "(an OpenAI-compatible endpoint, e.g. your OpenCode base URL ending in /v1)."
        )
    return settings.llm_base_url.rstrip("/") + "/chat/completions"


def _headers() -> dict:
    if not settings.llm_api_key:
        raise LLMError("LLM_API_KEY is not set. Configure config/.env.")
    return {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }


def _schema_to_str(schema: JSONSchema) -> str:
    if isinstance(schema, dict):
        return json.dumps(schema, ensure_ascii=False)
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return json.dumps(schema.model_json_schema(), ensure_ascii=False)
    raise LLMError(f"Unsupported json_schema type: {type(schema)!r}")


def _inject_json_instruction(messages: list[dict], schema: JSONSchema) -> list[dict]:
    instr = (
        "You MUST respond with a single valid JSON object only — no markdown, no "
        "fences, no prose before or after. The object must conform to this JSON "
        f"Schema:\n{_schema_to_str(schema)}\n"
        "Include only keys allowed by the schema."
    )
    return list(messages) + [{"role": "system", "content": instr}]


def _extract_json(text: str) -> Optional[dict]:
    t = text.strip()
    if t.startswith("```"):
        # strip a leading fence (and optional language word) + trailing fence
        t = t.strip("`")
        if "\n" in t:
            t = t.split("\n", 1)[1]
        t = t.strip().rstrip("`").strip()
    start = t.find("{")
    if start == -1:
        return None
    end = t.rfind("}")
    if end == -1 or end <= start:
        return None
    try:
        return json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return None


async def chat(
    messages: list[dict],
    *,
    json_schema: Optional[JSONSchema] = None,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    retries: int = 2,
    timeout: Optional[int] = None,
) -> Union[str, dict]:
    """Call the configured LLM. Returns parsed ``dict`` if ``json_schema`` given, else text."""
    model = model or settings.llm_model
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens

    use_json = json_schema is not None
    if use_json:
        body["response_format"] = {"type": "json_object"}
        body["messages"] = _inject_json_instruction(messages, json_schema)

    timeout = timeout or settings.llm_timeout
    last_err: Optional[str] = None

    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_endpoint(), headers=_headers(), json=body)
            if resp.status_code >= 400:
                # Surface the provider's error body — e.g. MiniMax free-promo-ended 401.
                raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:400]}")
            data = resp.json()
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise LLMError(f"Unexpected LLM response shape: {str(data)[:400]}")

            if not use_json:
                return content

            parsed = _extract_json(content)
            if parsed is not None:
                return parsed
            last_err = "invalid JSON in model output"
            log.warning("LLM JSON parse failed (attempt %d). Raw: %s", attempt, content[:200])
            # nudge the model and retry
            body["messages"] = body["messages"] + [
                {"role": "assistant", "content": content},
                {"role": "system", "content": (
                    "That was not valid JSON. Reply with ONLY a JSON object "
                    "matching the schema — no prose, no code fences."
                )},
            ]
            continue
        except LLMError:
            raise
        except httpx.HTTPError as e:
            last_err = f"transport: {e}"
            log.warning("LLM transport error (attempt %d): %s", attempt, e)
            continue

    raise LLMError(f"LLM call failed after {retries + 1} attempt(s): {last_err}")


async def chat_json(
    messages: list[dict], schema: JSONSchema, **kw: Any
) -> dict:
    """Convenience wrapper that guarantees a ``dict`` return."""
    result = await chat(messages, json_schema=schema, **kw)
    if not isinstance(result, dict):
        raise LLMError(f"Expected JSON object, got {type(result).__name__}.")
    return result
