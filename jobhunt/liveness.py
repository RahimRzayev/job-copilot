"""Is a job still open? Checks the job board AND the employer's own ATS before we spend effort on it.

Aggregators (Pracuj especially) keep listings alive after the employer closed the role, so the
employer-side check is the one that matters. Every check returns (state, detail) where state is
"open", "closed" or "unknown"; unknown never removes a job.
"""

import html as htmllib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from . import settings
from .db import now
from .normalize import parse_iso
from .prefilter import language_flags, required_years
from .sources.base import Http

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"

CLOSED_TEXT = re.compile(
    r"(this job has expired|job (is )?no longer available|no longer accepting applications|"
    r"position has been filled|closed vacancy|vacancy (was|has been) closed|this job is closed|"
    r"job posting (is )?(closed|expired)|oferta (wygasła|jest nieaktualna)|ogłoszenie (wygasło|jest nieaktualne)|"
    r"rekrutacja (została )?zakończona|registration closed)", re.I)

ATS_HOSTS = ("smartrecruiters.com", "myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com",
             "darwinbox.com", "teamtailor.com", "recruitee.com", "peopleforce.io", "erecruiter.pl",
             "workable.com", "personio.de", "personio.com", "bamboohr.com", "jobvite.com", "icims.com")


def is_employer_url(url: str) -> bool:
    host = urlparse(url or "").netloc.lower()
    return bool(host) and any(h in host for h in ATS_HOSTS)


# ---------- employer ATS checks ----------

def _smartrecruiters(http: Http, url: str):
    m = re.search(r"smartrecruiters\.com/([^/]+)/(\d{6,})", url)
    if not m:
        return UNKNOWN, "smartrecruiters: no posting id"
    company, pid = m.groups()
    r = http.client.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{pid}")
    if r.status_code == 404:
        return CLOSED, "smartrecruiters: posting not found"
    if r.status_code != 200:
        return UNKNOWN, f"smartrecruiters: HTTP {r.status_code}"
    return (OPEN, "smartrecruiters: active") if r.json().get("active") else (CLOSED, "smartrecruiters: expired (active=false)")


def _workday(http: Http, url: str):
    p = urlparse(url)
    tenant = p.netloc.split(".")[0]
    parts = [x for x in p.path.split("/") if x and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)]
    if "job" not in parts:
        return UNKNOWN, "workday: unexpected url"
    i = parts.index("job")
    site, rest = parts[i - 1], parts[i:]
    if rest and rest[-1] in ("apply", "applyManually"):
        rest = rest[:-1]
    api = f"{p.scheme}://{p.netloc}/wday/cxs/{tenant}/{site}/{'/'.join(rest)}"
    r = http.client.get(api, headers={"Accept": "application/json"})
    # 403 is "permission denied" (errorCode S22), not "removed": a live posting can answer 403 while a made-up job
    # id gets 404. Only 404/410 mean closed.
    if r.status_code in (404, 410):
        return CLOSED, f"workday: HTTP {r.status_code}"
    if r.status_code != 200:
        return UNKNOWN, f"workday: HTTP {r.status_code}"
    info = r.json().get("jobPostingInfo", {})
    return (OPEN, "workday: canApply") if info.get("canApply", True) else (CLOSED, "workday: canApply=false")


def _greenhouse(http: Http, url: str):
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?&]+)/jobs/(\d+)", url)
    if not m:
        return UNKNOWN, "greenhouse: no job id"
    slug, jid = m.groups()
    r = http.client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}")
    return (OPEN, "greenhouse: live") if r.status_code == 200 else (
        (CLOSED, "greenhouse: 404") if r.status_code == 404 else (UNKNOWN, f"greenhouse: HTTP {r.status_code}"))


