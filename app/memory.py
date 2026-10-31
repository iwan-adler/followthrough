"""Cross-meeting decision memory and conflict detection.

When a meeting is staged, each new decision is compared against ACTIVE decisions from
earlier confirmed meetings:
  1. cheap prefilter: same topic, or enough keyword overlap
  2. one batched LLM call judges each pair: contradiction | supersession | none
Conflicts are stored unresolved. The user picks supersede / keep / dismiss, and the
resolutions are applied when the meeting is confirmed (confirm is blocked until then).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from . import db, llm
from .extraction import ExtractedDecision
from .speech import keywords

RESOLUTIONS = ("supersede", "keep", "dismiss")


class Judgment(BaseModel):
    new_index: int
    old_id: int
    shared_subject: str = Field(
        default="", description="The single thing BOTH decisions set (e.g. 'launch date'); empty if they set different things")
    verdict: Literal["contradiction", "supersession", "none"]
    explanation: str


class Judgments(BaseModel):
    judgments: list[Judgment] = []


# --- memory queries ---------------------------------------------------------------------

_DECISION_SELECT = """
SELECT d.*, m.title AS meeting_title, m.meeting_date,
       s.text AS superseded_by_text, sm.title AS superseded_by_meeting
FROM decisions d
JOIN meetings m ON m.id = d.meeting_id
LEFT JOIN decisions s ON s.id = d.superseded_by
LEFT JOIN meetings sm ON sm.id = s.meeting_id
"""


def active_decisions() -> list[dict]:
    return db.rows(_DECISION_SELECT + " WHERE d.status = 'active' ORDER BY m.meeting_date, d.id")


def timeline() -> list[dict]:
    """All decisions in meeting order, each with the conflict (if any) that changed it."""
    decisions = db.rows(_DECISION_SELECT + " ORDER BY m.meeting_date, d.id")
    why = {c["old_decision_id"]: c for c in db.rows(
        "SELECT old_decision_id, kind, explanation, resolution FROM conflicts WHERE resolution IS NOT NULL")}
    for d in decisions:
        c = why.get(d["id"])
        d["change"] = {"kind": c["kind"], "explanation": c["explanation"], "resolution": c["resolution"]} if c else None
    return decisions


def known_topics() -> list[str]:
    return [r["topic"] for r in db.rows("SELECT DISTINCT topic FROM decisions ORDER BY topic")]


def list_conflicts(meeting_id: int | None = None, open_only: bool = False) -> list[dict]:
    sql = """
    SELECT c.*, o.text AS old_text, o.topic AS old_topic, o.speaker AS old_speaker,
           o.status AS old_status, om.title AS old_meeting_title, om.meeting_date AS old_meeting_date,
           m.title AS meeting_title, m.meeting_date, m.status AS meeting_status
    FROM conflicts c
    JOIN decisions o ON o.id = c.old_decision_id
    JOIN meetings om ON om.id = o.meeting_id
    JOIN meetings m ON m.id = c.meeting_id
    WHERE 1 = 1"""
    params: list = []
    if meeting_id is not None:
        sql += " AND c.meeting_id = ?"
        params.append(meeting_id)
    if open_only:
        sql += " AND c.resolution IS NULL"
    return db.rows(sql + " ORDER BY c.id", tuple(params))


# --- prefilter ------------------------------------------------------------------------

def _kw(text: str) -> set[str]:
    return set(keywords(text))


def candidates(new: ExtractedDecision, actives: list[dict], limit: int = 5) -> list[dict]:
    """Earlier active decisions worth asking the LLM about."""
    nk = _kw(f"{new.topic} {new.text}")
    scored = []
    for old in actives:
        ok = _kw(f"{old['topic']} {old['text']}")
        shared = len(nk & ok)
        overlap = shared / max(1, min(len(nk), len(ok)))
        same_topic = old["topic"] == new.topic
        if same_topic or shared >= 2 or (shared == 1 and overlap >= 0.5):
            scored.append((int(same_topic) + overlap, old))
    scored.sort(key=lambda s: -s[0])
    return [old for _, old in scored[:limit]]


# --- judges -------------------------------------------------------------------------------

JUDGE_SYSTEM = """You audit a product team's decisions across meetings to catch silent reversals.
For every (new decision, earlier decision) pair, return a verdict:
- "contradiction": both cannot be true at once AND the new decision does not acknowledge the earlier
  one. This is a silent reversal, the dangerous case.
- "supersession": both cannot be true at once, but the new decision knowingly replaces the earlier one
  ("we're moving the launch to...", "instead of X we'll...").
