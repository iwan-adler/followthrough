from datetime import date
from pathlib import Path

import pytest

from app import llm
from app.dates import resolve_date
from app.extraction import Extraction, extract
from app.speech import clean_speech, parse_transcript, resolve_self_corrections

SAMPLE = (Path(__file__).parent / "fixtures" / "sample_meeting.txt").read_text()
MON = date(2026, 11, 2)  # a Monday


@pytest.mark.parametrize("phrase,expected", [
    ("by tomorrow", date(2026, 11, 3)),
    ("Friday", date(2026, 11, 6)),
    ("next Friday", date(2026, 11, 13)),
    ("end of week", date(2026, 11, 6)),
    ("end of month", date(2026, 11, 30)),
    ("in two weeks", date(2026, 11, 16)),
    ("Nov 20th", date(2026, 11, 20)),
    ("the 15th", date(2026, 11, 15)),
    ("Jan 5", date(2027, 1, 5)),
    ("2026-12-01", date(2026, 12, 1)),
    ("whenever", None),
])
def test_resolve_date(phrase, expected):
    assert resolve_date(phrase, MON) == expected


def test_bare_weekday_same_day_means_next_week():
    assert resolve_date("Monday", MON) == date(2026, 11, 9)


@pytest.mark.parametrize("raw,expected", [
    ("we launch Friday... no wait, Monday the 17th, ok", "we launch Monday the 17th, ok"),
    ("Pricing is $29 a month, no wait, $39 a month per seat.", "Pricing is $39 a month per seat."),
    ("we're going with Mongo, sorry, Postgres.", "we're going with Postgres."),
])
def test_self_corrections_keep_final_intent(raw, expected):
    assert resolve_self_corrections(raw) == expected


def test_clean_speech_drops_filler():
    assert clean_speech("Um, so, like, we should uh ship the the beta") == "so, we should ship the beta"


def test_parse_transcript_speakers_and_unknown():
    u = parse_transcript("so we kind of agreed on stuff\nPriya: hi\ncontinuing thought\n[00:01:02] Raj Patel: ok")
    assert [(x.speaker, x.text) for x in u] == [
        ("Unknown", "so we kind of agreed on stuff"),
        ("Priya", "hi continuing thought"),
        ("Raj Patel", "ok"),
    ]


def test_parse_vtt():
    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\n<v Priya>Launch is Monday.</v>\n"
    assert [(x.speaker, x.text) for x in parse_transcript(vtt)] == [("Priya", "Launch is Monday.")]


def test_extract_sample_meeting():
    utterances, ex = extract(SAMPLE, MON, "Launch sync")
    assert {u.speaker for u in utterances} == {"Priya", "Raj", "Sam"}

    by_topic = {d.topic: d for d in ex.decisions}
    assert "Monday the 17th" in by_topic["launch date"].text and "Friday" not in by_topic["launch date"].text
    assert "Postgres" in by_topic["database"].text and "Mongo" not in by_topic["database"].text
    assert "$39" in by_topic["pricing"].text and "$29" not in by_topic["pricing"].text

    tasks = {a.title: a for a in ex.action_items}
    plan = tasks["Draft the migration plan"]
    assert (plan.owner, plan.deadline, plan.needs_owner) == ("Raj", "2026-11-13", False)
    staging = tasks["Set up the staging environment"]
    assert (staging.owner, staging.deadline) == ("Sam", "2026-11-03")
    orphan = tasks["Update the landing page copy"]
    assert orphan.needs_owner and orphan.needs_deadline


def test_unlabeled_dictation_is_unknown():
    utterances, ex = extract("I'll send the investor update by Friday.", MON)
    assert utterances[0].speaker == "Unknown"
    assert ex.action_items[0].needs_owner and ex.action_items[0].deadline == "2026-11-06"


def test_llm_retries_then_validates(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    replies = iter(['{"oops": true}', '```json\n{"summary": "ok", "decisions": [], "action_items": []}\n```'])
    monkeypatch.setitem(llm._PROVIDERS, "groq", lambda system, prompt: next(replies))
    out = llm.generate_json(system="s", prompt="p", schema=Extraction, mock=lambda: {"summary": "mock"})
    assert out.summary == "ok" and llm.provider_info()["last_source"] == "groq"


def test_llm_falls_back_to_mock_when_provider_down(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")

    def boom(system, prompt):
        raise ConnectionError("down")

    monkeypatch.setitem(llm._PROVIDERS, "gemini", boom)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    out = llm.generate_json(system="s", prompt="p", schema=Extraction, mock=lambda: {"summary": "mock"})
    assert out.summary == "mock" and llm.provider_info()["last_source"] == "mock-fallback"


def test_extract_api_stages_meeting(client):
    r = client.post("/api/extract", json={"transcript": SAMPLE, "title": "Launch sync", "meeting_date": "2026-11-02"})
    assert r.status_code == 200
    body = r.json()
    assert body["speakers"] == ["Priya", "Raj", "Sam"]
    assert len(body["extraction"]["decisions"]) == 3
    m = client.get(f"/api/meetings/{body['meeting_id']}").json()
    assert m["status"] == "staged"


def test_extract_api_rejects_empty(client):
    assert client.post("/api/extract", json={"transcript": "   "}).status_code == 400