def _lever(http: Http, url: str):
    m = re.search(r"lever\.co/([^/]+)/([0-9a-f-]{36})", url)
    if not m:
        return UNKNOWN, "lever: no posting id"
    r = http.client.get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
    return (OPEN, "lever: live") if r.status_code == 200 else (
        (CLOSED, "lever: 404") if r.status_code == 404 else (UNKNOWN, f"lever: HTTP {r.status_code}"))


def _ashby(http: Http, url: str):
    m = re.search(r"ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
    if not m:
        return UNKNOWN, "ashby: no job id"
    r = http.client.get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
    if r.status_code != 200:
        return UNKNOWN, f"ashby: HTTP {r.status_code}"
    ids = {j["id"] for j in r.json().get("jobs", [])}
    return (OPEN, "ashby: on board") if m.group(2) in ids else (CLOSED, "ashby: not on board")


def _generic_page(http: Http, url: str):
    try:
        r = http.client.get(url)
    except httpx.HTTPError as e:
        return UNKNOWN, f"page: {type(e).__name__}"
    if r.status_code in (404, 410):
        return CLOSED, f"page: HTTP {r.status_code}"
    if r.status_code != 200:
        return UNKNOWN, f"page: HTTP {r.status_code}"
    m = CLOSED_TEXT.search(r.text)
    return (CLOSED, f"page says: {m.group(0)}") if m else (UNKNOWN, "page: no closed marker")


def check_employer(http: Http, url: str):
    host = urlparse(url).netloc.lower()
    if "smartrecruiters.com" in host:
        return _smartrecruiters(http, url)
    if "myworkdayjobs.com" in host:
        return _workday(http, url)
    if "greenhouse.io" in host:
        return _greenhouse(http, url)
    if "lever.co" in host:
        return _lever(http, url)
    if "ashbyhq.com" in host:
        return _ashby(http, url)
    return _generic_page(http, url)


# ---------- employer posting text (requirements the board listing did not show) ----------
# A board copy can say "previous experience" while the employer's own posting asks 3+ years, so jobs fall out only at
# apply time. The employer's text is read once here and checked with the same rules as the scan; the result is a
# warning, never an automatic drop.

def _html_text(html: str) -> str:
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html or "", flags=re.I)
    text = re.sub(r"<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = htmllib.unescape(text).replace("\xa0", " ")  # "2–5&#43; years" must read "2–5+ years"
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def employer_text(http: Http, url: str) -> str:
    """Plain text of the employer's own posting ('' when it cannot be read)."""
    host = urlparse(url).netloc.lower()
    try:
        if "myworkdayjobs.com" in host:
            p = urlparse(url)
            parts = [x for x in p.path.split("/") if x and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)]
            if "job" not in parts:
                return ""
            i = parts.index("job")
            rest = [x for x in parts[i:] if x not in ("apply", "applyManually")]
            r = http.client.get(f"{p.scheme}://{p.netloc}/wday/cxs/{p.netloc.split('.')[0]}/{parts[i - 1]}/{'/'.join(rest)}",
                                headers={"Accept": "application/json"})
            return _html_text(r.json().get("jobPostingInfo", {}).get("jobDescription", "")) if r.status_code == 200 else ""
        if "greenhouse.io" in host:
            m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?&]+)/jobs/(\d+)", url) or \
                re.search(r"for=([^&]+)&token=(\d+)", url)
            if not m:
                return ""
            r = http.client.get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
            return _html_text(htmllib.unescape(r.json().get("content", ""))) if r.status_code == 200 else ""  # content is HTML-escaped
        if "lever.co" in host:
            m = re.search(r"lever\.co/([^/]+)/([0-9a-f-]{36})", url)
            if not m:
                return ""
            r = http.client.get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
            if r.status_code != 200:
                return ""
            d = r.json()
            lists = " ".join(f"{x.get('text', '')} {_html_text(x.get('content', ''))}" for x in d.get("lists", []))
            return f"{d.get('descriptionPlain', '')}\n{lists}"
        if "smartrecruiters.com" in host:
            m = re.search(r"smartrecruiters\.com/([^/]+)/(\d{6,})", url)
            if not m:
                return ""
            r = http.client.get(f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
            if r.status_code != 200:
                return ""
            sections = (r.json().get("jobAd") or {}).get("sections") or {}
            return "\n".join(_html_text((v or {}).get("text", "")) for v in sections.values())
        r = http.client.get(url)
        return _html_text(r.text) if r.status_code == 200 else ""
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, AttributeError):
        return ""


