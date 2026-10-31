"""Weekly digest per person: open items, due this week, decisions that affect them,
unresolved conflicts. Facts are gathered deterministically; the LLM only writes the
narrative, with a plain template as fallback (and as the mock)."""
from __future__ import annotations

import json
from datetime import date, timedelta

from pydantic import BaseModel, Field

from . import db, llm, memory
from .speech import keywords


class DigestNarrative(BaseModel):
    subject: str = Field(description="Short subject line for this person's week")
    summary: str = Field(description="2-3 friendly sentences: the shape of their week")
    highlights: list[str] = Field(default=[], description="Decisions and changes that affect them")
    heads_up: list[str] = Field(default=[], description="Overdue work, open conflicts, missing info")


DIGEST_SYSTEM = """You write a short weekly digest for ONE teammate on a product team.
Use ONLY the facts in the JSON you are given; never invent tasks, dates or decisions.
Lead with what changed (decisions that replaced earlier ones) and anything overdue.
Mention deadlines with the weekday ("due Thu Nov 5"). Address the person as "you".
highlights: one bullet per decision or change that affects them. heads_up: overdue items,
unresolved conflicts, tasks that may be obsolete because a decision changed (say why), and
tasks missing a deadline. Keep every bullet under 25 words."""


def people() -> list[str]:
    owners = {r["owner"] for r in db.rows("SELECT DISTINCT owner FROM action_items WHERE owner IS NOT NULL")}
    speakers = set()
    for r in db.rows("SELECT speakers FROM meetings WHERE status = 'confirmed'"):
        speakers.update(json.loads(r["speakers"]))
    return sorted((owners | speakers) - {"Unknown"})


def _week(today: date) -> tuple[date, date]:
    """A rolling week, so a digest read on Saturday still shows Monday's work."""
    return today, today + timedelta(days=6)


def gather(person: str, today: date | None = None) -> dict:
    today = today or date.today()
    week_start, week_end = _week(today)
    tasks = db.rows(
        "SELECT a.id, a.title, a.deadline, a.status, a.completed_at, a.tracker_ref, a.tracker_url, "
        "m.title AS meeting_title "
        "FROM action_items a JOIN meetings m ON m.id = a.meeting_id "
        "WHERE lower(a.owner) = lower(?) ORDER BY a.deadline IS NULL, a.deadline", (person,))
    open_items = [t for t in tasks if t["status"] == "open"]
    overdue = [t for t in open_items if t["deadline"] and t["deadline"] < today.isoformat()]
    due_this_week = [t for t in open_items
                     if t["deadline"] and today.isoformat() <= t["deadline"] <= week_end.isoformat()]
    no_deadline = [t for t in open_items if not t["deadline"]]
    done_this_week = [t for t in tasks if t["status"] == "done"
                      and (t["completed_at"] or "") >= (today - timedelta(days=7)).isoformat()]
    stale = memory.stale_task_reasons(open_items)
    possibly_obsolete = [{**t, "reason": stale[t["id"]]} for t in open_items if t["id"] in stale]

    # Decisions from the last 7 days that affect this person: they were in the meeting,
    # they made the call, or it shares keywords with their open work.
    task_words = set().union(*(set(keywords(t["title"])) for t in open_items)) if open_items else set()
    since = (today - timedelta(days=7)).isoformat()
    recent = db.rows(
        "SELECT d.id, d.text, d.topic, d.speaker, d.status, m.title AS meeting_title, m.meeting_date, m.speakers, "
        "  (SELECT group_concat(o.text, ' | ') FROM decisions o WHERE o.superseded_by = d.id) AS replaced "
        "FROM decisions d JOIN meetings m ON m.id = d.meeting_id "
        "WHERE m.meeting_date >= ? AND d.status = 'active' ORDER BY m.meeting_date, d.id", (since,))
    decisions = []
    for d in recent:
        attended = person.lower() in (s.lower() for s in json.loads(d.pop("speakers")))
        related = bool(task_words & set(keywords(f"{d['topic']} {d['text']}")))
        if attended or related or d["speaker"].lower() == person.lower():
            d["why"] = "related to your tasks" if related else ("you were there" if attended else "your call")
            decisions.append(d)

    conflicts = [
        c for c in db.rows(
            "SELECT c.id, c.new_decision, c.new_speaker, c.explanation, c.kind, o.text AS old_text, "
            "o.speaker AS old_speaker, m.title AS meeting_title, m.speakers "
            "FROM conflicts c JOIN decisions o ON o.id = c.old_decision_id JOIN meetings m ON m.id = c.meeting_id "
            "WHERE c.resolution IS NULL")
        if person.lower() in {(c["new_speaker"] or "").lower(), c["old_speaker"].lower()}
        or person.lower() in (s.lower() for s in json.loads(c["speakers"]))
    ]
    for c in conflicts:
        c.pop("speakers")

    return {
        "person": person,
        "today": today.isoformat(),
        "week": {"start": week_start.isoformat(), "end": week_end.isoformat()},
        "open_items": open_items,
        "overdue": overdue,
        "due_this_week": due_this_week,
        "no_deadline": no_deadline,
        "possibly_obsolete": possibly_obsolete,
        "done": done_this_week,
        "decisions": decisions,
        "unresolved_conflicts": conflicts,
    }


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%a %b')} {d.day}"


