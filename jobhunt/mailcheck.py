"""/mail-check: sort the unread Gmail inbox of the job-search address (settings.job_email).

Rows come from Gmail's list view (browser/gmail_rows_bodies.js on the `is:unread` search): sender, subject, snippet,
date, thread id, and `body` = the newest message's text from the print view (reading it does not mark the thread
read). Classification uses the body when present: a ~150-char snippet can hide a rejection that opens like an
"application received" note. Every row lands in one bucket:

- keep      : needs the user (interview, assessment/test, offer, a recruiter asking something, a deadline). Stays
              unread and is listed in the report.
- job_info  : application received / rejection / status update / account created / old codes. Recorded in the
              Gmail history (and job statuses synced), then marked read. Rejections are still named in the report.
- noise     : job alerts, newsletters, promotions, receipts, social notifications. Marked read.
- unknown   : rules can't tell. Claude reads it (subject/snippet, print view if needed) and decides; default keep.

Marking read happens in the browser (browser/gmail_mark_read.js). Nothing is ever deleted, archived, moved or sent.
Every run writes data/inbox/mailcheck-<date>.json (what was marked read, with thread ids) so mistakes can be undone.
"""

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from . import history, settings
from .db import ROOT, now

QUERY = "is:unread"
LOG_DIR = ROOT / "data" / "inbox"

# Questionnaire requests, Polish ones included: "Wypełnij kwestionariusz, aby dokończyć swoją aplikację" ("fill in the
# questionnaire to finish your application") must not be marked read as a confirmation.
ASK_EXTRA = r"|(wypełnij|uzupełnij|rozwiąż) (\w+ )?(kwestionariusz|formularz|test|zadani)|dokończ\w* (\w+ )?aplikacj|" \
            r"complete (the|a|our|this|short) (\w+ )?questionnaire"

# Order matters: the first matching group wins. "keep" is checked before anything that would mark read.
KEEP = re.compile(
    r"(interview|rozmow[aęy] (rekrutacyjn|kwalifikacyjn)|zaproszenie|invit(e|ation) (you )?to|"
    r"schedule (a|an|your) |book (a|your) (slot|time)|calendly|availability|when are you available|"
    r"assessment|online test|technical test|coding (task|challenge|test)|hackerrank|codility|testgorilla|"
    r"hirevue|video interview|recorded interview|task for you|zadanie|"
    r"offer letter|pleased to offer|job offer|oferta zatrudnienia|"
    r"next steps?|following steps|action (required|needed)|please (complete|provide|send|submit|confirm|reply|"
    r"respond|choose|select|upload|fill)|complete your application|missing (information|documents?)|"
    r"documents? (required|needed)|deadline|by (monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"reference check|background check|onboarding|could you (possibly |please )?(specify|tell|send|confirm|share|let)|"
    r"can you (please )?(specify|tell|send|confirm|share|let)|would you be available|are you (still )?interested|"
    r"looking forward to your (reply|response|answer)" + ASK_EXTRA + ")", re.I)
NOISE_SENDERS = re.compile(
    r"(jobalerts-noreply@linkedin|jobs-listings@linkedin|messages-noreply@linkedin|notifications-noreply@linkedin|"
    r"jobs-noreply@linkedin|news@|newsletter|marketing|promo|offers@|deals@|@email\.|@e\.|@mail\.|@news\.|"
    r"@info\.|alert|digest|noreply@medium|@substack|@quora|glassdoor|indeed\.com|jooble|careerjet|"
    r"jobs2web\.)", re.I)
NOISE_TEXT = re.compile(
    r"(job alert|jobs? for you|new jobs? (matching|for)|recommended jobs|\d+% match|top job picks|"
    r"oferty dla ciebie|nowe oferty|newsletter|webinar|unsubscribe|digest|weekly|%\s*off|discount|promo(tion)?|"
    r"sale\b|deal(s)?\b|black friday|your (order|receipt|invoice|purchase|payment|subscription|trial)|"
    r"order (confirmed|shipped)|has (shipped|been delivered)|delivery|faktura|zamówieni|"
    r"is hiring|people you may know|viewed your profile|new connection|endorse|congratulate|trending|"
    r"tips|career advice|learning|course|certificate earned|survey|feedback on|rate your|how did we do|"
    r"shared some google account data|help us match you|keep your (details|profile) up to date|"
    r"share your (recruitment|candidate) experience)", re.I)
JOB_INFO_KINDS = {"applied", "rejection", "status", "account"}
# With a full body, confirmations mention "interview", "next steps", "availability" while describing the process
# ("we'll be in touch ... if we'd like to meet you for an interview"). For confirmation-type mail only a direct
# request keeps it unread.
ASKS = re.compile(
    r"(please (complete|provide|send|submit|reply|respond|choose|select|upload|fill)|action (required|needed)|"
    r"complete your application|missing (information|documents?)|documents? (required|needed)|"
    r"could you (possibly |please )?(specify|tell|send|confirm|share|let)|can you (please )?(specify|tell|send|confirm|"
    r"share|let)|would you be available|are you (still )?interested|deadline" + ASK_EXTRA + ")", re.I)
