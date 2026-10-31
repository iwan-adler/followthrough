"""Spoken edits to a staged draft: "make Sam own the landing page and push it to Friday".

The LLM (or a rules fallback) only produces a list of operations against numbered items;
applying them is deterministic, and dates go through the same resolver as extraction.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from . import llm
from .dates import parse_iso, resolve_date
from .extraction import ExtractedAction, ExtractedDecision, Extraction
from .speech import clean_speech, keywords

Op = Literal["set_owner", "set_deadline", "rename_task", "delete_task", "add_task",
             "edit_decision", "delete_decision"]


class EditOp(BaseModel):
    op: Op
    index: int | None = Field(default=None, description="Item number from the list, for edits/deletes")
    value: str = Field(default="", description="New owner, deadline phrase, title or decision text")
    owner: str | None = Field(default=None, description="add_task only")
    deadline: str | None = Field(default=None, description="add_task only: deadline phrase")


class EditPlan(BaseModel):
    ops: list[EditOp] = []


SYSTEM = """You turn a spoken edit request into operations on a meeting draft.
The draft lists numbered action items (T1, T2...) and decisions (D1, D2...). Use the number
without its letter as `index`. Operations:
- set_owner(index, value=person) · set_deadline(index, value=deadline phrase as spoken, e.g. "Friday")
- rename_task(index, value=new title) · delete_task(index)
- add_task(value=title, owner?, deadline?) · edit_decision(index, value=new text) · delete_decision(index)
"it"/"that" refers to the item mentioned just before. Ignore filler. Return no ops if unsure."""


def _listing(ex: Extraction) -> str:
    tasks = [f"T{i}. {a.title} (owner: {a.owner or 'none'}, due: {a.deadline or 'none'})"
             for i, a in enumerate(ex.action_items)]
    decisions = [f"D{i}. {d.text}" for i, d in enumerate(ex.decisions)]
    return "Action items:\n" + ("\n".join(tasks) or "(none)") + "\n\nDecisions:\n" + ("\n".join(decisions) or "(none)")


# --- rules fallback -------------------------------------------------------------------------

_NAME = r"(?P<who>[A-Z][a-z]+)"
_RULES: list[tuple[str, re.Pattern]] = [
    ("rename_task", re.compile(r"^rename (?:the )?(?P<task>.+?) to (?P<value>.+)$", re.I)),
    ("delete_task", re.compile(r"^(?:delete|remove|drop|scratch|cancel|get rid of) (?:the )?(?P<task>.+?)(?: task)?$", re.I)),
    ("add_task", re.compile(r"^(?:add|create) (?:a |another )?(?:new )?(?:task|action item|to-?do)?(?: to| for| that)?:? (?P<value>.+)$", re.I)),
    ("set_owner", re.compile(rf"^(?i:assign|give|hand) (?i:the )?(?P<task>.+?) (?i:to) {_NAME}$")),
    ("set_owner", re.compile(rf"^(?i:make|have|let) {_NAME} (?i:own|do|take|handle|run) (?i:the )?(?P<task>.+)$")),
    ("set_owner", re.compile(rf"^{_NAME} (?:owns|takes|will do|will handle|should do|is doing) (?:the )?(?P<task>.+)$")),
    ("set_deadline", re.compile(r"^(?:move|push|change|set|make|bump|shift) (?:the )?(?P<task>.+?)(?: deadline| due date)? (?:to|for|until) (?P<value>.+)$", re.I)),
    ("set_deadline", re.compile(r"^(?P<task>.+?) (?:is |should be )?due (?:by |on )?(?P<value>.+)$", re.I)),
]


def _best_index(target: str, titles: list[str], last: int | None) -> int | None:
    if re.fullmatch(r"(?:it|that|this|that one|this one|that task)", target.strip(), re.I):
        return last
    want = set(keywords(target))
    scored = [(len(want & set(keywords(t))), i) for i, t in enumerate(titles)]
    best = max(scored, default=(0, None))
    return best[1] if best[0] > 0 else None


def mock_plan(command: str, ex: Extraction) -> EditPlan:
    titles = [a.title for a in ex.action_items]
    ops, last = [], None
    for clause in re.split(r"\s*(?:,? and then|,? and also|,? and|;|\.|,? then)\s+", clean_speech(command)):
        clause = clause.strip(" ,.")
        if not clause:
            continue
        for op, pat in _RULES:
            m = pat.match(clause)
            if not m:
                continue
            g = m.groupdict()
            if op == "add_task":
                rest = g["value"]
                if lead := re.match(r"([A-Z][a-z]+) (?:to|should|will|needs to) (.+)$", rest):
                    rest = f"{lead[2]} for {lead[1]}"  # "Raj to update the diagram" -> owner Raj
                owner = (re.search(r"\bfor ([A-Z][a-z]+)\b", rest) or [None, None])[1]
                when = re.search(r"\b(?:by|due|on) (.+)$", rest)
                title = re.sub(r"\s*\bfor [A-Z][a-z]+\b|\s*\b(?:by|due|on) .+$", "", rest).strip(" ,")
                ops.append(EditOp(op=op, value=title[:1].upper() + title[1:], owner=owner,
                                  deadline=when[1] if when else None))
                titles.append(title)
                last = len(titles) - 1
                break
            idx = _best_index(g["task"], titles, last)
            if idx is None:
                continue
            value = g.get("who") or g.get("value") or ""
            ops.append(EditOp(op=op, index=idx, value=value))
            last = idx
            break
    return EditPlan(ops=ops)


# --- apply ------------------------------------------------------------------------------------

def plan(command: str, ex: Extraction, meeting_date: date) -> EditPlan:
    return llm.generate_json(
        system=SYSTEM,
        prompt=f"Meeting date: {meeting_date.isoformat()} ({meeting_date.strftime('%A')})\n\n"
               f"{_listing(ex)}\n\nRequest: {command}",
        schema=EditPlan,
        mock=lambda: mock_plan(command, ex),
    )


def _deadline(phrase: str | None, meeting_date: date) -> str | None:
    d = resolve_date(phrase, meeting_date) or parse_iso(phrase)
    return d.isoformat() if d else None


def apply(ex: Extraction, edit_plan: EditPlan, meeting_date: date) -> tuple[Extraction, list[str]]:
    """Apply operations to a copy of the draft; returns it with human-readable changes."""
    ex = ex.model_copy(deep=True)
    tasks, decisions = ex.action_items, ex.decisions
    changes: list[str] = []
    deleted_tasks, deleted_decisions = set(), set()
    for op in edit_plan.ops:
        if op.op == "add_task":
            if op.value.strip():
                tasks.append(ExtractedAction(title=op.value.strip(), owner=op.owner, confidence=1.0,
                                             deadline=_deadline(op.deadline, meeting_date)))
                changes.append(f"Added “{op.value.strip()}”" + (f" for {op.owner}" if op.owner else ""))
            continue
        if op.op == "edit_decision" or op.op == "delete_decision":
            if op.index is None or not 0 <= op.index < len(decisions):
                continue
            d = decisions[op.index]
            if op.op == "delete_decision":
                deleted_decisions.add(op.index)
                changes.append(f"Removed decision “{d.text}”")
            elif op.value.strip():
                changes.append(f"Decision now reads “{op.value.strip()}”")
                d.text = op.value.strip()
            continue
        if op.index is None or not 0 <= op.index < len(tasks):
            continue
        t = tasks[op.index]
        if op.op == "set_owner" and op.value.strip():
            t.owner = op.value.strip()
            changes.append(f"“{t.title}” → owner {t.owner}")
        elif op.op == "set_deadline":
            due = _deadline(op.value, meeting_date)
            if due:
                # Store the resolved date only: the phrase would be re-read against the meeting date.
                t.deadline, t.deadline_text = due, None
                changes.append(f"“{t.title}” → due {date.fromisoformat(due).strftime('%a %b %d')}")
        elif op.op == "rename_task" and op.value.strip():
            changes.append(f"Renamed “{t.title}” → “{op.value.strip()}”")
            t.title = op.value.strip()
        elif op.op == "delete_task":
            deleted_tasks.add(op.index)
            changes.append(f"Removed “{t.title}”")
    ex.action_items = [t for i, t in enumerate(tasks) if i not in deleted_tasks]
    ex.decisions = [d for i, d in enumerate(decisions) if i not in deleted_decisions]
    return ex, changes