def employer_flags(text: str, warn_years: int | None = None) -> list[str]:
    """Blockers found in the employer's posting: years, and languages the user doesn't speak (per settings).
    Years warn from settings.max_years up (the edge of what the user takes), so they are checked before the
    shortlist."""
    if len(text) < 200:
        return []
    flags = []
    warn_years = settings.get().max_years if warn_years is None else warn_years
    years = required_years(text)
    if years is not None and years >= warn_years:
        flags.append(f"{years}+ years")
    return flags + language_flags(text)


EASY_APPLY = "Easy Apply only"
NO_APPLY_BUTTON = "apply button not shown"


# ---------- job board checks ----------

def check_board(http: Http, job: dict):
    src, sid = job["source"], job["source_id"]
    expires = parse_iso(job.get("expires_at") or "")
    if expires and expires < datetime.now(timezone.utc):
        return CLOSED, f"{src}: expired {expires.date()}", None
    if src == "justjoin":
        r = http.client.get(f"https://justjoin.it/api/candidate-api/offers/{sid}")
        if r.status_code == 404:
            return CLOSED, "justjoin: offer removed", None
        d = r.json()
        if d.get("isActive") is False:
            return CLOSED, "justjoin: inactive", None
        return OPEN, "justjoin: active", d.get("applyUrl")
    if src == "nofluffjobs":
        r = http.client.get(f"https://nofluffjobs.com/api/posting/{sid}")
        if r.status_code == 404:
            return CLOSED, "nofluffjobs: removed", None
        status = r.json().get("status")
        return (OPEN if status == "PUBLISHED" else CLOSED), f"nofluffjobs: {status}", None
    if src == "linkedin":
        r = http.client.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{sid}")
        if r.status_code == 404:
            return CLOSED, "linkedin: removed", None
        if re.search(r"no longer accepting applications", r.text, re.I):
            return CLOSED, "linkedin: no longer accepting applications", None
        # The guest page's Apply button says how to apply: "apply-link-onsite" = LinkedIn Easy Apply (never
        # automated), "apply-link-offsite" = the employer's own site. Otherwise Easy Apply jobs reach the shortlist
        # and are found out only at apply time.
        # Some responses come without the Apply button (often on the first try), and one retry is not always enough,
        # so retry twice and report a missing button instead of passing the job as plain "accepting".
        for _ in range(2):
            if "apply-link-on" in r.text or "apply-link-off" in r.text:
                break
            time.sleep(1.5)
            retry = http.client.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{sid}")
            if retry.status_code == 200:
                r = retry
        if "apply-link-onsite" in r.text and "apply-link-offsite" not in r.text:
            return OPEN, f"linkedin: accepting ({EASY_APPLY})", None
        if "apply-link-offsite" in r.text:
            return OPEN, "linkedin: accepting (employer site)", None
        return OPEN, f"linkedin: accepting ({NO_APPLY_BUTTON})", None
    if src == "bulldogjob":
        r = http.client.get(job["url"])
        m = re.search(r'"state":"(\w+)"', r.text)
        state = m.group(1) if m else ""
        return (OPEN if state in ("published", "") else CLOSED), f"bulldogjob: {state or 'unknown'}", None
    if src == "smartrecruiters":
        return (*check_employer(http, job["url"]), None)
    if src in ("greenhouse", "lever", "ashby"):
        return (*check_employer(http, job["url"]), None)
    # Every other board (Pracuj, sf_rmk, Workday...): read the posting page for a closed marker, or hundreds of jobs
    # stay 'unknown'. Cloudflare/403 answers stay 'unknown' with the reason.
    state, detail = _generic_page(http, job["url"]) if job.get("url") else (UNKNOWN, "no url")
    return state, f"{src}: {detail}", None


