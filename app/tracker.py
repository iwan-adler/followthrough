"""Task tracker integration: GitHub Issues when GITHUB_TOKEN + GITHUB_REPO are set,
otherwise a local mock tracker table that behaves the same in the UI.

Only called after the confirmation gate; nothing here runs on extraction or edits.
"""
from __future__ import annotations

from datetime import date

import httpx

from . import db
from .config import settings

GITHUB_API = "https://api.github.com"


def mode() -> str:
    s = settings()
    return "github" if s.github_token and s.github_repo else "mock"


def info() -> dict:
    return {"mode": mode(), "repo": settings().github_repo if mode() == "github" else None}


def _assignee(owner: str | None) -> str | None:
    """GITHUB_USER_MAP="Priya=priya-gh,Raj=rajdev" maps meeting names to GitHub logins."""
    for pair in settings().github_user_map.split(","):
        name, _, login = pair.partition("=")
        if owner and name.strip().lower() == owner.lower() and login.strip():
            return login.strip()
    return None


def issue_body(item: dict, meeting: dict, evidence: str = "") -> str:
    due = date.fromisoformat(item["deadline"]).strftime("%Y-%m-%d (%a)") if item.get("deadline") else "_not set_"
    lines = [
        f"**Owner:** {item.get('owner') or '_unassigned_'}",
        f"**Due:** {due}",
        f"**From meeting:** {meeting['title']} ({meeting['meeting_date']})",
    ]
    if evidence:
        lines += ["", f"> {evidence}"]
    lines += ["", "_Created by Followthrough after human review and confirmation._"]
    return "\n".join(lines)


def _github(method: str, path: str, json: dict) -> dict:
    s = settings()
    resp = httpx.request(
        method, f"{GITHUB_API}{path}", json=json, timeout=10,
        headers={"Authorization": f"Bearer {s.github_token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"GitHub {resp.status_code}: {resp.json().get('message', resp.text)[:200]}")
    return resp.json()


def post(item_id: int, evidence: str = "", local_only: bool = False) -> dict:
    """Create the ticket for one confirmed action item and record where it went.
    local_only keeps it in the mock tracker even when GitHub is configured (seeded demo data)."""
    item = db.row("SELECT * FROM action_items WHERE id = ?", (item_id,))
    meeting = db.row("SELECT * FROM meetings WHERE id = ?", (item["meeting_id"],))
    title, body = item["title"], issue_body(item, meeting, evidence)
    where = "mock" if local_only else mode()
    try:
        if where == "github":
            payload: dict = {"title": title, "body": body, "labels": ["followthrough"]}
            if login := _assignee(item["owner"]):
                payload["assignees"] = [login]
            issue = _github("POST", f"/repos/{settings().github_repo}/issues", payload)
            url, ref = issue["html_url"], f"#{issue['number']}"
        else:
            tid = db.execute(
                "INSERT INTO mock_tracker (action_item_id, title, body, assignee, due) VALUES (?, ?, ?, ?, ?)",
                (item_id, title, body, item["owner"], item["deadline"]),
            )
            url, ref = f"mock://FT-{tid}", f"FT-{tid}"
        db.execute("UPDATE action_items SET tracker = ?, tracker_url = ?, tracker_ref = ?, tracker_error = NULL "
                   "WHERE id = ?", (where, url, ref, item_id))
    except Exception as e:  # keep the task; the UI offers a retry
        db.execute("UPDATE action_items SET tracker = ?, tracker_error = ? WHERE id = ?",
                   (where, str(e)[:300], item_id))
    return db.row("SELECT * FROM action_items WHERE id = ?", (item_id,))


def set_closed(item: dict, closed: bool) -> None:
    """Mirror done/open to the tracker. Best effort: the local status is the source of truth."""
    if item.get("tracker") == "github" and (item.get("tracker_ref") or "").startswith("#") and mode() == "github":
        try:
            _github("PATCH", f"/repos/{settings().github_repo}/issues/{item['tracker_ref'][1:]}",
                    {"state": "closed" if closed else "open"})
        except Exception:
            pass


def close_obsolete(item_id: int, reason: str) -> dict:
    """Close a task made obsolete by a superseded decision, leaving the reason on the ticket."""
    db.execute("UPDATE action_items SET status = 'cancelled', completed_at = datetime('now'), "
               "closed_reason = ? WHERE id = ?", (reason, item_id))
    item = db.row("SELECT * FROM action_items WHERE id = ?", (item_id,))
    ref = item.get("tracker_ref") or ""
    try:
        if item.get("tracker") == "github" and ref.startswith("#") and mode() == "github":
            repo = settings().github_repo
            _github("POST", f"/repos/{repo}/issues/{ref[1:]}/comments", {"body": f"Closed by Followthrough. {reason}"})
            _github("PATCH", f"/repos/{repo}/issues/{ref[1:]}", {"state": "closed", "state_reason": "not_planned"})
        elif ref.startswith("FT-"):
            db.execute("UPDATE mock_tracker SET body = body || ? WHERE id = ?",
                       (f"\n\n**Closed as obsolete.** {reason}", int(ref[3:])))
    except Exception:  # local status already records it; tracker sync is best effort
        pass
    return item


def check() -> dict:
    """Can we actually create issues? Used by the demo preflight."""
    if mode() == "mock":
        return {"status": "warn", "detail": "Local mock tracker (no GITHUB_TOKEN / GITHUB_REPO set)",
                "fix": "Optional: set GITHUB_TOKEN and GITHUB_REPO in .env to post real GitHub issues."}
    s = settings()
    try:
        resp = httpx.get(f"{GITHUB_API}/repos/{s.github_repo}", timeout=8, headers={
            "Authorization": f"Bearer {s.github_token}", "Accept": "application/vnd.github+json"})
    except Exception as e:
        return {"status": "fail", "detail": f"GitHub unreachable: {str(e)[:120]}", "fix": "Check the network."}
    if resp.status_code == 401:
        return {"status": "fail", "detail": "GitHub rejected the token", "fix": "Create a new token and update GITHUB_TOKEN."}
    if resp.status_code == 404:
        return {"status": "fail", "detail": f"Repo {s.github_repo} not found, or the token can't see it",
                "fix": "Check GITHUB_REPO (owner/name) and give the token access to that repo."}
    repo = resp.json()
    if not repo.get("has_issues", True):
        return {"status": "fail", "detail": "Issues are disabled on the repo", "fix": "Enable Issues in the repo's settings."}
    if repo.get("permissions") and not repo["permissions"].get("push"):
        return {"status": "fail", "detail": "Token can read but not create issues",
                "fix": "Give the token Issues: Read and write on this repo."}
    return {"status": "ok", "detail": f"GitHub {s.github_repo}: issues can be created", "fix": ""}


def mock_tickets() -> list[dict]:
    return db.rows("SELECT * FROM mock_tracker ORDER BY id DESC")
