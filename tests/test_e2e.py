"""End-to-end: stage -> conflict flagged -> confirm gate blocks posting -> confirm posts
-> reminders -> digest. The LLM provider and GitHub are mocked at their network edge,
so the real prompt building, JSON parsing, validation and post-processing all run."""
import json
from datetime import date, timedelta

import pytest

from app import digest, extraction, llm, memory, tracker

TODAY = date.today()
KICKOFF = """Priya: Okay so um, the launch, we're launching November 20th... no wait, November 23rd.
Raj: For the database we're going with Postgres. Diego, can you set up the schema by yesterday?"""
SYNC = """Raj: Uh, Firebase just gets us there faster, so we're using Firebase.
Priya: Launch is December 7th. Diego, move auth to Firebase by tomorrow."""


def fake_llm(system, prompt):
    """Answers like a well-behaved model, keyed on which prompt it received."""
    if system.startswith(extraction.SYSTEM_PROMPT[:40]):
        if "November" in prompt:
            return json.dumps({
                "summary": "Kickoff.",
                "decisions": [
                    {"text": "Launch on November 23rd", "topic": "launch date", "speaker": "Priya", "confidence": 0.9},
                    {"text": "Use Postgres for the database", "topic": "database", "speaker": "Raj", "confidence": 0.9},
                ],
                "action_items": [{"title": "Set up the Postgres schema", "owner": "Diego",
                                  "deadline_text": "yesterday", "deadline": (TODAY - timedelta(days=1)).isoformat(),
                                  "speaker": "Raj", "confidence": 0.85}],
            })
        return json.dumps({
            "summary": "Infra sync.",
            "decisions": [
                {"text": "Use Firebase for the backend", "topic": "database", "speaker": "Raj", "confidence": 0.8},
                {"text": "Launch on December 7th", "topic": "launch date", "speaker": "Priya", "confidence": 0.85},
            ],
            "action_items": [{"title": "Move auth to Firebase", "owner": "diego", "deadline_text": "tomorrow",
                              "deadline": None, "speaker": "Priya", "confidence": 0.9}],
        })
    if system.startswith(memory.JUDGE_SYSTEM[:40]):
        pairs = [line.split() for line in prompt.splitlines() if line.startswith("PAIR")]
        return json.dumps({"judgments": [
            {"new_index": int(p[1].split("=")[1]), "old_id": int(p[2].split("=")[1]),
             "verdict": "contradiction", "shared_subject": "decision", "explanation": "Silently reverses the kickoff decision."}
            for p in pairs]})
    if system.startswith(digest.DIGEST_SYSTEM[:40]):
        facts = json.loads(prompt.split("\n", 1)[1])
        return json.dumps({"subject": f"Your week, {facts['person']}",
                           "summary": f"{len(facts['open_items'])} open items.",
                           "highlights": [d["text"] for d in facts["decisions"]],
                           "heads_up": [t["title"] for t in facts["possibly_obsolete"]]})
    raise AssertionError("unexpected prompt")


