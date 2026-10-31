"""How real Wispr Flow dictation arrives: one paragraph, recap phrasing, live preview."""
from datetime import date

from app.extraction import extract

FRI = date(2026, 10, 30)

RECAP = ("Okay so recap of today's sync. Priya said the launch is moving to December 7th. "
         "Diego said he'll write the security rules by Wednesday, actually no, Tuesday. "
         "Mei volunteered to send the beta invite email by Monday. "
         "Someone needs to set up error monitoring.")


def test_recap_style_dictation_assigns_reported_owners():
    _, ex = extract(RECAP, FRI)
    tasks = {a.title: a for a in ex.action_items}
    assert (tasks["Write the security rules"].owner, tasks["Write the security rules"].deadline) == ("Diego", "2026-11-03")
    assert tasks["Send the beta invite email"].owner == "Mei"
    assert tasks["Set up error monitoring"].needs_owner
    assert ex.decisions[0].speaker == "Priya" and ex.decisions[0].text.startswith("The launch is moving")


def test_inline_speaker_labels_in_one_paragraph():
    u, ex = extract("Priya: So the launch is December 7th now. Diego: I will write the rules by Tuesday. "
                    "Sam, can you finish the empty states by Friday?", FRI)
    assert [x.speaker for x in u] == ["Priya", "Diego"]
    assert {a.title: a.owner for a in ex.action_items} == {
        "Write the rules": "Diego", "Finish the empty states": "Sam"}


def test_live_preview_warns_about_conflicts_without_saving(client):
    client.post("/api/demo/load")
    before = client.get("/api/meetings").json()
    p = client.post("/api/preview", json={"transcript": "Priya: Marketing wants time, so launch is December 7th. "
                                                        "Diego: I'll ship it by Wednesday... no wait, Tuesday."}).json()
    assert p["speakers"] == ["Priya", "Diego"]
    assert p["conflicts"] and "November 23rd" in p["conflicts"][0]["explanation"]
    assert p["voice"]["corrections"] == [{"speaker": "Diego", "said": "Wednesday", "meant": "Tuesday"}]
    assert client.get("/api/meetings").json() == before  # nothing staged
    assert client.post("/api/preview", json={"transcript": "  "}).json()["decisions"] == []
