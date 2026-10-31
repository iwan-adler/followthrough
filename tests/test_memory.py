import json
from datetime import date

from app import db, llm, memory, workflow
from app.extraction import ExtractedDecision


def _decision(text, topic, speaker="Priya"):
    return ExtractedDecision(text=text, topic=topic, speaker=speaker, confidence=0.9)


def test_prefilter_matches_topic_or_keywords():
    actives = [
        {"id": 1, "topic": "launch date", "text": "We launch on November 23rd"},
        {"id": 2, "topic": "database", "text": "We're going with Postgres"},
        {"id": 3, "topic": "go-to-market", "text": "Launch announcement goes out on November 23rd"},
    ]
    ids = [c["id"] for c in memory.candidates(_decision("Launch is December 7th", "launch date"), actives)]
    assert ids[0] == 1 and 2 not in ids


def test_mock_judge_flags_changed_values_only():
    old = {"id": 1, "topic": "pricing", "text": "Pricing is $25 a month per seat",
           "meeting_title": "Kickoff", "meeting_date": "2026-10-19"}
    same = memory.mock_judge([(0, _decision("Pricing stays at $25 a seat", "pricing"), old)])
    changed = memory.mock_judge([(0, _decision("Pricing is $35 a seat", "pricing"), old)])
    moved = memory.mock_judge([(0, _decision("We're moving pricing to $35", "pricing"), old)])
    assert same.judgments[0].verdict == "none"
    assert changed.judgments[0].verdict == "contradiction"
    assert "$25" in changed.judgments[0].explanation and "$35" in changed.judgments[0].explanation
    assert moved.judgments[0].verdict == "supersession"


def test_demo_conflict_flow_end_to_end(client):
    loaded = client.post("/api/demo/load").json()["meetings"]
    assert [m["status"] for m in loaded] == ["confirmed", "staged"]
    staged_id = loaded[1]["id"]

    meeting = client.get(f"/api/meetings/{staged_id}").json()
    by_topic = {c["old_topic"]: c for c in meeting["conflicts"]}
    assert set(by_topic) == {"database", "launch date"}  # unchanged pricing is not flagged
    assert by_topic["database"]["kind"] == "contradiction"
    assert "Postgres" in by_topic["database"]["old_text"] and "Firebase" in by_topic["database"]["new_decision"]

    # Gate: cannot confirm with open conflicts.
    r = client.post(f"/api/meetings/{staged_id}/confirm")
    assert r.status_code == 409 and "Resolve 2 conflict" in r.json()["detail"]
    assert client.get("/api/memory").json()[-1]["meeting_id"] == loaded[0]["id"]  # nothing new committed

    assert client.post(f"/api/conflicts/{by_topic['database']['id']}/resolve", json={"resolution": "bogus"}).status_code == 400
    client.post(f"/api/conflicts/{by_topic['database']['id']}/resolve", json={"resolution": "supersede"})
    client.post(f"/api/conflicts/{by_topic['launch date']['id']}/resolve", json={"resolution": "keep"})
    assert client.post(f"/api/meetings/{staged_id}/confirm").status_code == 200

    mem = {(d["topic"], d["text"]): d for d in client.get("/api/memory").json()}
    status = {k: v["status"] for k, v in mem.items()}
    assert status[("database", "We're going with Postgres")] == "superseded"
    assert status[("database", "We're using Firebase for the backend")] == "active"
    assert status[("launch date", "Launch is December 7th")] == "superseded"  # kept the old one
    assert [v["status"] for k, v in mem.items() if k[0] == "pricing"] == ["active", "active"]

    # Resolutions are frozen once confirmed.
    r = client.post(f"/api/conflicts/{by_topic['database']['id']}/resolve", json={"resolution": "dismiss"})
    assert r.status_code == 409
    assert client.post(f"/api/meetings/{staged_id}/confirm").status_code == 409


def test_redetect_keeps_chosen_resolution():
    db.init_db()
    workflow.load_demo()
    staged = next(m for m in db.rows("SELECT * FROM meetings WHERE status = 'staged'"))
    first = memory.list_conflicts(staged["id"])[0]
    memory.resolve_conflict(first["id"], "dismiss")
    decisions = [ExtractedDecision(**d) for d in json.loads(staged["extraction"])["decisions"]]
    again = memory.detect_conflicts(staged["id"], date.fromisoformat(staged["meeting_date"]), decisions)
    assert {c["old_decision_id"]: c["resolution"] for c in again}[first["old_decision_id"]] == "dismiss"


def test_llm_judge_output_is_validated_against_candidates(monkeypatch):
    db.init_db()
    workflow.load_demo(confirm_all=True)
    old = memory.active_decisions()[0]
    mid = db.create_meeting("Live", "2026-10-30", "x", [], "", {})
    monkeypatch.setenv("LLM_API_KEY", "fake")
    reply = {"judgments": [
        {"new_index": 0, "old_id": old["id"], "verdict": "supersession", "explanation": "Moved."},
        {"new_index": 0, "old_id": 999, "verdict": "contradiction", "explanation": "Hallucinated id."},
    ]}
    monkeypatch.setitem(llm._PROVIDERS, "gemini", lambda system, prompt: json.dumps(reply))
    out = memory.detect_conflicts(mid, date(2026, 10, 30), [_decision(old["text"] + " changed", old["topic"])])
    assert [(c["old_decision_id"], c["kind"]) for c in out] == [(old["id"], "supersession")]