def template_narrative(data: dict) -> DigestNarrative:
    name = data["person"]
    n_open, n_due, n_over = len(data["open_items"]), len(data["due_this_week"]), len(data["overdue"])
    if not n_open and not data["decisions"]:
        summary = f"Quiet week, {name}: nothing open is assigned to you and no new decisions touch your work."
    else:
        parts = [f"You have {n_open} open item{'s' * (n_open != 1)}"]
        if n_due:
            parts.append(f"{n_due} due this week")
        if n_over:
            parts.append(f"{n_over} overdue")
        summary = ", ".join(parts) + "."
        if data["due_this_week"]:
            t = data["due_this_week"][0]
            summary += f" Next up: “{t['title']}”, due {_day(t['deadline'])}."
    highlights = []
    for d in data["decisions"]:
        if d["replaced"]:
            highlights.append(f"Changed ({d['topic']}): now “{d['text']}”, replacing “{d['replaced']}”.")
        else:
            highlights.append(f"Decided ({d['topic']}): “{d['text']}” in {d['meeting_title']}.")
    heads_up = [f"May be obsolete: “{t['title']}” {t['reason']}." for t in data["possibly_obsolete"]]
    heads_up += [f"Overdue since {_day(t['deadline'])}: “{t['title']}”." for t in data["overdue"]]
    heads_up += [f"Unresolved conflict: {c['explanation']}" for c in data["unresolved_conflicts"]]
    heads_up += [f"No deadline yet: “{t['title']}”." for t in data["no_deadline"]]
    return DigestNarrative(
        subject=f"{name}'s week: {n_open} open, {n_due} due, {n_over} overdue",
        summary=summary, highlights=highlights, heads_up=heads_up,
    )


def generate(person: str, today: date | None = None) -> dict:
    data = gather(person, today)
    narrative = llm.generate_json(
        system=DIGEST_SYSTEM,
        prompt=f"Write {person}'s weekly digest from these facts:\n{json.dumps(data, indent=1, default=str)}",
        schema=DigestNarrative,
        mock=lambda: template_narrative(data),
    )
    return {**data, "narrative": narrative.model_dump(), "source": llm.provider_info()["last_source"]}


def to_markdown(d: dict) -> str:
    n = d["narrative"]
    lines = [f"# {n['subject']}", "", n["summary"], ""]
    if n["highlights"]:
        lines += ["## What changed", *[f"- {h}" for h in n["highlights"]], ""]
    if n["heads_up"]:
        lines += ["## Heads up", *[f"- {h}" for h in n["heads_up"]], ""]
    if d["due_this_week"]:
        lines += ["## Due this week",
                  *[f"- [ ] {t['title']} ({_day(t['deadline'])})" + (f" {t['tracker_ref']}" if t["tracker_ref"] else "")
                    for t in d["due_this_week"]], ""]
    later = [t for t in d["open_items"] if t not in d["due_this_week"] and t not in d["overdue"]]
    if later:
        lines += ["## Also open", *[f"- [ ] {t['title']}" + (f" ({_day(t['deadline'])})" if t["deadline"] else "")
                                    for t in later], ""]
    return "\n".join(lines).rstrip() + "\n"
