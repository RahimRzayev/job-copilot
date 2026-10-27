"""Job queries and updates used by the CLI (scoring, status, listing)."""

import json
import sqlite3

from .db import now, row_to_dict
from .normalize import role_key

STATUSES = ("new", "filtered", "closed", "scored", "shortlisted", "applying", "skipped", "needs_input", "ready",
            "applied", "manual", "rejected", "interview", "offer")
TIERS = ("high", "medium", "low")


def list_jobs(conn: sqlite3.Connection, statuses: list[str], limit: int = 200,
              full: bool = False, min_score: int | None = None, order: str = "score",
              desc_chars: int | None = 1500) -> list[dict]:
    marks = ",".join("?" * len(statuses))
    sql = f"SELECT * FROM jobs WHERE status IN ({marks})"
    params: list = list(statuses)
    if min_score is not None:
        sql += " AND score >= ?"
        params.append(min_score)
    sql += (" ORDER BY prescore DESC NULLS LAST, posted_at DESC LIMIT ?" if order == "prescore"
            else " ORDER BY score DESC NULLS LAST, posted_at DESC LIMIT ?")
    params.append(limit)
    rows = [row_to_dict(r) for r in conn.execute(sql, params)]
    if full:
        return rows
    # Compact view for scoring: enough for Claude to judge, small enough to read 100 at once.
    keep = ("id", "title", "company", "location", "remote", "seniority", "skills", "salary",
            "posted_at", "languages", "source", "url", "score", "tier", "status", "liveness",
            "history_note", "history_warn", "employer_note", "prescore", "prescore_why")
    out = []
    for r in rows:
        item = {k: r[k] for k in keep}
        # 1500 chars can hide the requirements of long LinkedIn ads: desc_chars=None keeps everything.
        item["description"] = (r["description"] or "")[:desc_chars]
        out.append(item)
    return out


def get(conn: sqlite3.Connection, job_id: int) -> dict:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(f"no job {job_id}")
    return row_to_dict(row)


def tier_for(score: int) -> str:
    """Tier follows the score (the /find-jobs table): 80+ high, 55-79 medium, below 55 low. Labels written by hand
    drift from the numbers ('medium' at 50), so the number decides."""
    return "high" if score >= 80 else "medium" if score >= 55 else "low"


def set_scores(conn: sqlite3.Connection, scores: list[dict]) -> int:
    """scores: [{id, score, matched, missing, red_flags, reason}]. The tier is derived from the score."""
    n = 0
    for s in scores:
        if s.get("tier") not in (None, *TIERS):
            raise ValueError(f"job {s.get('id')}: tier must be one of {TIERS}")
        notes = {k: s.get(k) for k in ("matched", "missing", "red_flags", "reason")}
        n += conn.execute(
            "UPDATE jobs SET score = ?, tier = ?, score_notes = ?, status = 'scored', scored_at = ? "
            "WHERE id = ? AND status IN ('new', 'scored')",
            (int(s["score"]), tier_for(int(s["score"])), json.dumps(notes, ensure_ascii=False), now(), int(s["id"])),
        ).rowcount
    conn.commit()
    return n


def set_status(conn: sqlite3.Connection, job_ids: list[int], status: str, note: str | None = None) -> int:
    """`note` is what the apply queue shows next to the job ("waiting for your Submit approval", skip reason).
    Shortlisting starts a new queue batch for the job (`shortlisted_at`)."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    marks = ",".join("?" * len(job_ids))
    ts = now()
    n = conn.execute(f"UPDATE jobs SET status = ?, status_at = ?, queue_note = ?, "
                     f"shortlisted_at = CASE WHEN ? = 'shortlisted' THEN ? ELSE shortlisted_at END "
                     f"WHERE id IN ({marks})", [status, ts, note, status, ts, *job_ids]).rowcount
    conn.commit()
    return n


def record_application(conn: sqlite3.Connection, job_id: int, ats: str, answers: dict,
                       cover_letter: str = "", notes: str = "") -> int:
    """Store what was sent and mark the job applied."""
    get(conn, job_id)  # raises if the job doesn't exist
    cur = conn.execute(
        "INSERT INTO applications (job_id, ats, applied_at, answers, cover_letter, notes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, ats, now(), json.dumps(answers, ensure_ascii=False), cover_letter, notes),
    )
    conn.execute("UPDATE jobs SET status = 'applied', status_at = ?, queue_note = ? WHERE id = ?",
                 (now(), f"via {ats}" if ats else None, job_id))
    # Other stored copies of the same role (another board, another city, or the ATS copy of a LinkedIn ad) are the
    # same application: close them so they are not scored or applied to again.
    job = get(conn, job_id)
    key = role_key(job["company"], job["title"])
    for other in conn.execute("SELECT id, company, title FROM jobs WHERE id != ? AND status IN "
                              "('new', 'scored', 'shortlisted', 'applying', 'manual', 'needs_input', 'ready')", (job_id,)).fetchall():
        if role_key(other["company"], other["title"]) == key:
            conn.execute("UPDATE jobs SET status = 'skipped', filter_reason = ? WHERE id = ?",
                         (f"duplicate of #{job_id} (applied)", other["id"]))
    conn.commit()
    return cur.lastrowid


def counts(conn: sqlite3.Connection) -> dict:
    return {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status ORDER BY n DESC")}
