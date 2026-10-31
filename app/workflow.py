"""Meeting lifecycle: stage (extract + detect conflicts) -> review -> confirm (commit to memory)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from . import db, llm, memory
from .extraction import Extraction, extract, postprocess
from .speech import parse_transcript

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"


class GateError(Exception):
    """The confirmation gate refused: unresolved conflicts, already confirmed, etc."""


def meeting_view(meeting_id: int) -> dict:
    m = db.get_meeting(meeting_id)
    if not m:
        raise LookupError("Meeting not found.")
    m["conflicts"] = memory.list_conflicts(meeting_id)
    m["open_conflicts"] = sum(1 for c in m["conflicts"] if not c["resolution"])
    return m


def stage_meeting(transcript: str, meeting_date: date, title: str) -> dict:
    utterances, ex = extract(transcript, meeting_date, title, known_topics=memory.known_topics())
    extraction_source = llm.provider_info()["last_source"]
    speakers = list(dict.fromkeys(u.speaker for u in utterances))
    meeting_id = db.create_meeting(title, meeting_date.isoformat(), transcript, speakers,
                                   ex.summary, ex.model_dump())
    memory.detect_conflicts(meeting_id, meeting_date, ex.decisions)
    view = meeting_view(meeting_id)
    view["utterances"] = [{"speaker": u.speaker, "text": u.text} for u in utterances]
    view["llm_source"] = extraction_source
    return view


def _require_staged(meeting_id: int) -> dict:
    m = db.get_meeting(meeting_id)
    if not m:
        raise LookupError("Meeting not found.")
    if m["status"] != "staged":
        raise GateError("This meeting is already confirmed and can no longer be edited.")
    return m


def update_draft(meeting_id: int, extraction: Extraction, title: str | None = None) -> dict:
    """Save inline edits from the review screen and re-check conflicts."""
    m = _require_staged(meeting_id)
    meeting_date = date.fromisoformat(m["meeting_date"])
    extraction.decisions = [d for d in extraction.decisions if d.text.strip()]
    extraction.action_items = [a for a in extraction.action_items if a.title.strip()]
    ex = postprocess(extraction, parse_transcript(m["transcript"]), meeting_date)
    db.execute("UPDATE meetings SET extraction = ?, title = ? WHERE id = ?",
               (json.dumps(ex.model_dump()), (title or m["title"]).strip(), meeting_id))
    memory.detect_conflicts(meeting_id, meeting_date, ex.decisions)
    return meeting_view(meeting_id)


def discard_draft(meeting_id: int) -> None:
    _require_staged(meeting_id)
    with db.connect() as conn:
        conn.execute("DELETE FROM conflicts WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))


def confirm_meeting(meeting_id: int) -> dict:
    """The confirmation gate: nothing reaches memory (or, later, the tracker) before this."""
    m = meeting_view(meeting_id)
    if m["status"] == "confirmed":
        raise GateError("This meeting is already confirmed.")
    if m["open_conflicts"]:
        raise GateError(f"Resolve {m['open_conflicts']} conflict(s) with earlier decisions before confirming.")
    ex = Extraction.model_validate(m["extraction"])
    with db.connect() as conn:
        decision_ids = memory.commit_decisions(conn, meeting_id, ex.decisions)
        action_ids = [
            conn.execute(
                "INSERT INTO action_items (meeting_id, title, owner, deadline, confidence) VALUES (?, ?, ?, ?, ?)",
                (meeting_id, a.title, a.owner, a.deadline, a.confidence),
            ).lastrowid
            for a in ex.action_items
        ]
        conn.execute("UPDATE meetings SET status = 'confirmed', confirmed_at = datetime('now') WHERE id = ?",
                     (meeting_id,))
    return {"meeting_id": meeting_id, "decision_ids": decision_ids, "action_item_ids": action_ids}


def load_demo(confirm_all: bool = False) -> dict:
    """Reset and load the scripted demo.

    Default: the kickoff is confirmed and the next meeting (which quietly contradicts it) is
    left staged for review; the sprint planning meeting is left out so it can be dictated live.
    confirm_all: load all three and confirm them, superseding old decisions on conflict."""
    db.reset()
    manifest = json.loads((SEED_DIR / "meetings.json").read_text(encoding="utf-8"))
    loaded = []
    for n, item in enumerate(manifest if confirm_all else manifest[:2], 1):
        transcript = (SEED_DIR / item["file"]).read_text(encoding="utf-8")
        view = stage_meeting(transcript, date.today() - timedelta(days=item["days_ago"]), item["title"])
        confirm = confirm_all or n == 1
        if confirm:
            for c in view["conflicts"]:
                memory.resolve_conflict(c["id"], "supersede")
            confirm_meeting(view["id"])
        loaded.append({"id": view["id"], "title": item["title"],
                       "status": "confirmed" if confirm else "staged", "conflicts": len(view["conflicts"])})
    return {"meetings": loaded}


def demo_transcripts() -> list[dict]:
    manifest = json.loads((SEED_DIR / "meetings.json").read_text(encoding="utf-8"))
    return [{**item, "meeting_date": (date.today() - timedelta(days=item["days_ago"])).isoformat(),
             "transcript": (SEED_DIR / item["file"]).read_text(encoding="utf-8")} for item in manifest]
