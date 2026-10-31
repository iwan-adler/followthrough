"""Score Followthrough's extraction and conflict detection on hand-labelled messy meetings.

    python eval/run_eval.py            # offline rules, plus the live model if LLM_API_KEY is set
    python eval/run_eval.py --offline  # offline rules only

Writes eval/RESULTS.md. Every live call is made fresh (cache off), and any call that fell
back to the offline rules is counted and reported, so the live numbers are honest.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, llm, memory  # noqa: E402
from app.config import settings  # noqa: E402
from app.extraction import ExtractedDecision, extract  # noqa: E402

CASES = json.loads((ROOT / "eval" / "cases.json").read_text(encoding="utf-8"))
MEETING_DATE = date.fromisoformat(CASES["meeting_date"])
PAUSE_S = 2.0  # stay under free-tier rate limits


def _has_all(text: str, needles: list[str]) -> bool:
    return all(n.lower() in text.lower() for n in needles)


def _mentions(title: str, keywords: list[str]) -> bool:
    """Task keywords match word forms too: "docs" ~ "documentation", "rules" ~ "rule"."""
    words = title.lower().replace("-", " ").split()
    stems = [k.lower().rstrip("s")[:5] for k in keywords]
    return all(any(w.startswith(s) for w in words) for s in stems)


def score_extraction(log: list[str], fallbacks: list[int]) -> dict:
    t = {"expected": 0, "extracted": 0, "matched": 0, "owner_ok": 0, "deadline_ok": 0}
    d = {"expected": 0, "extracted": 0, "matched": 0}
    corrections = {"expected": 0, "ok": 0}
    for case in CASES["extraction"]:
        _, ex = extract(case["transcript"], MEETING_DATE, case["id"])
        if llm.provider_info()["last_source"] == "mock-fallback":
            fallbacks[0] += 1
        t["extracted"] += len(ex.action_items)
        d["extracted"] += len(ex.decisions)

        free = list(ex.action_items)
        for want in case["tasks"]:
            t["expected"] += 1
            got = next((a for a in free if _mentions(a.title, want["keywords"])), None)
            if not got:
                log.append(f"{case['id']}: missed task {want['keywords']}")
                continue
            free.remove(got)
            t["matched"] += 1
            owner_ok = (got.owner or None) == want["owner"] or (
                got.owner and want["owner"] and got.owner.lower() == want["owner"].lower())
            t["owner_ok"] += bool(owner_ok)
            t["deadline_ok"] += got.deadline == want["deadline"]
            if not owner_ok:
                log.append(f"{case['id']}: “{got.title}” owner {got.owner!r}, expected {want['owner']!r}")
            if got.deadline != want["deadline"]:
                log.append(f"{case['id']}: “{got.title}” due {got.deadline}, expected {want['deadline']}")

        matched_decisions = []
        for want in case["decisions"]:
            d["expected"] += 1
            got = next((x for x in ex.decisions if _has_all(x.text, want["must_contain"])), None)
            if got:
                matched_decisions.append(got)
            if want["must_not_contain"]:
                corrections["expected"] += 1
            if not got:
                log.append(f"{case['id']}: missed decision containing {want['must_contain']}")
                continue
            d["matched"] += 1
            if want["must_not_contain"]:
                leaked = [w for w in want["must_not_contain"] if w.lower() in got.text.lower()]
                corrections["ok"] += not leaked
                if leaked:
                    log.append(f"{case['id']}: decision “{got.text}” still mentions {leaked}")
        for extra in (x for x in ex.decisions if x not in matched_decisions):
            log.append(f"{case['id']}: extra decision (unlabelled, counts against precision): “{extra.text}”")
        for extra in free:
            log.append(f"{case['id']}: extra task (unlabelled, counts against precision): “{extra.title}”")
        if case["id"] == "owner-self-correction":  # the abandoned owner must not win
            corrections["expected"] += 1
            corrections["ok"] += all((a.owner or "") != "Sam" for a in ex.action_items)
        time.sleep(PAUSE_S if llm._live() else 0)
    return {"tasks": t, "decisions": d, "corrections": corrections}


def score_conflicts(log: list[str], fallbacks: list[int]) -> dict:
    m = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for case in CASES["conflicts"]:
        db.reset()
        old_meeting = db.create_meeting("Earlier meeting", "2026-10-19", "x", [], "", {})
        db.execute("UPDATE meetings SET status = 'confirmed' WHERE id = ?", (old_meeting,))
        for e in case["earlier"]:
            db.execute("INSERT INTO decisions (meeting_id, text, topic, speaker) VALUES (?, ?, ?, 'Priya')",
                       (old_meeting, e["text"], e["topic"]))
        new_meeting = db.create_meeting("New meeting", MEETING_DATE.isoformat(), "x", [], "", {})
        new = [ExtractedDecision(text=n["text"], topic=n["topic"], speaker="Raj", confidence=0.9) for n in case["new"]]
        found = memory.detect_conflicts(new_meeting, MEETING_DATE, new)
        if found and llm.provider_info()["last_source"] == "mock-fallback":
            fallbacks[0] += 1
        flagged = {c["new_decision_index"] for c in found}
        for i, n in enumerate(case["new"]):
            expected = n["topic"] in case["expected_conflicts"]
            hit = i in flagged
            m[("tp" if expected else "fp") if hit else ("fn" if expected else "tn")] += 1
            if hit != expected:
                log.append(f"{case['id']}: “{n['text']}” {'flagged' if hit else 'not flagged'}, expected "
                           f"{'conflict' if expected else 'no conflict'}")
        time.sleep(PAUSE_S if llm._live() else 0)
    return m


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "n/a"


def run(mode: str) -> dict:
    if mode == "offline":
        os.environ["LLM_API_KEY"] = ""
    os.environ["LLM_CACHE"] = "false"
    llm._sticky.clear()
    llm._dead.clear()
    log, fallbacks = [], [0]
    started = time.time()
    ex = score_extraction(log, fallbacks)
    cf = score_conflicts(log, fallbacks)
    name = "Offline rules" if mode == "offline" else f"{settings().llm_provider} ({llm.provider_info()['model']})"
    return {"name": name, "ex": ex, "cf": cf, "log": log, "fallbacks": fallbacks[0], "secs": time.time() - started}


def report(results: list[dict]) -> str:
    rows = [
        ("Action items found (recall)", lambda r: pct(r["ex"]["tasks"]["matched"], r["ex"]["tasks"]["expected"])),
        ("Action items correct (precision)", lambda r: pct(r["ex"]["tasks"]["matched"], r["ex"]["tasks"]["extracted"])),
        ("Owner correct", lambda r: pct(r["ex"]["tasks"]["owner_ok"], r["ex"]["tasks"]["matched"])),
        ("Deadline resolved to the right date", lambda r: pct(r["ex"]["tasks"]["deadline_ok"], r["ex"]["tasks"]["matched"])),
        ("Decisions found (recall)", lambda r: pct(r["ex"]["decisions"]["matched"], r["ex"]["decisions"]["expected"])),
        ("Decisions correct (precision)", lambda r: pct(r["ex"]["decisions"]["matched"], r["ex"]["decisions"]["extracted"])),
        ("Self-corrections resolved to final intent", lambda r: pct(r["ex"]["corrections"]["ok"], r["ex"]["corrections"]["expected"])),
        ("Conflicts caught (recall)", lambda r: pct(r["cf"]["tp"], r["cf"]["tp"] + r["cf"]["fn"])),
        ("Conflict flags correct (precision)", lambda r: pct(r["cf"]["tp"], r["cf"]["tp"] + r["cf"]["fp"])),
        ("Conflict judgements correct (accuracy)", lambda r: pct(r["cf"]["tp"] + r["cf"]["tn"], sum(r["cf"].values()))),
    ]
    head = "| Metric | " + " | ".join(r["name"] for r in results) + " |"
    lines = [
        "# Followthrough accuracy eval",
        "",
        f"{len(CASES['extraction'])} messy spoken meetings and {len(CASES['conflicts'])} conflict scenarios, labelled by hand "
        "(`eval/cases.json`): self-corrections of dates, prices, owners and technologies; recap-style and one-paragraph "
        "dictation; filler; a rejected proposal; hedged ideas; WebVTT; relative dates; unowned work. "
        "Conflict scenarios include true reversals and look-alikes that must **not** be flagged "
        "(unchanged values, different aspects of a topic, restatements).",
        "",
        head,
        "|" + "---|" * (len(results) + 1),
        *[f"| {label} | " + " | ".join(fn(r) for r in results) + " |" for label, fn in rows],
        "| Calls that fell back to offline rules | " + " | ".join(
            "n/a" if r["name"] == "Offline rules" else str(r["fallbacks"]) for r in results) + " |",
        "| Run time | " + " | ".join(f"{r['secs']:.0f}s" for r in results) + " |",
        "",
        "Reproduce: `python eval/run_eval.py` (uses `LLM_API_KEY` from `.env` if set; `--offline` for rules only).",
        "",
        "**Read the offline column with care.** The offline rules were improved using these same cases, so for them "
        "this is a regression suite rather than evidence they generalise. Nothing was tuned for the live model, so its "
        "column is the honest measure. Twelve meetings is a small set; treat the numbers as directional.",
        "",
    ]
    for r in results:
        lines += [f"## Misses: {r['name']}", ""] + ([f"- {x}" for x in r["log"]] or ["- none"]) + [""]
    return "\n".join(lines)


def _merge(a: dict, b: dict) -> dict:
    """Pool counts from repeated runs (live models vary run to run)."""
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(a[k], v) if isinstance(v, dict) else a[k] + v if isinstance(v, (int, float)) else a[k] + v if isinstance(v, list) else a[k]
    return out


def main() -> None:
    tmp = tempfile.mkdtemp()
    os.environ["DB_PATH"] = str(Path(tmp) / "eval.db")
    os.environ["SCHEDULER_ENABLED"] = "false"
    db.init_db()
    runs = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 1
    modes = ["offline"] if "--offline" in sys.argv or not settings().llm_api_key else ["live", "offline"]
    results = []
    for mode in modes:
        pooled = None
        for n in range(runs if mode == "live" else 1):
            print(f"Running {mode} ({n + 1}/{runs if mode == 'live' else 1})…", flush=True)
            r = run(mode)
            pooled = r if pooled is None else {**_merge(pooled, r), "name": pooled["name"]}
        if mode == "live" and runs > 1:
            pooled["name"] += f", {runs} runs pooled"
        results.append(pooled)
    results.sort(key=lambda r: r["name"] == "Offline rules")
    md = report(results)
    (ROOT / "eval" / "RESULTS.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
