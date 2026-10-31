"""Handling real speech: speaker attribution, filler removal, self-corrections, topics.

Transcripts arrive dictated through Wispr Flow or pasted from a spoken meeting, so
they look like: "Priya: um so launch is Friday... no wait, Monday the 17th".
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

UNKNOWN = "Unknown"


@dataclass
class Utterance:
    speaker: str
    text: str


# "Priya: ...", "[00:01:23] Priya Shah: ...", "00:01 - Sam: ..."
_SPEAKER_RE = re.compile(
    r"^\s*(?:[\[(]?\d{1,2}:\d{2}(?::\d{2})?[\])]?\s*[-–]?\s*)?"
    r"([A-Z][A-Za-z.'\-]*(?:\s[A-Z][A-Za-z.'\-]*){0,2})\s*[:：]\s*(.*)$"
)
# Labels that look like "Word:" but aren't people.
_NOT_SPEAKERS = {
    "note", "notes", "action", "actions", "action item", "action items", "decision",
    "decisions", "agenda", "todo", "to do", "re", "subject", "summary", "update",
    "updates", "next steps", "attendees", "date", "meeting", "title", "fyi", "ps",
}
_VTT_VOICE_RE = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>|$)")
_CUE_TIMING_RE = re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*-->")


def normalize_transcript(text: str) -> str:
    """Flatten WebVTT / SRT exports into plain 'Speaker: text' lines."""
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.upper() == "WEBVTT" or _CUE_TIMING_RE.match(s) or re.fullmatch(r"\d+", s):
            continue
        if m := _VTT_VOICE_RE.search(s):
            s = f"{m[1].strip()}: {m[2].strip()}"
        out.append(s)
    return "\n".join(out)


def parse_transcript(text: str) -> list[Utterance]:
    """Split into utterances. Unlabeled lines continue the previous speaker,
    or go to "Unknown" when nobody has spoken yet."""
    utterances: list[Utterance] = []
    for line in normalize_transcript(text).split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _SPEAKER_RE.match(line)
        if m and m[1].strip().lower() not in _NOT_SPEAKERS:
            name = m[1].strip()
            if utterances and utterances[-1].speaker == name:
                utterances[-1].text += " " + m[2].strip()
            else:
                utterances.append(Utterance(name, m[2].strip()))
        elif utterances:
            utterances[-1].text += " " + line
        else:
            utterances.append(Utterance(UNKNOWN, line))
    return [u for u in utterances if u.text.strip()]


def speakers(utterances: list[Utterance]) -> list[str]:
    seen: dict[str, None] = {}
    for u in utterances:
        seen.setdefault(u.speaker, None)
    return list(seen)


def format_for_llm(utterances: list[Utterance]) -> str:
    return "\n".join(f"[{i}] {u.speaker}: {u.text}" for i, u in enumerate(utterances, 1))


# --- self-corrections and filler -------------------------------------------------

_CORRECTION_RE = re.compile(
    r"(?:\s*(?:\.{2,}|…|—|–|,|-))?\s*\b(?:no,? wait|wait,? no|no no|sorry|scratch that|"
    r"i mean|actually,? no|correction|or rather|make that|let me rephrase)\b[,.:]?\s*",
    re.IGNORECASE,
)
_FILLER_RE = re.compile(
    r"\b(?:u+m+|u+h+|e+r+m*|a+h+|hmm+|mhm|you know|like,|sort of|kind of|kinda|basically|"
    r"literally|honestly|i guess|okay so|ok so|alright so|so yeah|yeah so)\b[,]?\s*|\blike,\s*",
    re.IGNORECASE,
)

_WD = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_MONTH = r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
# Token classes used to decide what a short correction replaces.
_CLASSES = [
    re.compile(rf"\b{_WD}(?: the \d{{1,2}}(?:st|nd|rd|th)?)?\b", re.I),
    re.compile(rf"\b{_MONTH} \d{{1,2}}(?:st|nd|rd|th)?\b", re.I),
    re.compile(r"\$\d[\d,]*(?:\.\d+)?k?\b|\b\d+(?:\.\d+)? ?(?:dollars|bucks|euros)\b", re.I),
    re.compile(r"\b\d+(?:\.\d+)?%?"),
    re.compile(r"\b(?:tomorrow|today|next week|this week|end of (?:the )?(?:week|month))\b", re.I),
    re.compile(r"(?<!^)(?<![.!?] )\b(?!I\b|I')[A-Z][A-Za-z0-9]+\b"),  # proper nouns: Postgres, Raj
]


def _apply_correction(before: str, after: str) -> str:
    clause_m = re.match(r"[^.;!?,]*", after)
    clause = clause_m[0].strip()
    rest = after[len(clause_m[0]) :]
    if not before.strip():
        return after
    for cls in _CLASSES:
        new = cls.match(clause)  # correction starts with a replaceable slot: date, price, name...
        olds = list(cls.finditer(before))
        if new and olds:
            old = olds[-1]
            tail = before[old.end() :] if len(clause.split()) <= 3 else ""
            return before[: old.start()] + clause + tail + rest
    if len(clause.split()) >= 4:
        # Long restatement replaces the abandoned sentence fragment entirely.
        cut = max(before.rfind(". "), before.rfind("? "), before.rfind("! "))
        return (before[: cut + 2] if cut >= 0 else "") + clause + rest
    # No recognisable slot: assume the correction replaces the last word.
    words = before.rstrip(" ,.").rsplit(" ", 1)
    return (words[0] + " " if len(words) > 1 else "") + clause + rest


def resolve_self_corrections(text: str) -> str:
    """'launch Friday... no wait, Monday' -> 'launch Monday'."""
    for _ in range(10):
        m = _CORRECTION_RE.search(text)
        if not m:
            break
        text = _apply_correction(text[: m.start()], text[m.end() :])
    return text


def clean_speech(text: str) -> str:
    text = resolve_self_corrections(text)
    text = _FILLER_RE.sub("", text)
    text = re.sub(r"\b(\w+)( \1\b)+", r"\1", text, flags=re.I)  # stutters: "the the"
    text = re.sub(r"\.{2,}|…", ",", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r",\s*,+", ",", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ,")


# --- topics & keywords (shared with conflict prefiltering) --------------------------

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "launch date": ["launch", "ship", "shipping", "release", "go live", "go-live", "ga ", "public beta", "launch date"],
    "database": ["database", " db", "postgres", "postgresql", "mysql", "mongo", "mongodb", "dynamo",
                 "dynamodb", "sqlite", "supabase", "firebase", "firestore", "redis", "cockroach"],
    "pricing": ["price", "pricing", "$", "per seat", "per month", "a month", "tier", "subscription",
                "free plan", "freemium", "discount", "paywall", "plan costs"],
    "hosting": ["aws", "gcp", "azure", "vercel", "heroku", "kubernetes", "hosting", "cloud provider"],
    "mobile app": ["ios", "android", "mobile app", "app store"],
    "hiring": ["hire", "hiring", "headcount", "recruit"],
    "design": ["design", "figma", "mockup", "ui ", "ux "],
    "marketing": ["marketing", "campaign", "press", "blog post", "social"],
    "authentication": ["auth", "login", "sso", "oauth", "sign-in", "sign in"],
    "testing": ["qa", "testing", "test plan", "beta testers"],
}

STOPWORDS = set(
    """a an the and or but if so to of in on at by for with from up down out over is are was were be
    been being am do does did doing have has had i we you he she they it this that these those my our
    your their its me us them will would can could should shall may might must not no yes just then
    than also very really going gonna wanna get got let lets let's okay ok yeah all any some more most
    about into after before again what which who when where why how there here think know want need
    one two thing things stuff way go said say make made week day today tomorrow next last""".split()
)


def guess_topic(text: str) -> str:
    t = f" {text.lower()} "
    best, hits = None, 0
    for topic, kws in TOPIC_KEYWORDS.items():
        n = sum(1 for k in kws if k in t)
        if n > hits:
            best, hits = topic, n
    if best:
        return best
    kw = keywords(text)
    return kw[0] if kw else "general"


def keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower())
    counts = Counter(w for w in words if w not in STOPWORDS)
    return [w for w, _ in counts.most_common()]