def check_job(http: Http, job: dict) -> tuple[str, str, str | None]:
    """(state, detail, employer_url). Board first, then the employer ATS if we know it."""
    try:
        state, detail, found_url = check_board(http, job)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
        state, detail, found_url = UNKNOWN, f"board check failed: {type(e).__name__}", None
    if state == CLOSED:
        return state, detail, job.get("employer_url")
    employer = job.get("employer_url") or (found_url if is_employer_url(found_url or "") else None) \
        or (job.get("apply_url") if is_employer_url(job.get("apply_url") or "") else None)
    if employer:
        try:
            e_state, e_detail = check_employer(http, employer)
        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
            e_state, e_detail = UNKNOWN, f"employer check failed: {type(e).__name__}"
        if e_state != UNKNOWN or state == UNKNOWN:
            state, detail = e_state, f"{detail}; {e_detail}"
    return state, detail, employer


def verify(conn: sqlite3.Connection, statuses: tuple[str, ...] = ("new", "scored", "shortlisted"),
           ids: list[int] | None = None, http: Http | None = None, min_score: int = 55) -> dict:
    http = http or Http(delay=0.3)
    if ids:
        rows = conn.execute(f"SELECT * FROM jobs WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    else:
        # Low-scored jobs are never offered, so re-checking them is wasted time: thousands of scored jobs take over
        # 10 minutes and keep the database locked. Scored jobs count from min_score up.
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(statuses))}) "
            "AND (status != 'scored' OR COALESCE(score, 0) >= ?)", (*statuses, min_score)).fetchall()
    summary = {OPEN: 0, CLOSED: 0, UNKNOWN: 0, "closed_jobs": [], "flagged": [], "easy_apply": [], "apply_unknown": []}
    for r in rows:
        job = dict(r)
        state, detail, employer = check_job(http, job)
        summary[state] += 1
        note = None
        if state != CLOSED and employer and job["status"] in ("new", "scored", "shortlisted"):
            text = employer_text(http, employer)
            if text:
                flags = employer_flags(text)
                note = "company page: " + (", ".join(flags) if flags else "no blockers found")
                if flags:
                    summary["flagged"].append({"id": job["id"], "title": job["title"], "company": job["company"],
                                               "flags": flags})
        if state == OPEN and EASY_APPLY in detail and not (employer or job.get("employer_url")):
            note = note or "LinkedIn Easy Apply only (no employer form found): apply manually"
            summary["easy_apply"].append({"id": job["id"], "title": job["title"], "company": job["company"]})
        if state == OPEN and NO_APPLY_BUTTON in detail and not (employer or job.get("employer_url")):
            note = note or "LinkedIn apply button not shown, no employer form found: check the posting before shortlisting"
            summary["apply_unknown"].append({"id": job["id"], "title": job["title"], "company": job["company"]})
        conn.execute("UPDATE jobs SET liveness = ?, liveness_detail = ?, verified_at = ?, "
                     "employer_url = COALESCE(?, employer_url), employer_note = COALESCE(?, employer_note) WHERE id = ?",
                     (state, detail, now(), employer, note, job["id"]))
        # Only open pipeline states move to "closed"; applied/skipped/manual history is never rewritten.
        if state == CLOSED and job["status"] in ("new", "scored", "shortlisted"):
            conn.execute("UPDATE jobs SET status = 'closed', filter_reason = ? WHERE id = ?", (f"closed: {detail}", job["id"]))
            summary["closed_jobs"].append({"id": job["id"], "title": job["title"], "company": job["company"], "why": detail})
    conn.commit()
    return summary