- "none": compatible, a restatement, a refinement, or about a different aspect (Pro price vs free tier).
explanation: ONE plain sentence naming both values and when the earlier one was made, e.g.
"Product kickoff (Oct 19) set launch for Nov 23; this sets Dec 7 without mentioning the change."
First fill shared_subject: the one specific thing both decisions set a value for (the launch date, the
database, the Pro plan price). If they set different things that merely share a word (the beta launch
date vs. when the beta invite email goes out), shared_subject is "" and the verdict must be "none".
Return one judgment per pair, using the given new_index and old_id."""


def build_judge_prompt(pairs: list[tuple[int, ExtractedDecision, dict]], meeting_date: date) -> str:
    lines = [f"New meeting date: {meeting_date.isoformat()}", ""]
    for i, new, old in pairs:
        lines.append(f"PAIR new_index={i} old_id={old['id']}")
        lines.append(f"  NEW [{new.topic}] {new.speaker}: {new.text}")
        if new.evidence:
            lines.append(f"      said: \"{new.evidence}\"")
        lines.append(f"  EARLIER [{old['topic']}] {old['speaker']} in \"{old['meeting_title']}\" "
                     f"({old['meeting_date']}): {old['text']}")
        lines.append("")
    return "\n".join(lines)


_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_TECH = {
    "postgresql": "Postgres", "postgres": "Postgres", "mongodb": "MongoDB", "mongo": "MongoDB",
    "firebase": "Firebase", "firestore": "Firebase", "mysql": "MySQL", "dynamodb": "DynamoDB",
    "dynamo": "DynamoDB", "supabase": "Supabase", "sqlite": "SQLite", "redis": "Redis",
    "cockroachdb": "CockroachDB", "aws": "AWS", "gcp": "GCP", "azure": "Azure", "vercel": "Vercel",
    "heroku": "Heroku", "react": "React", "vue": "Vue", "flutter": "Flutter",
}
_VALUE_PATTERNS = {
    "date": re.compile(rf"\b(?:{_MONTHS})[a-z]*\.? \d{{1,2}}(?:st|nd|rd|th)?\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b", re.I),
    # "free tier" is an aspect of pricing, not a price, so only amounts count.
    "price": re.compile(r"\$\d[\d,]*(?:\.\d+)?", re.I),
    "colour": re.compile(r"\b(?:red|orange|yellow|green|blue|purple|pink|black|white|gr[ae]y|teal|navy)\b", re.I),
    "technology": re.compile(rf"\b(?:{'|'.join(sorted(_TECH, key=len, reverse=True))})\b", re.I),
}
_CHANGE_CUES = re.compile(r"\b(moving|move|moved|switch|switching|instead|change|changing|pushed?|delay|"
                          r"revis|updated?|no longer|actually)\w*", re.I)


def _values(text: str) -> dict[str, dict[str, str]]:
    """Comparable 'slots' in a decision: {"date": {"nov 23": "November 23rd"}, ...}."""
    out: dict[str, dict[str, str]] = {}
    for cls, pat in _VALUE_PATTERNS.items():
        for m in pat.finditer(text):
            raw = m[0]
            if cls == "technology":
                norm = disp = _TECH[raw.lower()]
            elif cls == "date" and not raw[0].isdigit():
                mon, day = raw.split()[0][:3].lower(), re.sub(r"\D", "", raw.split()[1])
                norm, disp = f"{mon} {int(day)}", raw
            else:
                norm, disp = raw.lower().replace(",", ""), raw
            out.setdefault(cls, {})[norm] = disp
    return out


def _short_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}"


def mock_judge(pairs: list[tuple[int, ExtractedDecision, dict]]) -> Judgments:
    """Same slot, different value -> conflict. Change language -> supersession."""
    out = []
    for i, new, old in pairs:
        nv, ov = _values(new.text), _values(old["text"])
        clash = [c for c in nv if c in ov and not set(nv[c]) & set(ov[c])]
        if not clash:
            out.append(Judgment(new_index=i, old_id=old["id"], verdict="none", explanation="Compatible."))
            continue
        c = clash[0]
        kind = "supersession" if _CHANGE_CUES.search(new.text) else "contradiction"
        tail = "." if kind == "supersession" else " without acknowledging the change."
        out.append(Judgment(
            new_index=i, old_id=old["id"], verdict=kind, shared_subject=new.topic,
            explanation=(f"{old['meeting_title']} ({_short_date(old['meeting_date'])}) set the {new.topic} "
                         f"to {', '.join(ov[c].values())}; this meeting says {', '.join(nv[c].values())}{tail}"),
        ))
    return Judgments(judgments=out)


def stale_task_reasons(tasks: list[dict]) -> dict[int, str]:
    """Open tasks that mention a value a later decision dropped, e.g. "Set up the Postgres
    schema" after the team moved from Postgres to Firebase."""
    replaced = db.rows(
        "SELECT o.text AS old_text, n.text AS new_text, m.title AS meeting_title FROM decisions o "
        "JOIN decisions n ON n.id = o.superseded_by JOIN meetings m ON m.id = n.meeting_id "
        "WHERE o.status = 'superseded'")
    reasons: dict[int, str] = {}
    for r in replaced:
        for t, dropped, now in _tasks_mentioning_dropped(r["old_text"], r["new_text"], tasks):
            reasons.setdefault(t["id"], f"mentions {dropped}, which {r['meeting_title']} replaced with {now}")
    return reasons


