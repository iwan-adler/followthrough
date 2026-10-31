"""Single entry point for every LLM call.

generate_json() asks the configured provider (Gemini or Groq, free tier) for JSON,
validates it against a Pydantic schema, retries with the validation error fed back,
and falls back to a caller-supplied deterministic mock when no key is configured or
the provider keeps failing. The demo never dies.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from .config import settings

log = logging.getLogger("followthrough.llm")

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODELS = {"gemini": "gemini-2.5-flash", "groq": "llama-3.3-70b-versatile"}

# What served the most recent call: "gemini", "groq", "mock" or "mock-fallback".
_last: dict = {"source": None, "error": None}


def _live() -> bool:
    s = settings()
    return bool(s.llm_api_key) and s.llm_provider in DEFAULT_MODELS


def _model() -> str:
    s = settings()
    return s.llm_model or DEFAULT_MODELS.get(s.llm_provider, "")


def provider_info() -> dict:
    live = _live()
    return {
        "provider": settings().llm_provider if live else "mock",
        "model": _model() if live else "deterministic-mock",
        "live": live,
        "last_source": _last["source"],
        "last_error": _last["error"],
    }


def _call_gemini(system: str, prompt: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings().llm_api_key)
    resp = client.models.generate_content(
        model=_model(),
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )
    return resp.text or ""


def _call_groq(system: str, prompt: str) -> str:
    from groq import Groq

    client = Groq(api_key=settings().llm_api_key)
    resp = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return resp.choices[0].message.content or ""


_PROVIDERS = {"gemini": _call_gemini, "groq": _call_groq}


def parse_json(raw: str) -> dict:
    """Parse model output, tolerating code fences or stray prose around the object."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model output")
        return json.loads(text[start : end + 1])


def _run_mock(schema: type[T], mock: Callable[[], dict | T]) -> T:
    out = mock()
    return out if isinstance(out, schema) else schema.model_validate(out)


def generate_json(
    *, system: str, prompt: str, schema: type[T], mock: Callable[[], dict | T]
) -> T:
    if not _live():
        _last.update(source="mock", error=None)
        return _run_mock(schema, mock)

    s = settings()
    call = _PROVIDERS[s.llm_provider]
    full_system = (
        f"{system}\n\nRespond with ONLY a JSON object (no prose) that validates against "
        f"this JSON schema:\n{json.dumps(schema.model_json_schema())}"
    )
    attempt_prompt = prompt
    last_err: Exception | None = None
    for attempt in range(1, s.llm_max_retries + 1):
        try:
            result = schema.model_validate(parse_json(call(full_system, attempt_prompt)))
            _last.update(source=s.llm_provider, error=None)
            return result
        except (ValidationError, ValueError) as e:
            # Bad shape: tell the model exactly what was wrong and try again.
            last_err = e
            attempt_prompt = (
                f"{prompt}\n\nYour previous reply was invalid JSON for the schema: "
                f"{str(e)[:600]}\nReturn the corrected JSON object only."
            )
        except Exception as e:  # network, rate limit, auth
            last_err = e
            time.sleep(min(2 ** (attempt - 1), 8))
        log.warning("LLM attempt %d/%d failed: %s", attempt, s.llm_max_retries, last_err)

    log.error("LLM failed after %d attempts, using mock: %s", s.llm_max_retries, last_err)
    _last.update(source="mock-fallback", error=str(last_err)[:300])
    return _run_mock(schema, mock)