# Confirmations the history classifier doesn't name (Polish thanks, account welcomes).
CONFIRMATION = re.compile(
    r"(podziękowanie za (zainteresowanie|aplikacj|przesłan)|dziękujemy za|aplikacj\w* (na|w odpowiedzi)|"
    r"creating an account|account (has been )?(created|activated)|welcome to (the )?\w+ (careers?|talent|candidate)|"
    r"registration (complete|successful)|profile (created|updated)|talent community)", re.I)
CODE_TEXT = re.compile(r"(one[- ]time pass(code|word)|verification code|security code|kod weryfikacyjny|"
                       r"\botp\b|confirm your email|verify your email|activate your account)", re.I)
SECURITY_TEXT = re.compile(r"(security alert|new sign-in|sign-in attempt|password (was )?(changed|reset)|"
                           r"suspicious|2-step|recovery (email|phone))", re.I)


def classify(row: dict, today: date | None = None) -> tuple[str, str]:
    """-> (bucket, reason). Buckets: keep | job_info | noise | unknown."""
    today = today or date.today()
    subject = (row.get("subject") or "").replace("​", "")
    snippet = (row.get("snippet") or "").replace("​", "")
    body = (row.get("body") or "").replace("​", "")
    sender = f"{row.get('sender_name', '')} <{row.get('sender_email', '')}>"
    text = f"{subject} {body or snippet}"
    # Footers and boilerplate ("next steps", "security", "verify your email") sit deep in bodies: the keep, code and
    # security rules only look at the start, as they did with the snippet.
    head = f"{subject} {(body[:400] if body else snippet)}"
    short = f"{subject} {snippet}"  # security/code wording in a body is usually footer boilerplate (SmartRecruiters)
    kind = history.classify(subject, snippet, body)

    if SECURITY_TEXT.search(short):
        return "keep", "account security notice"
    if kind in ("interview", "test", "offer"):
        return "keep", kind
    if CODE_TEXT.search(short):
        when = history.parse_gmail_date(row.get("date", ""))
        try:
            age = (today - date.fromisoformat(when)).days
        except ValueError:
            age = 0
        return ("job_info", "old verification code") if age >= 1 else ("keep", "fresh verification code")
    if kind == "alert":
        return "noise", "job alert / newsletter sender"
    if kind == "rejection":  # before the sender check: some rejections come from a marketing-style address
        return "job_info", "rejection"
    if NOISE_SENDERS.search(sender):
        return "noise", "job alert / newsletter sender"
    if (ASKS if body and kind in ("applied", "status", "account") else KEEP).search(head):
        return "keep", "asks for something / next steps"
    if kind in JOB_INFO_KINDS:
        return "job_info", kind
    if CONFIRMATION.search(text):
        return "job_info", "confirmation / account"
    if NOISE_TEXT.search(text):
        return "noise", "promotion / notification"
    return "unknown", ""


def plan(rows: list[dict], today: date | None = None) -> dict:
    out = {"keep": [], "job_info": [], "noise": [], "unknown": []}
    seen = set()
    for r in rows:
        tid = r.get("thread_id") or ""
        if tid and tid in seen:
            continue
        seen.add(tid)
        bucket, reason = classify(r, today)
        out[bucket].append({**{k: r.get(k, "") for k in ("thread_id", "date", "sender_name", "sender_email",
                                                           "subject", "snippet")}, "reason": reason,
                            "has_body": bool(r.get("body"))})
    return out


def sync_job_statuses(conn: sqlite3.Connection) -> list[dict]:
    """Applied jobs whose company later sent an interview/test/offer or rejection for that role get their status
    updated (applied -> interview | rejected | offer). A mail that names no role counts only when it is the single
    open application at that company."""
    changes = []
    active = conn.execute("SELECT j.id, j.company, j.title, j.status, MAX(a.applied_at) AS applied_at FROM jobs j "
                          "JOIN applications a ON a.job_id = j.id WHERE j.status IN ('applied', 'interview') "
                          "GROUP BY j.id").fetchall()
    per_company: dict[str, int] = {}
    for job in active:
        core = history.company_core(job["company"])
        per_company[core] = per_company.get(core, 0) + 1
    # Every application ever made, closed ones too: once one of two applications at a company is rejected, the other
    # becomes "the only open one" and must not take the same rejection.
    applied_titles: dict[str, list[tuple[int, str]]] = {}
    for r in conn.execute("SELECT DISTINCT j.id, j.company, j.title FROM jobs j JOIN applications a ON a.job_id = j.id"):
        applied_titles.setdefault(history.company_core(r["company"]), []).append((r["id"], r["title"]))
    for job in active:
        since = (job["applied_at"] or "")[:10]
        only_one = per_company[history.company_core(job["company"])] == 1
        others = [t for i, t in applied_titles.get(history.company_core(job["company"]), []) if i != job["id"]]
        for h in history.for_company(conn, job["company"], job["title"]):
            if h["date"] < since:
                continue
            if not h["same_role"] and any(history._same_role(t, h["role_text"]) for t in others):
                continue  # about another application at this company
            new = {"offer": "offer", "interview": "interview", "test": "interview"}.get(h["kind"])
            # A rejection that names no role ("Regarding your application") can only mean the one open application
            # at that company; with several open ones it stays a note for Claude to resolve. A mail naming the title
            # without its qualifiers ("AI Engineer" for "AI Engineer (LLM automation…), part-time, remote") counts
            # the same way, only for the single open application.
            ours = h["same_role"] or (only_one and (not h["names_other_role"] or h["core_role"]))
            if h["kind"] == "rejection" and ours:
                new = "rejected"
            # Same guard for invites: an assessment for one role must not flip another open application at the same
            # company to interview.
            if new in ("offer", "interview") and not ours:
                new = None
            if new and new != job["status"] and not (job["status"] == "interview" and new == "interview"):
                conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (new, job["id"]))
                changes.append({"id": job["id"], "company": job["company"], "title": job["title"],
                                "from": job["status"], "to": new, "email": h["subject"][:80], "date": h["date"]})
                break
    changes += _sync_by_role_only(conn, [j for j in active if j["id"] not in {c["id"] for c in changes}], active)
    conn.commit()
    return changes


