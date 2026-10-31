"""Free-tier resilience: busy models hand over, retired ones are skipped, bad keys fail fast."""
import pytest

from app import llm
from app.extraction import Extraction

OK = '{"summary": "ok", "decisions": [], "action_items": []}'


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"{code} {msg}")
        self.code = code


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "m-new,m-busy,m-good")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls = []

    def install(behaviour):
        def fake(system, prompt):
            model = llm._current["model"]
            calls.append(model)
            outcome = behaviour.get(model, OK)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        monkeypatch.setitem(llm._PROVIDERS, "gemini", fake)
        return calls
    return install


def _go():
    return llm.generate_json(system="s", prompt="p", schema=Extraction, mock=lambda: {"summary": "mock"})


def test_retired_and_busy_models_hand_over_and_winner_sticks(gemini):
    calls = gemini({"m-new": ApiError(404, "NOT_FOUND model retired"), "m-busy": ApiError(503, "UNAVAILABLE")})
    assert _go().summary == "ok"
    assert calls == ["m-new", "m-busy", "m-good"]
    info = llm.provider_info()
    assert info["last_source"] == "gemini" and info["model"] == "m-good"

    calls.clear()
    _go()
    assert calls == ["m-good"]          # sticky: the working model goes first
    assert "m-new" not in llm.chain()   # retired model skipped for the rest of the run


def test_bad_key_goes_straight_to_mock(gemini):
    calls = gemini({"m-new": ApiError(400, "API_KEY_INVALID")})
    assert _go().summary == "mock"
    assert calls == ["m-new"] and "Key rejected" in llm.provider_info()["last_error"]


def test_everything_busy_falls_back_to_mock_after_retries(gemini, monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    busy = ApiError(429, "RESOURCE_EXHAUSTED")
    calls = gemini({"m-new": busy, "m-busy": busy, "m-good": busy})
    assert _go().summary == "mock"
    assert len(calls) == 6 and llm.provider_info()["last_source"] == "mock-fallback"


def test_llm_model_env_goes_first(gemini, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "m-pinned")
    assert llm.chain()[0] == "m-pinned"
    calls = gemini({})
    _go()
    assert calls == ["m-pinned"]


def test_identical_prompt_is_served_from_cache(gemini, monkeypatch):
    from app import db

    db.init_db()
    monkeypatch.setenv("LLM_CACHE", "true")
    calls = gemini({})
    first, second = _go(), _go()
    assert first == second and calls == ["m-new"]       # second answer came from the cache
    assert llm.provider_info()["cached"] and llm.provider_info()["model"] == "m-new"
    db.reset()                                           # demo reset keeps the cache warm
    _go()
    assert calls == ["m-new"]
