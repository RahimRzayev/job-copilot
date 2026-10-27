"""Ranked HTML report of scored jobs: reports/YYYY-MM-DD.html."""

import html
import sqlite3
from datetime import date
from pathlib import Path

from .db import ROOT
from .jobs import list_jobs

REPORTS = ROOT / "reports"

STYLE = """
:root { --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --card:#fff;
        --high:#15803d; --medium:#b45309; --low:#78716c; --link:#1d4ed8; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --line:#44403c; --card:#292524;
          --high:#4ade80; --medium:#fbbf24; --low:#a8a29e; --link:#93c5fd; } }
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px; background:var(--bg); color:var(--fg);
       font:14px/1.5 -apple-system, system-ui, sans-serif; }
main { max-width:960px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; }
.sub { color:var(--muted); margin-bottom:20px; }
.job { background:var(--card); border:1px solid var(--line); border-radius:10px;
       padding:14px 16px; margin-bottom:10px; display:grid; grid-template-columns:56px 1fr; gap:12px; }
.score { font-size:22px; font-weight:700; text-align:center; }
.score small { display:block; font-size:11px; font-weight:500; text-transform:uppercase; }
.high { color:var(--high); } .medium { color:var(--medium); } .low { color:var(--low); }
.title { font-weight:600; font-size:15px; }
.title a { color:var(--fg); text-decoration:none; } .title a:hover { color:var(--link); }
.meta { color:var(--muted); font-size:13px; }
.reason { margin:6px 0 4px; }
.tags { font-size:12px; } .tags b { font-weight:600; }
.flag { color:var(--medium); }
h2 { font-size:16px; margin:22px 0 10px; }
.badge { font-size:11px; padding:1px 6px; border-radius:8px; border:1px solid var(--line); margin-left:6px; }
details summary { cursor:pointer; color:var(--muted); margin:18px 0 10px; }
code { font-size:12px; background:var(--line); padding:1px 5px; border-radius:4px; }
"""


def _e(value) -> str:
    return html.escape(str(value or ""))


def _card(j: dict) -> str:
    notes = j.get("score_notes") or {}
    tier = j.get("tier") or "low"
    where = j["location"] + (" · remote" if j["remote"] else "")
    meta = " · ".join(_e(x) for x in (j["company"], where, j["salary"], j["source"],
                                        (j["posted_at"] or "")[:10]) if x)
    rows = []
    if notes.get("matched"):
        rows.append(f"<div class='tags'><b>Match:</b> {_e(', '.join(notes['matched']))}</div>")
    if notes.get("missing"):
        rows.append(f"<div class='tags'><b>Gaps:</b> {_e(', '.join(notes['missing']))}</div>")
    if j.get("history_note"):
        cls = "flag" if j.get("history_warn") else ""
        rows.append(f"<div class='tags {cls}'><b>History:</b> {_e(j['history_note'])}</div>")
    if j.get("employer_note"):
        cls = "" if j["employer_note"].endswith("no blockers found") else "flag"
        note = j["employer_note"].removeprefix("company page: ")
        rows.append(f"<div class='tags {cls}'><b>Company page:</b> {_e(note)}</div>")
    if notes.get("red_flags"):
        rows.append(f"<div class='tags flag'><b>Flags:</b> {_e(', '.join(notes['red_flags']))}</div>")
    badges = ""
    if (j.get("scored_at") or "")[:10] == date.today().isoformat():
        badges += "<span class='badge'>scored today</span>"
    badges += ("<span class='badge'>employer link</span>" if j.get("employer_url")
               else "<span class='badge flag'>no employer link</span>")
    return f"""
<div class="job">
  <div class="score {tier}">{_e(j['score'])}<small>{tier}</small></div>
  <div>
    <div class="title"><a href="{_e(j['url'])}" target="_blank" rel="noopener">{_e(j['title'])}</a></div>
    <div class="meta">{meta} · <code>#{j['id']}</code> · {_e(j['status'])}{badges}</div>
    <div class="reason">{_e(notes.get('reason'))}</div>
    {''.join(rows)}
  </div>
</div>"""


def build(conn: sqlite3.Connection, min_score: int = 0) -> Path:
    """Open jobs only (closed/duplicate/applied are not counted); today's new ones are badged.

    Counting every scored job ever made the header promise far more jobs than were still open to apply.
    """
    jobs = [j for j in list_jobs(conn, ["scored", "shortlisted", "applying", "ready", "needs_input"], limit=1000,
                                 full=True, min_score=min_score) if j.get("liveness") != "closed"]
    today = date.today().isoformat()
    todays = [j for j in jobs if (j.get("scored_at") or "")[:10] == today]
    t_tier = {t: sum(1 for j in todays if j["tier"] == t) for t in ("high", "medium", "low")}
    open_jobs = sorted((j for j in jobs if j["tier"] in ("high", "medium")),
                       key=lambda j: (j["status"] != "shortlisted", -(j["score"] or 0)))
    low = [j for j in jobs if j["tier"] not in ("high", "medium")]
    applied_total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    applied_today = conn.execute("SELECT COUNT(*) FROM applications WHERE substr(applied_at, 1, 10) = ?",
                                 (today,)).fetchone()[0]
    shortlisted = sum(1 for j in open_jobs if j["status"] == "shortlisted")
    body = ("<h2>Open to apply (medium/high)</h2>" + ("".join(_card(j) for j in open_jobs) or "<p>None open.</p>")
            + f"<details><summary>{len(low)} low-scored jobs</summary>{''.join(_card(j) for j in low)}</details>")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Matches {today}</title><style>{STYLE}</style></head>
<body><main>
<h1>Job matches · {today}</h1>
<div class="sub">Today: {len(todays)} new scored ({t_tier['high']} high · {t_tier['medium']} medium · {t_tier['low']} low).
Open to apply: {len(open_jobs)} medium/high ({shortlisted} shortlisted). Applied: {applied_today} today, {applied_total} total.
Apply with <code>/apply &lt;id&gt;</code>.</div>
{body}
</main></body></html>"""
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{today}.html"
    path.write_text(page)
    return path
