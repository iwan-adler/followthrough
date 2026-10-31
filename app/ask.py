"""Ask the team's memory: grounded Q&A over decisions, tasks and conflicts, with citations.

The model only sees what is in memory and must cite item ids; citations are checked
against the real ids before anything is shown. A retrieval + template fallback answers
the common questions (what did we decide about X, what changed, what does Y owe) offline.
"""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field

from . import db, digest, llm
from .speech import guess_topic, keywords


class Answer(BaseModel):
    answer: str = Field(description="2-4 sentences, plain language, grounded only in the memory given")
    decision_ids: list[int] = Field(default=[], description="ids (D#) of decisions the answer relies on")
    task_ids: list[int] = Field(default=[], description="ids (T#) of tasks the answer relies on")


SYSTEM = """You answer questions about a product team's decisions and work, using ONLY the team
memory provided. Cite what you use: decision ids (the number after D) in decision_ids and task ids
(the number after T) in task_ids. Superseded decisions are history: say what replaced them and when.
Give dates as "Mon Oct 26". If the memory does not contain the answer, say so plainly."""


def _memory() -> tuple[list[dict], list[dict]]:
    decisions = db.rows(
        "SELECT d.id, d.text, d.topic, d.speaker, d.status, d.superseded_by, m.title AS meeting_title, "
        "m.meeting_date FROM decisions d JOIN meetings m ON m.id = d.meeting_id ORDER BY m.meeting_date, d.id")
    tasks = db.rows(
        "SELECT a.id, a.title, a.owner, a.deadline, a.status, a.tracker_ref, m.title AS meeting_title "
        "FROM action_items a JOIN meetings m ON m.id = a.meeting_id ORDER BY a.deadline IS NULL, a.deadline")
    return decisions, tasks


def _day(iso: str | None) -> str:
    if not iso:
        return "no date"
    d = date.fromisoformat(iso[:10])
    return f"{d.strftime('%a %b')} {d.day}"


def _context(decisions: list[dict], tasks: list[dict]) -> str:
    by_id = {d["id"]: d for d in decisions}
    lines = ["DECISIONS"]
    for d in decisions:
        status = (f"SUPERSEDED by D{d['superseded_by']}" if d["status"] == "superseded" and d["superseded_by"] in by_id
                  else d["status"].upper())
        lines.append(f"D{d['id']} [{d['topic']}] {status} · {d['speaker']} in “{d['meeting_title']}” "
                     f"({_day(d['meeting_date'])}): {d['text']}")
    lines.append("\nTASKS")
    lines += [f"T{t['id']} {t['status'].upper()} · {t['owner'] or 'unassigned'} · due {_day(t['deadline'])}"
              f" · from “{t['meeting_title']}”: {t['title']}" for t in tasks]
    return "\n".join(lines)


def mock_answer(question: str, decisions: list[dict], tasks: list[dict]) -> Answer:
    q_words = set(keywords(question))
    people = {p.lower(): p for p in digest.people()}
    person = next((people[w] for w in re.findall(r"[a-z]+", question.lower()) if w in people), None)

    if person and re.search(r"\b(owe|own|working on|tasks?|doing|due|plate|assigned)\b", question, re.I):
        mine = [t for t in tasks if t["status"] == "open" and (t["owner"] or "").lower() == person.lower()]
        if not mine:
            return Answer(answer=f"{person} has no open tasks in memory.")
        listing = "; ".join(f"“{t['title']}” (due {_day(t['deadline'])})" for t in mine[:5])
        return Answer(answer=f"{person} has {len(mine)} open task{'s' * (len(mine) != 1)}: {listing}.",
                      task_ids=[t["id"] for t in mine[:5]])

    topic = guess_topic(question)
    scored = sorted(
        ((len(q_words & set(keywords(f"{d['topic']} {d['text']}"))) + 2 * (d["topic"] == topic), d) for d in decisions),
        key=lambda s: -s[0])
    hits = [d for score, d in scored if score > 0]
    if not hits:
        related = [t for t in tasks if q_words & set(keywords(t["title"]))]
        if related:
            t = related[0]
            return Answer(answer=f"No decision covers that, but there is a task: “{t['title']}”, "
                                 f"{t['owner'] or 'unassigned'}, due {_day(t['deadline'])} ({t['status']}).",
                          task_ids=[t["id"]])
        return Answer(answer="I couldn't find anything about that in the team's memory.")

    chain = [d for d in decisions if d["topic"] == hits[0]["topic"]]
    current = next((d for d in reversed(chain) if d["status"] == "active"), hits[0])
    answer = (f"On {current['topic']}, the current decision is “{current['text']}”, from {current['speaker']} "
              f"in {current['meeting_title']} ({_day(current['meeting_date'])}).")
    replaced = [d for d in chain if d["superseded_by"] == current["id"]]
    if replaced:
        old = replaced[-1]
        answer += (f" It replaced “{old['text']}”, decided by {old['speaker']} in {old['meeting_title']} "
                   f"({_day(old['meeting_date'])}).")
    return Answer(answer=answer, decision_ids=[current["id"]] + [d["id"] for d in replaced])


def ask(question: str) -> dict:
    decisions, tasks = _memory()
    if not decisions and not tasks:
        return {"answer": "Memory is empty. Confirm a meeting first.", "decisions": [], "tasks": [], "source": "none"}
    result = llm.generate_json(
        system=SYSTEM,
        prompt=f"TEAM MEMORY\n{_context(decisions, tasks)}\n\nQUESTION: {question}",
        schema=Answer,
        mock=lambda: mock_answer(question, decisions, tasks),
    )
    d_by_id, t_by_id = {d["id"]: d for d in decisions}, {t["id"]: t for t in tasks}
    return {
        "answer": result.answer,
        "decisions": [d_by_id[i] for i in dict.fromkeys(result.decision_ids) if i in d_by_id],  # drop invented ids
        "tasks": [t_by_id[i] for i in dict.fromkeys(result.task_ids) if i in t_by_id],
        "source": llm.provider_info()["last_source"],
    }