def _tasks_mentioning_dropped(old_text: str, new_text: str, tasks: list[dict]):
    """Yield (task, dropped value, replacement) for open tasks naming a value the new decision drops."""
    old_v, new_v = _values(old_text), _values(new_text)
    for cls, olds in old_v.items():
        for norm, disp in olds.items():
            if norm in new_v.get(cls, {}):
                continue
            now = ", ".join(new_v.get(cls, {}).values()) or "something else"
            for t in tasks:
                if t["status"] == "open" and disp.lower() in t["title"].lower():
                    yield t, disp, now


def conflict_impact(conflict: dict) -> list[dict]:
    """Open tasks built on the old decision, which superseding it would make obsolete."""
    tasks = db.rows("SELECT id, title, owner, status, tracker_ref, tracker_url FROM action_items WHERE status = 'open'")
    seen, out = set(), []
    for t, dropped, now in _tasks_mentioning_dropped(conflict["old_text"], conflict["new_decision"], tasks):
        if t["id"] not in seen:
            seen.add(t["id"])
            out.append({**t, "reason": f"mentions {dropped}; the new decision says {now}"})
    return out


# --- detection & resolution --------------------------------------------------------------

def detect_conflicts(meeting_id: int, meeting_date: date, decisions: list[ExtractedDecision]) -> list[dict]:
    """(Re)compute conflicts for a staged meeting. A resolution already chosen is kept while
    the new decision's text is unchanged; editing the text asks the question again."""
    previous = {(c["new_decision"], c["old_decision_id"]): c["resolution"]
                for c in list_conflicts(meeting_id)}
    db.execute("DELETE FROM conflicts WHERE meeting_id = ?", (meeting_id,))

    actives = [d for d in active_decisions() if d["meeting_id"] != meeting_id]
    pairs = [(i, d, old) for i, d in enumerate(decisions) for old in candidates(d, actives)]
    if not pairs:
        return []
    result = llm.generate_json(
        system=JUDGE_SYSTEM,
        prompt=build_judge_prompt(pairs, meeting_date),
        schema=Judgments,
        mock=lambda: mock_judge(pairs),
    )
    valid = {(i, old["id"]): (d, old) for i, d, old in pairs}
    seen = set()
    for j in result.judgments:
        key = (j.new_index, j.old_id)
        # A conflict needs both decisions to set the same subject; otherwise it's a false alarm.
        if j.verdict == "none" or not j.shared_subject.strip() or key not in valid or key in seen:
            continue
        seen.add(key)
        new, _ = valid[key]
        db.execute(
            "INSERT INTO conflicts (meeting_id, new_decision, new_decision_index, new_speaker, "
            "old_decision_id, kind, explanation, resolution) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (meeting_id, new.text, j.new_index, new.speaker, j.old_id, j.verdict,
             j.explanation.strip(), previous.get((new.text, j.old_id))),
        )
    return list_conflicts(meeting_id)


class ConflictError(ValueError):
    pass


def resolve_conflict(conflict_id: int, resolution: str) -> dict:
    if resolution not in RESOLUTIONS:
        raise ConflictError(f"resolution must be one of {', '.join(RESOLUTIONS)}")
    c = db.row("SELECT c.*, m.status AS meeting_status FROM conflicts c "
               "JOIN meetings m ON m.id = c.meeting_id WHERE c.id = ?", (conflict_id,))
    if not c:
        raise LookupError("Conflict not found.")
    if c["meeting_status"] != "staged":
        raise ConflictError("This meeting is already confirmed; its resolutions were applied.")
    db.execute("UPDATE conflicts SET resolution = ?, resolved_at = datetime('now') WHERE id = ?",
               (resolution, conflict_id))
    return next(x for x in list_conflicts(c["meeting_id"]) if x["id"] == conflict_id)


def commit_decisions(conn: sqlite3.Connection, meeting_id: int, decisions: list[ExtractedDecision]) -> list[int]:
    """Write a confirmed meeting's decisions to memory and apply conflict resolutions.

    supersede -> old decision becomes superseded by the new one
    keep      -> the new decision is recorded but immediately superseded by the old one
    dismiss   -> both stay active (not a real conflict)
    """
    conflicts = [dict(r) for r in conn.execute(
        "SELECT * FROM conflicts WHERE meeting_id = ?", (meeting_id,)).fetchall()]
    ids = []
    for i, d in enumerate(decisions):
        new_id = conn.execute(
            "INSERT INTO decisions (meeting_id, text, topic, speaker, confidence) VALUES (?, ?, ?, ?, ?)",
            (meeting_id, d.text, d.topic, d.speaker, d.confidence),
        ).lastrowid
        ids.append(new_id)
        for c in (c for c in conflicts if c["new_decision_index"] == i):
            conn.execute("UPDATE conflicts SET new_decision_id = ? WHERE id = ?", (new_id, c["id"]))
            if c["resolution"] == "supersede":
                conn.execute("UPDATE decisions SET status = 'superseded', superseded_by = ? WHERE id = ?",
                             (new_id, c["old_decision_id"]))
            elif c["resolution"] == "keep":
                conn.execute("UPDATE decisions SET status = 'superseded', superseded_by = ? WHERE id = ?",
                             (c["old_decision_id"], new_id))
    return ids
