import json

from app import db, digest, llm, workflow


def _full_demo():
    db.init_db()
    workflow.load_demo(confirm_all=True)


def test_digest_facts_for_diego():
    _full_demo()
    d = digest.generate("Diego")
    assert d["source"] == "mock"
    titles = lambda key: {t["title"] for t in d[key]}
    assert "Set up the Postgres schema and migrations" in titles("overdue")
    assert "Write the security rules" in titles("due_this_week")
    assert titles("overdue").isdisjoint(titles("due_this_week"))

    stale = {t["title"]: t["reason"] for t in d["possibly_obsolete"]}
    assert "Postgres" in stale["Set up the Postgres schema and migrations"]
    assert "Firebase" in stale["Set up the Postgres schema and migrations"]
    assert "Move the auth flow over to Firebase" not in stale

    changed = {x["topic"]: x for x in d["decisions"]}
    assert "Postgres" in changed["database"]["replaced"]
    n = d["narrative"]
    assert n["subject"].startswith("Diego's week:")
    assert any(h.startswith("Changed (database)") for h in n["highlights"])
    assert any(h.startswith("May be obsolete") for h in n["heads_up"])


def test_unchanged_pricing_task_not_flagged_obsolete():
    _full_demo()
    assert digest.generate("Mei")["possibly_obsolete"] == []


def test_people_and_quiet_digest():
    _full_demo()
    assert digest.people() == ["Diego", "Mei", "Priya", "Raj", "Sam"]
    quiet = digest.generate("Nobody")
    assert quiet["open_items"] == [] and quiet["narrative"]["summary"].startswith("Quiet week")


def test_unresolved_conflicts_reach_the_people_involved():
    db.init_db()
    workflow.load_demo()  # meeting 2 staged with open conflicts
    raj = digest.generate("Raj")
    assert any("Postgres" in c["old_text"] for c in raj["unresolved_conflicts"])
    assert any(h.startswith("Unresolved conflict") for h in raj["narrative"]["heads_up"])


def test_llm_narrative_used_when_valid(monkeypatch):
    _full_demo()
    monkeypatch.setenv("LLM_API_KEY", "fake")
    seen = {}

    def fake(system, prompt):
        seen["prompt"] = prompt
        return json.dumps({"subject": "Your week, Diego", "summary": "Busy one.",
                           "highlights": ["Firebase replaced Postgres."], "heads_up": []})

    monkeypatch.setitem(llm._PROVIDERS, "gemini", fake)
    d = digest.generate("Diego")
    assert d["narrative"]["subject"] == "Your week, Diego" and d["source"] == "gemini"
    assert "Set up the Postgres schema" in seen["prompt"]  # facts are passed to the model


def test_digest_api_and_markdown(client):
    client.post("/api/demo/load")
    assert "Raj" in client.get("/api/digest/people").json()
    md = client.get("/api/digest/Raj/markdown").text
    assert md.startswith("# Raj's week:") and "## Heads up" in md
    tasks = client.get("/api/tasks").json()
    assert all("possibly_obsolete" in t for t in tasks)
