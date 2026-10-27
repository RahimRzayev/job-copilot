"""Apply queue: the latest shortlist batch and where each job stands (`jobhunt queue`, `/shortlist`).

Read-only on purpose: the user watches it in another terminal while /apply runs, so it must never lock the DB.
"""

import sqlite3
from datetime import datetime, timedelta

from .db import DB_PATH, connect

PENDING = ("shortlisted", "applying", "needs_input")
# Jobs shortlisted within this time before the batch anchor belong to the same batch (/find-jobs and
# /linkedin-alerts usually shortlist within an hour or two of each other).
BATCH_HOURS = 12
# Display groups in order: (title, statuses).
GROUPS = [
    ("Applying now", ("applying",)),
    ("Waiting for you", ("needs_input",)),
    ("In queue", ("shortlisted",)),
    ("Applied", ("applied", "interview", "offer", "rejected")),
    ("You apply yourself", ("manual",)),
    ("Skipped", ("skipped", "closed")),
]


def connect_ro(path=DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    if "shortlisted_at" not in {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}:
        conn.close()
        connect(path).close()  # adds the queue columns once on an older database
        return connect_ro(path)
    return conn


def batch(conn: sqlite3.Connection) -> dict:
    """The current batch: anchored at the oldest job still pending, else at the newest shortlisting."""
    marks = ",".join("?" * len(PENDING))
    anchor = conn.execute(f"SELECT MIN(shortlisted_at) FROM jobs WHERE status IN ({marks}) "
                          f"AND shortlisted_at IS NOT NULL", PENDING).fetchone()[0]
    if not anchor:
        anchor = conn.execute("SELECT MAX(shortlisted_at) FROM jobs").fetchone()[0]
    if not anchor:
        return {"started": None, "groups": {title: [] for title, _ in GROUPS}, "total": 0}
    since = (datetime.fromisoformat(anchor) - timedelta(hours=BATCH_HOURS)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT id, company, title, score, status, status_at, shortlisted_at, queue_note, filter_reason "
        "FROM jobs WHERE shortlisted_at >= ? ORDER BY score DESC, id", (since,)).fetchall()
    groups: dict[str, list[dict]] = {title: [] for title, _ in GROUPS}
    other: list[dict] = []
    for r in rows:
        job = dict(r)
        title = next((t for t, statuses in GROUPS if job["status"] in statuses), None)
        (groups[title] if title else other).append(job)
    for title in ("Applied", "You apply yourself", "Skipped"):
        groups[title].sort(key=lambda j: j["status_at"] or "")  # in the order they were finished
    if other:
        groups["Other"] = other
    return {"started": min(r["shortlisted_at"] for r in rows), "groups": groups, "total": len(rows)}


# Status column: one icon + one word, so the table reads at a glance (grouped plain text looked like code).
ICONS = {
    "applied": ("✅", "Applied"), "interview": ("⭐", "Interview"), "offer": ("🎉", "Offer"),
    "rejected": ("⛔", "Rejected"), "applying": ("🔄", "Applying now"), "needs_input": ("✋", "Needs you"),
    "shortlisted": ("⏳", "In queue"), "manual": ("👤", "You apply"), "skipped": ("❌", "Skipped"),
    "closed": ("❌", "Closed"),
}


def _short(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def rows(data: dict) -> list[dict]:
    """One row per job, in group order (applying now first, skipped last), numbered 1..N."""
    out = []
    for title in [t for t, _ in GROUPS] + (["Other"] if "Other" in data["groups"] else []):
        for j in data["groups"].get(title) or []:
            icon, label = ICONS.get(j["status"], ("•", j["status"].replace("_", " ").title()))
            note = ""  # applied rows need none ("via workday" was noise)
            if j["status"] in ("applying", "needs_input", "manual", "skipped", "closed"):
                note = j["queue_note"] or (j["filter_reason"] if j["status"] in ("skipped", "closed") else "") or ""
            out.append({"n": len(out) + 1, "id": j["id"], "icon": icon, "label": label, "company": j["company"],
                        "role": j["title"], "score": j["score"], "note": note})
    return out


def _header(data: dict) -> tuple[str, str, str]:
    g = data["groups"]
    done = sum(len(g[t]) for t in ("Applied", "You apply yourself", "Skipped"))
    filled = round(10 * done / data["total"])
    bar = "🟩" * filled + "⬜" * (10 - filled)
    started = datetime.fromisoformat(data["started"]).astimezone().strftime("%d %b %H:%M")
    counts: dict[str, int] = {}
    for r in rows(data):
        key = f"{r['icon']} {r['label'].lower()}"
        counts[key] = counts.get(key, 0) + 1
    return (f"Shortlist {started} · {data['total']} jobs · {done}/{data['total']} done", bar,
            "   ".join(f"{k.split(' ', 1)[0]} {v} {k.split(' ', 1)[1]}" for k, v in counts.items()))


def _esc(text: str) -> str:
    return text.replace("|", "\\|")  # titles like "Contoso Lab | Developer path"


def markdown(data: dict) -> str:
    """For /shortlist: Claude prints this as-is, so Claude Code renders a real table (not a code block)."""
    if not data["total"]:
        return "No shortlist yet. `/find-jobs` or `/linkedin-alerts` creates one.\n"
    title, bar, counts = _header(data)
    lines = [f"**{title}**", "", f"{bar}  {counts}", "",
             "| # | Status | Company | Role | Score | Note |", "|---|---|---|---|---|---|"]
    for r in rows(data):
        lines.append(f"| {r['n']} | {r['icon']} {r['label']} | {_esc(_short(r['company'], 28))} | "
                     f"{_esc(_short(r['role'], 48))} | {r['score'] if r['score'] is not None else ''} | "
                     f"{_esc(_short(r['note'], 48))} |")
    return "\n".join(lines) + "\n"


def render(data: dict, width: int = 120, now: datetime | None = None) -> str:
    """Terminal table for `jobhunt queue --watch` (same rows as the /shortlist table)."""
    if not data["total"]:
        return "No shortlist yet. /find-jobs or /linkedin-alerts creates one.\n"
    title, bar, counts = _header(data)
    table = rows(data)
    cw = min(24, max(len("Company"), *(len(r["company"]) for r in table)))
    rw = min(44, max(len("Role"), *(len(r["role"]) for r in table)))
    lw = max(len(r["label"]) for r in table)
    lines = [title, f"{bar}  {counts}", "",
             f" #   {'Status':<{lw + 5}}{'Company':<{cw}}  {'Role':<{rw}}  Score  Note"]
    for r in table:
        score = str(r["score"]) if r["score"] is not None else ""
        line = (f"{r['n']:>2}   {r['icon']} {r['label']:<{lw}}  {_short(r['company'], cw):<{cw}}  "
                f"{_short(r['role'], rw):<{rw}}  {score:>5}  {_short(r['note'], 40)}")
        lines.append(line.rstrip()[:width])
    return "\n".join(lines) + "\n"
