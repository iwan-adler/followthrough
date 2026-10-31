"""Resolve spoken deadline phrases ("next Friday", "end of month", "Nov 3rd") to dates.

Deterministic on purpose: LLMs are unreliable at weekday arithmetic, so the extractor
prefers this resolver whenever it understands the phrase.

Conventions: a bare weekday means the next one strictly after the meeting day;
"next <weekday>" means that weekday in the following calendar week (Mon-Sun).
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tues": 1, "tue": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thurs": 3, "thur": 3, "thu": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "ten": 10, "fourteen": 14, "couple of": 2, "few": 3}

_WD = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues|tue|wed|thurs|thur|thu|fri|sat|sun)"
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*\.?"
_ORD = r"(\d{1,2})(?:st|nd|rd|th)?"

# Phrases the action-item mock looks for inside a sentence.
DATE_PHRASE_RE = re.compile(
    r"\b(?:(?:by|before|on|until|due|for)\s+)?(?:the\s+)?(?:"
    r"end of (?:the |next )?(?:day|week|month)|eod|eow|"
    r"day after tomorrow|tomorrow|today|tonight|"
    r"in (?:\d+|a|an|one|two|three|four|five|six|seven|ten|fourteen|a couple of|a few) (?:days?|weeks?)|"
    rf"(?:next|this|coming) {_WD}|{_WD}(?: next week| this week)?|"
    r"next week|this week|"
    rf"{_MON}\s+{_ORD}|{_ORD}\s+(?:of\s+)?{_MON}|"
    r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"the \d{1,2}(?:st|nd|rd|th)"
    r")\b",
    re.IGNORECASE,
)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _end_of_month(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _safe(y: int, m: int, dd: int) -> date | None:
    try:
        return date(y, m, dd)
    except ValueError:
        return None


def resolve_date(phrase: str | None, meeting: date) -> date | None:
    if not phrase:
        return None
    p = re.sub(r"\s+", " ", phrase.lower()).strip()

    if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", p):
        return _safe(int(m[1]), int(m[2]), int(m[3]))
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", p):  # US M/D[/Y]
        y = int(m[3]) if m[3] else meeting.year
        y = y + 2000 if y < 100 else y
        d = _safe(y, int(m[1]), int(m[2]))
        if d and not m[3] and d < meeting - timedelta(days=60):
            d = _safe(y + 1, int(m[1]), int(m[2]))
        return d

    if re.search(r"\bday after tomorrow\b", p):
        return meeting + timedelta(days=2)
    if re.search(r"\btomorrow\b", p):
        return meeting + timedelta(days=1)
    if re.search(r"\b(today|tonight|eod|end of (the )?day)\b", p):
        return meeting

    if m := re.search(r"\bin (\d+|a couple of|a few|an|a|one|two|three|four|five|six|seven|ten|fourteen) (day|week)s?\b", p):
        n = int(m[1]) if m[1].isdigit() else NUMBER_WORDS[m[1].removeprefix("a ").strip()]
        return meeting + timedelta(days=n * (7 if m[2] == "week" else 1))

    if re.search(r"\bend of (the )?next month\b", p):
        return _end_of_month(_end_of_month(meeting) + timedelta(days=1))
    if re.search(r"\bend of (the )?month\b", p):
        return _end_of_month(meeting)
    if re.search(r"\bend of (the )?next week\b", p):
        return _monday(meeting) + timedelta(days=11)
    if re.search(r"\b(end of (the )?week|eow)\b", p):
        return max(meeting, _monday(meeting) + timedelta(days=4))

    if m := re.search(rf"\b(?:(next|this|coming) )?{_WD}\b( next week| this week)?", p):
        target = WEEKDAYS[m[2]]
        if m[1] == "next" or m[3] == " next week":
            return _monday(meeting) + timedelta(days=7 + target)
        if m[1] == "this" or m[3] == " this week":
            d = _monday(meeting) + timedelta(days=target)
            return d if d >= meeting else d + timedelta(days=7)
        return meeting + timedelta(days=((target - meeting.weekday()) % 7) or 7)

    if re.search(r"\bnext week\b", p):
        return _monday(meeting) + timedelta(days=7)
    if re.search(r"\bthis week\b", p):
        return max(meeting, _monday(meeting) + timedelta(days=4))

    m = re.search(rf"\b{_MON}\s+{_ORD}\b", p)
    mon, day = (m[1], m[2]) if m else (None, None)
    if not m and (m := re.search(rf"\b{_ORD}\s+(?:of\s+)?{_MON}", p)):
        day, mon = m[1], m[2]
    if mon:
        d = _safe(meeting.year, MONTHS[mon[:4] if mon.startswith("sept") else mon[:3]], int(day))
        if d and d < meeting - timedelta(days=60):  # "Jan 5" said in November
            d = _safe(meeting.year + 1, d.month, d.day)
        return d

    if m := re.search(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", p):
        d = _safe(meeting.year, meeting.month, int(m[1]))
        if d and d < meeting:
            nxt = _end_of_month(meeting) + timedelta(days=1)
            d = _safe(nxt.year, nxt.month, int(m[1]))
        return d
    return None


def parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