def _sync_by_role_only(conn: sqlite3.Connection, todo: list, active: list) -> list[dict]:
    """Mails sent through an ATS that name only the role, never the company ("Thank you for your interest in the
    Engineer, Integrations position"). Such a mail counts when its role matches exactly one open application and names
    none of the companies applied to."""
    changes = []
    rows = conn.execute("SELECT * FROM app_history WHERE kind IN ('rejection', 'interview', 'test', 'offer') "
                        "ORDER BY msg_date DESC").fetchall()
    # Every company ever applied to, not only the open ones: a rejection naming a closed application's company must
    # not flip a similar role at another company.
    applied_cores = {history.company_core(r["company"]) for r in conn.execute(
        "SELECT DISTINCT j.company FROM jobs j JOIN applications a ON a.job_id = j.id")}
    applied_cores.discard("")
    for row in rows:
        domain = (row["sender_email"] or "").split("@")[-1]
        if not any(p in domain for p in history.PLATFORM_DOMAINS):
            continue
        role_text = f"{row['subject']} {row['snippet']} {(row['body'] or '')[:600]}"
        hay = f"{history._haystack(row)} {history.fold(role_text)} "
        if any(f" {core} " in hay for core in applied_cores):
            continue  # names a company: the normal company match handles it (or it is about another company)
        matches = [j for j in active if history._same_role(j["title"], role_text)]
        if len(matches) != 1 or matches[0] not in todo or row["msg_date"] < (matches[0]["applied_at"] or "")[:10]:
            continue
        job = matches[0]
        new = {"rejection": "rejected", "offer": "offer"}.get(row["kind"], "interview")
        if new == job["status"]:
            continue
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (new, job["id"]))
        changes.append({"id": job["id"], "company": job["company"], "title": job["title"], "from": job["status"],
                        "to": new, "email": row["subject"][:80], "date": row["msg_date"], "matched_by": "role only"})
        todo = [j for j in todo if j["id"] != job["id"]]
    return changes


def run(conn: sqlite3.Connection, rows: list[dict], dry_run: bool = True) -> dict:
    """Classify rows; unless dry_run, record job mail in history, sync job statuses and write the mark-read list."""
    p = plan(rows)
    result = {"dry_run": dry_run, "counts": {k: len(v) for k, v in p.items()}, **p}
    if not dry_run:
        job_rows = [r for r in rows
                    if history.classify(r.get("subject", ""), r.get("snippet", ""), r.get("body", "")) != "alert"]
        result["history"] = history.import_rows(conn, job_rows)
        result["status_changes"] = sync_job_statuses(conn)
    to_mark = [r["thread_id"] for r in p["job_info"] + p["noise"] if r["thread_id"]]
    result["mark_read"] = to_mark
    log = LOG_DIR / f"mailcheck-{date.today().isoformat()}{'-dry' if dry_run else ''}.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"at": now(), **result}, ensure_ascii=False, indent=1))
    result["log"] = str(log)
    return result


def mark_script(thread_ids: list[str]) -> Path:
    """browser/gmail_mark_read.js with the thread ids filled in, saved under data/inbox for browser_run_code."""
    src = (ROOT / "browser" / "gmail_mark_read.js").read_text().replace("new Set(__THREADS__)", "new Set(" + json.dumps(thread_ids) + ")")
    path = LOG_DIR / "gmail-mark-read.js"
    path.write_text(src)
    return path


def gmail_base() -> str:
    """Gmail for the job inbox: the address in place of the account index opens that account even when the browser
    is signed in to several (/u/0/ is whichever signed in first)."""
    email = settings.get().job_email.strip()
    return f"https://mail.google.com/mail/u/{email or 0}/"


def gmail_url(query: str = QUERY) -> str:
    from urllib.parse import quote
    return gmail_base() + "#search/" + quote(query)


def load_rows(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        rows += history.load_rows(p)
    return rows