@pytest.fixture
def mocked_edges(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setitem(llm._PROVIDERS, "groq", fake_llm)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPO", "acme/launch")
    issues = []

    def fake_github(method, path, json):
        issues.append((method, path, json))
        return {"html_url": f"https://github.com/acme/launch/issues/{len(issues)}", "number": len(issues)}

    monkeypatch.setattr(tracker, "_github", fake_github)
    return issues


def test_meeting_to_execution_end_to_end(client, mocked_edges):
    issues = mocked_edges

    # 1. Stage and confirm the kickoff: decisions enter memory, one issue is created.
    kickoff = client.post("/api/extract", json={"transcript": KICKOFF, "title": "Kickoff",
                                                "meeting_date": (TODAY - timedelta(days=6)).isoformat()}).json()
    assert kickoff["llm_source"] == "groq" and kickoff["speakers"] == ["Priya", "Raj"]
    assert kickoff["conflicts"] == [] and issues == []
    assert client.post(f"/api/meetings/{kickoff['id']}/confirm").status_code == 200
    assert len(issues) == 1

    # 2. Stage a later meeting that quietly reverses two decisions: both are flagged.
    sync = client.post("/api/extract", json={"transcript": SYNC, "title": "Infra sync",
                                             "meeting_date": (TODAY - timedelta(days=1)).isoformat()}).json()
    assert {(c["old_text"], c["new_decision"]) for c in sync["conflicts"]} == {
        ("Use Postgres for the database", "Use Firebase for the backend"),
        ("Launch on November 23rd", "Launch on December 7th"),
    }
    assert all(c["kind"] == "contradiction" for c in sync["conflicts"])
    task = sync["extraction"]["action_items"][0]
    assert task["owner"] == "Diego" and task["deadline"] == TODAY.isoformat()  # "tomorrow" said yesterday

    # 3. The gate: nothing is posted while conflicts are open.
    blocked = client.post(f"/api/meetings/{sync['id']}/confirm")
    assert blocked.status_code == 409 and len(issues) == 1
    assert [t["title"] for t in client.get("/api/tasks").json()] == ["Set up the Postgres schema"]

    # 4. Resolve, confirm: the issue is posted and memory updates.
    for c in sync["conflicts"]:
        client.post(f"/api/conflicts/{c['id']}/resolve", json={"resolution": "supersede"})
    confirmed = client.post(f"/api/meetings/{sync['id']}/confirm").json()
    assert len(issues) == 2 and issues[1][2]["title"] == "Move auth to Firebase"
    assert "**Owner:** Diego" in issues[1][2]["body"]
    assert confirmed["tasks"][0]["tracker_url"] == "https://github.com/acme/launch/issues/2"
    statuses = {d["text"]: d["status"] for d in client.get("/api/memory").json()}
    assert statuses["Use Postgres for the database"] == "superseded"
    assert statuses["Use Firebase for the backend"] == "active"

    # 5. Reminders: yesterday's task is overdue, today's is due soon.
    created = client.post("/api/reminders/run").json()["created"]
    assert {(n["person"], n["kind"]) for n in created} == {("Diego", "overdue"), ("Diego", "due_soon")}
    assert client.post("/api/reminders/run").json()["created"] == []  # no duplicates

    # 6. Digest: LLM narrative over deterministic facts, incl. the now-obsolete Postgres task.
    d = client.get("/api/digest/Diego").json()
    assert d["source"] == "groq" and d["narrative"]["subject"] == "Your week, Diego"
    assert "Set up the Postgres schema" in d["narrative"]["heads_up"]
    assert {t["title"] for t in d["overdue"]} == {"Set up the Postgres schema"}
    assert {t["title"] for t in d["due_this_week"]} == {"Move auth to Firebase"}


def test_same_story_without_any_keys(client):
    """No LLM key and no GitHub token: the deterministic mock and mock tracker carry the demo."""
    meetings = client.post("/api/demo/load").json()["meetings"]
    staged = client.get(f"/api/meetings/{meetings[1]['id']}").json()
    assert len(staged["conflicts"]) == 2
    assert client.post(f"/api/meetings/{staged['id']}/confirm").status_code == 409
    for c in staged["conflicts"]:
        client.post(f"/api/conflicts/{c['id']}/resolve", json={"resolution": "supersede"})
    posted = client.post(f"/api/meetings/{staged['id']}/confirm").json()["tasks"]
    assert posted and all(t["tracker_url"].startswith("mock://") for t in posted)

    sample = client.get("/api/demo/transcripts").json()[2]
    sprint = client.post("/api/extract", json=sample).json()
    assert [c["said"] for c in sprint["voice"]["corrections"]] == ["Wednesday", "the launch webinar"]
    assert client.post(f"/api/meetings/{sprint['id']}/confirm").status_code == 200

    assert client.post("/api/reminders/run").json()["created"]
    assert client.get("/api/digest/Diego").json()["possibly_obsolete"]
