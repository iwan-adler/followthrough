"""Single entry point for every LLM call.

generate_json() asks the configured provider (Gemini or Groq, free tier) for JSON and
validates it against a Pydantic schema. Free tiers are flaky: models get busy (503/429)
or retired (404). So each provider has a chain of models; a busy model hands over to the
next one immediately, a retired one is skipped for the rest of the run, and the last model
that worked is tried first next time. Invalid JSON is retried with the error fed back.
If everything fails, or no key is set, a caller-supplied deterministic mock answers.
The demo never dies.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from functools import lru_cache
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from . import db
from .config import settings

log = logging.getLogger("followthrough.llm")

T = TypeVar("T", bound=BaseModel)

# Tried in order (after LLM_MODEL, if set). Availability on free tiers changes often.
DEFAULT_CHAINS = {
    "gemini": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-flash-latest",
               "gemini-2.5-flash"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
}
REQUEST_TIMEOUT_S = 15  # flash models answer in 2-5s; a hung busy model should hand over quickly

# What served the most recent call: "gemini", "groq", "mock" or "mock-fallback".
_last: dict = {"source": None, "error": None, "model": None, "cached": False}
_sticky: dict[str, str] = {}   # provider -> last model that worked
_dead: set[str] = set()        # models that answered 404 (retired / not available to this key)
_current = {"model": ""}       # model for the call in flight, read by the provider functions


def _live() -> bool:
    s = settings()
    return bool(s.llm_api_key) and s.llm_provider in DEFAULT_CHAINS


def chain() -> list[str]:
    s = settings()
    models = [s.llm_model] if s.llm_model else []
    models += [m.strip() for m in s.llm_fallback_models.split(",") if m.strip()] or DEFAULT_CHAINS.get(s.llm_provider, [])
    if sticky := _sticky.get(s.llm_provider):
        models.insert(0, sticky)
    return [m for m in dict.fromkeys(models) if m not in _dead]


def provider_info() -> dict:
    live = _live()
    models = chain() if live else []
    return {
        "provider": settings().llm_provider if live else "mock",
        "model": (_last["model"] or (models[0] if models else "")) if live else "deterministic-mock",
        "cached": _last["cached"],
        "live": live,
        "fallback_models": models[1:] if live else [],
        "last_source": _last["source"],
        "last_error": _last["error"],
    }


@lru_cache(maxsize=4)
def _gemini_client(key: str):
    from google import genai
    from google.genai import types

    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_S * 1000))


@lru_cache(maxsize=4)
def _groq_client(key: str):
    from groq import Groq

    return Groq(api_key=key, timeout=REQUEST_TIMEOUT_S, max_retries=0)


def _call_gemini(system: str, prompt: str) -> str:
    from google.genai import types

    resp = _gemini_client(settings().llm_api_key).models.generate_content(
        model=_current["model"],
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json", temperature=0.1),
    )
    return resp.text or ""


def _call_groq(system: str, prompt: str) -> str:
    resp = _groq_client(settings().llm_api_key).chat.completions.create(
        model=_current["model"],
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


def classify(e: Exception) -> str:
    """'auth' (stop, use the mock), 'gone' (skip this model for good) or 'busy' (try the next one)."""
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    text = str(e)
    if code in (401, 403) or re.search(r"API_KEY_INVALID|PERMISSION_DENIED|invalid api key|Unauthorized", text, re.I):
        return "auth"
    if code == 404 or "NOT_FOUND" in text or re.search(r"model .*(not found|does not exist|decommissioned)", text, re.I):
        return "gone"
    return "busy"


class _Ping(BaseModel):
    ok: bool


def warm_up() -> str | None:
    """Find a responsive model before the first real request, so a demo's first click isn't
    the one that waits on busy models. Runs in a background thread at startup."""
    if not _live():
        return None
    s = settings()
    for model in chain():
        _current["model"] = model
        try:
            _Ping.model_validate(parse_json(_PROVIDERS[s.llm_provider]("Reply with JSON.", 'Return {"ok": true}')))
            _sticky[s.llm_provider] = model
            log.info("LLM warm-up: %s is responsive", model)
            return model
        except Exception as e:
            if classify(e) == "gone":
                _dead.add(model)
            elif classify(e) == "auth":
                return None
    return None


def _cache_get(key: str, schema: type[T]) -> T | None:
    try:
        hit = db.row("SELECT response FROM llm_cache WHERE key = ?", (key,))
        return schema.model_validate_json(hit["response"]) if hit else None
    except Exception:  # cache is an optimisation; never let it break a call
        return None


def _cache_put(key: str, result: BaseModel) -> None:
    try:
        db.execute("INSERT OR REPLACE INTO llm_cache (key, response) VALUES (?, ?)", (key, result.model_dump_json()))
    except Exception:
        pass


def _run_mock(schema: type[T], mock: Callable[[], dict | T]) -> T:
    out = mock()
    return out if isinstance(out, schema) else schema.model_validate(out)


def generate_json(
    *, system: str, prompt: str, schema: type[T], mock: Callable[[], dict | T]
) -> T:
    if not _live():
        _last.update(source="mock", error=None, model=None, cached=False)
        return _run_mock(schema, mock)

    s = settings()
    key = hashlib.sha256(f"{s.llm_provider}|{schema.__name__}|{system}|{prompt}".encode()).hexdigest()
    if s.llm_cache and (cached := _cache_get(key, schema)):
        _last.update(source=s.llm_provider, error=None, cached=True)
        return cached
    call = _PROVIDERS[s.llm_provider]
    full_system = (
        f"{system}\n\nRespond with ONLY a JSON object (no prose) that validates against "
        f"this JSON schema:\n{json.dumps(schema.model_json_schema())}"
    )
    attempt_prompt = prompt
    last_err: Exception | None = None
    for round_no in range(max(1, s.llm_max_retries)):
        for model in chain():
            _current["model"] = model
            try:
                result = schema.model_validate(parse_json(call(full_system, attempt_prompt)))
                if s.llm_cache:
                    _cache_put(key, result)
                _sticky[s.llm_provider] = model
                _last.update(source=s.llm_provider, error=None, model=model, cached=False)
                return result
            except (ValidationError, ValueError) as e:
                # Bad shape: tell the model exactly what was wrong and try again.
                last_err = e
                attempt_prompt = (
                    f"{prompt}\n\nYour previous reply was invalid JSON for the schema: "
                    f"{str(e)[:600]}\nReturn the corrected JSON object only."
                )
            except Exception as e:  # network, rate limit, busy, retired, auth
                last_err = e
                kind = classify(e)
                log.warning("LLM %s failed (%s): %s", model, kind, str(e)[:200])
                if kind == "auth":
                    _last.update(source="mock-fallback", error=f"Key rejected: {str(e)[:200]}", model=None)
                    return _run_mock(schema, mock)
                if kind == "gone":
                    _dead.add(model)
                    _sticky.pop(s.llm_provider, None) if _sticky.get(s.llm_provider) == model else None
        if round_no + 1 < s.llm_max_retries:
            time.sleep(min(2 ** round_no, 4))  # every model busy: brief pause, then another pass

    log.error("LLM failed on every model, using mock: %s", last_err)
    _last.update(source="mock-fallback", error=str(last_err)[:300], model=None)
    return _run_mock(schema, mock)
