"""Spoken edits to a draft, and grounded questions over memory."""
import json
from datetime import date, timedelta

from app import db, llm, workflow
from app.dates import resolve_date


def _staged(client):
    return client.post("/api/demo/load").json()["meetings"][1]["id"]


def _task(view, fragment):
    return next(a for a in view["extraction"]["action_items"] if fragment in a["title"])


def test_spoken_edit_assigns_and_reschedules_with_pronoun(client):
    sid = _staged(client)
    r = client.post(f"/api/meetings/{sid}/command",
                    json={"command": "Um, make Sam own the launch announcement draft and push it to Friday"}).json()
    assert len(r["changes"]) == 2
    t = _task(r, "launch announcement")
    friday = resolve_date("Friday", date.today()).isoformat()
    assert (t["owner"], t["deadline"]) == ("Sam", friday)
    # Saved: a reload shows the same thing (the phrase is not re-read against the meeting date).
    assert _task(client.get(f"/api/meetings/{sid}").json(), "launch announcement")["deadline"] == friday


def test_spoken_add_rename_delete(client):
    sid = _staged(client)
    client.post(f"/api/meetings/{sid}/command", json={"command": "add a task for Raj to update the infra diagram by Wednesday"})
    client.post(f"/api/meetings/{sid}/command", json={"command": "rename the auth flow to Migrate auth to Firebase"})
    r = client.post(f"/api/meetings/{sid}/command", json={"command": "delete the walk Mei through them task"}).json()
    titles = {a["title"]: a["owner"] for a in r["extraction"]["action_items"]}
    assert titles["Update the infra diagram"] == "Raj"
    assert "Migrate auth to Firebase" in titles and "Walk Mei through them" not in titles


def test_unrecognised_command_changes_nothing(client):
    sid = _staged(client)
    before = client.get(f"/api/meetings/{sid}").json()["extraction"]
    r = client.post(f"/api/meetings/{sid}/command", json={"command": "what a great meeting"}).json()
    assert r["changes"] == [] and r["extraction"] == before


def test_commands_refused_after_confirm(client):
    sid = _staged(client)
    for c in client.get(f"/api/meetings/{sid}").json()["conflicts"]:
        client.post(f"/api/conflicts/{c['id']}/resolve", json={"resolution": "dismiss"})
    client.post(f"/api/meetings/{sid}/confirm")
    assert client.post(f"/api/meetings/{sid}/command", json={"command": "delete the auth flow"}).status_code == 409


def test_llm_edit_plan_is_applied_deterministically(client, monkeypatch):
    sid = _staged(client)
    monkeypatch.setenv("LLM_API_KEY", "fake")
    plan = {"ops": [{"op": "set_owner", "index": 1, "value": "Priya"},
                    {"op": "set_deadline", "index": 1, "value": "tomorrow"},
                    {"op": "delete_task", "index": 99}]}  # out of range: ignored
    monkeypatch.setitem(llm._PROVIDERS, "gemini", lambda system, prompt: json.dumps(plan))
    r = client.post(f"/api/meetings/{sid}/command", json={"command": "Priya takes the second one, due tomorrow"}).json()
    t = r["extraction"]["action_items"][1]
    assert (t["owner"], t["deadline"]) == ("Priya", (date.today() + timedelta(days=1)).isoformat())
    assert len(r["changes"]) == 2


def test_ask_memory_explains_history_with_citations(client):
    db.init_db()
    workflow.load_demo(confirm_all=True)
    r = client.post("/api/ask", json={"question": "When did we switch to Firebase, and who decided?"}).json()
    assert "Firebase" in r["answer"] and "Postgres" in r["answer"] and "Raj" in r["answer"]
    assert [d["status"] for d in r["decisions"]] == ["active", "superseded"]

    owes = client.post("/api/ask", json={"question": "What does Diego owe?"}).json()
    assert owes["tasks"] and all(t["owner"] == "Diego" for t in owes["tasks"])
    nothing = client.post("/api/ask", json={"question": "Did we talk about the mobile app?"}).json()
    assert "couldn't find" in nothing["answer"] and nothing["decisions"] == []


def test_ask_drops_invented_citations(client, monkeypatch):
    db.init_db()
    workflow.load_demo(confirm_all=True)
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setitem(llm._PROVIDERS, "gemini", lambda system, prompt: json.dumps(
        {"answer": "Launch is Dec 7.", "decision_ids": [1, 9999], "task_ids": [4242]}))
    r = client.post("/api/ask", json={"question": "launch?"}).json()
    assert [d["id"] for d in r["decisions"]] == [1] and r["tasks"] == [] and r["source"] == "gemini"
