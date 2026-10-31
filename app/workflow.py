"""Meeting lifecycle: stage (extract + detect conflicts) -> review -> confirm (commit to memory)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from . import commands, db, digest, llm, memory, tracker
from .extraction import Extraction, extract, mock_extract, postprocess
from .speech import parse_transcript, speakers, voice_report

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"


class GateError(Exception):
    """The confirmation gate refused: unresolved conflicts, already confirmed, etc."""


def meeting_view(meeting_id: int) -> dict:
    m = db.get_meeting(meeting_id)
    if not m:
        raise LookupError("Meeting not found.")
    m["conflicts"] = [{**c, "affected_tasks": memory.conflict_impact(c)} for c in memory.list_conflicts(meeting_id)]
    m["voice"] = voice_report(parse_transcript(m["transcript"]))
    m["open_conflicts"] = sum(1 for c in m["conflicts"] if not c["resolution"])
    return m


def stage_meeting(transcript: str, meeting_date: date, title: str) -> dict:
    utterances, ex = extract(transcript, meeting_date, title,
                             known_topics=memory.known_topics(), known_people=digest.people())
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
    ex = postprocess(extraction, parse_transcript(m["transcript"]), meeting_date, digest.people())
    db.execute("UPDATE meetings SET extraction = ?, title = ? WHERE id = ?",
               (json.dumps(ex.model_dump()), (title or m["title"]).strip(), meeting_id))
    memory.detect_conflicts(meeting_id, meeting_date, ex.decisions)
    return meeting_view(meeting_id)


def apply_command(meeting_id: int, command: str) -> dict:
    """Apply a spoken edit ("make Sam own the landing page and push it to Friday") to a draft."""
    m = _require_staged(meeting_id)
    today = date.today()  # an edit spoken now means "Friday" from today, not from the meeting
    ex = Extraction.model_validate(m["extraction"])
    edited, changes = commands.apply(ex, commands.plan(command, ex, today), today)
    view = update_draft(meeting_id, edited) if changes else meeting_view(meeting_id)
    return {**view, "changes": changes, "llm_source": llm.provider_info()["last_source"]}


def discard_draft(meeting_id: int) -> None:
    _require_staged(meeting_id)
    with db.connect() as conn:
        conn.execute("DELETE FROM conflicts WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))


def preview(transcript: str, meeting_date: date) -> dict:
    """Instant, side-effect-free read of a transcript while it is being dictated.

    Uses only the deterministic rules (no LLM call, so it can run on every pause in speech)
    and checks the decisions it hears against memory, so a contradiction shows up live."""
    utterances = parse_transcript(transcript)
    if not utterances:
        return {"speakers": [], "voice": voice_report([]), "decisions": [], "action_items": [], "conflicts": []}
    ex = postprocess(mock_extract(utterances, meeting_date), utterances, meeting_date, digest.people())
    actives = memory.active_decisions()
    pairs = [(i, d, old) for i, d in enumerate(ex.decisions) for old in memory.candidates(d, actives)]
    flagged = [j for j in memory.mock_judge(pairs).judgments if j.verdict != "none"] if pairs else []
    olds = {old["id"]: old for _, _, old in pairs}
    return {
        "speakers": speakers(utterances),
        "voice": voice_report(utterances),
        "decisions": [{"text": d.text, "topic": d.topic, "speaker": d.speaker} for d in ex.decisions],
        "action_items": [{"title": a.title, "owner": a.owner, "deadline": a.deadline} for a in ex.action_items],
        "conflicts": [{"new_decision": ex.decisions[j.new_index].text, "old_text": olds[j.old_id]["text"],
                       "old_meeting_title": olds[j.old_id]["meeting_title"], "kind": j.verdict,
                       "explanation": j.explanation} for j in flagged],
    }


def confirm_meeting(meeting_id: int, close_task_ids: list[int] | tuple = ()) -> dict:
    """The confirmation gate: nothing reaches memory or the tracker before this.

    close_task_ids: open tasks built on a decision this meeting supersedes, which the user
    chose (in the confirm dialog) to close as obsolete."""
    m = meeting_view(meeting_id)
    if m["status"] == "confirmed":
        raise GateError("This meeting is already confirmed.")
    if m["open_conflicts"]:
        raise GateError(f"Resolve {m['open_conflicts']} conflict(s) with earlier decisions before confirming.")
    closable = {t["id"]: (t, c) for c in m["conflicts"] if c["resolution"] == "supersede" for t in c["affected_tasks"]}
    if unknown := set(close_task_ids) - set(closable):
        raise GateError(f"Task(s) {sorted(unknown)} are not made obsolete by a superseded decision.")
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
    # Committed first, then posted: a tracker outage never loses confirmed work (retry from Tasks).
    tasks = [tracker.post(item_id, a.evidence) for item_id, a in zip(action_ids, ex.action_items)]
    closed = [
        tracker.close_obsolete(tid, f"Superseded in {m['title']}: “{c['new_decision']}” replaces “{c['old_text']}”.")
        for tid, (_, c) in ((tid, closable[tid]) for tid in dict.fromkeys(close_task_ids))
    ]
    return {"meeting_id": meeting_id, "decision_ids": decision_ids, "action_item_ids": action_ids,
            "tracker": tracker.info(), "tasks": tasks, "closed": closed}


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
