from app import db


def _staged(client):
    loaded = client.post("/api/demo/load").json()["meetings"]
    return client.get(f"/api/meetings/{loaded[1]['id']}").json()


def _save(client, m, **changes):
    body = {"extraction": m["extraction"], "title": changes.pop("title", m["title"])}
    r = client.put(f"/api/meetings/{m['id']}/draft", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_inline_edits_are_saved_and_normalised(client):
    m = _staged(client)
    task = m["extraction"]["action_items"][0]
    task.update(owner="", deadline=None, deadline_text=None)
    m["extraction"]["action_items"].append({"title": "  ", "confidence": 1})  # blank row is dropped
    m["extraction"]["action_items"].append({"title": "Book the launch venue", "owner": "mei",
                                            "deadline": "2026-11-20", "confidence": 1})
    saved = _save(client, m, title="Design sync (edited)")

    items = saved["extraction"]["action_items"]
    assert saved["title"] == "Design sync (edited)"
    assert items[0]["needs_owner"] and items[0]["needs_deadline"]
    assert items[-1]["owner"] == "Mei" and items[-1]["deadline"] == "2026-11-20"  # matched to speaker
    assert len(items) == len(m["extraction"]["action_items"]) - 1


def test_editing_a_decision_rechecks_conflicts(client):
    m = _staged(client)
    by_topic = {c["old_topic"]: c for c in m["conflicts"]}
    client.post(f"/api/conflicts/{by_topic['launch date']['id']}/resolve", json={"resolution": "supersede"})

    db_decision = next(d for d in m["extraction"]["decisions"] if d["topic"] == "database")
    db_decision["text"] = "We're sticking with Postgres for the backend"
    saved = _save(client, m)

    assert {c["old_topic"] for c in saved["conflicts"]} == {"launch date"}  # Firebase clash gone
    assert saved["conflicts"][0]["resolution"] == "supersede"  # unchanged decision keeps its answer
    assert saved["open_conflicts"] == 0


def test_confirm_uses_edited_values_and_freezes_draft(client):
    m = _staged(client)
    for c in m["conflicts"]:
        client.post(f"/api/conflicts/{c['id']}/resolve", json={"resolution": "dismiss"})
    m["extraction"]["action_items"][0]["owner"] = "Priya"
    _save(client, m)

    assert client.post(f"/api/meetings/{m['id']}/confirm").status_code == 200
    owners = [r["owner"] for r in db.rows("SELECT owner FROM action_items WHERE meeting_id = ?", (m["id"],))]
    assert owners[0] == "Priya"

    r = client.put(f"/api/meetings/{m['id']}/draft", json={"extraction": m["extraction"]})
    assert r.status_code == 409
    assert client.delete(f"/api/meetings/{m['id']}").status_code == 409


def test_discard_draft_removes_it(client):
    m = _staged(client)
    assert client.delete(f"/api/meetings/{m['id']}").status_code == 200
    assert client.get(f"/api/meetings/{m['id']}").status_code == 404
    assert client.get("/api/conflicts").json() == []
