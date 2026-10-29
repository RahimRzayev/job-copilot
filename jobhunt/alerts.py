"""LinkedIn job-alert emails (Gmail) as a job source.

The user keeps LinkedIn job alerts (keywords + a location, e.g. "intern" in their city or remote in their country)
and LinkedIn emails the matches daily. `browser/linkedin_alerts.js` reads those emails in Gmail; this module turns the
cards into jobs, fetches each description from LinkedIn's public guest page, and runs the normal prefilter + pre-score.
No LinkedIn login is involved.
"""

import json
import math
import re
import time
from datetime import datetime, timezone

import httpx
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

from . import pipeline, prefilter, prescore, settings
from .db import get_meta, set_meta
from .normalize import Job
from .sources import linkedin
from .sources.base import Http

GMAIL_QUERY = "from:jobalerts-noreply@linkedin.com newer_than:{days}d"
# Default: every alert email since the last import, so a run late in the day (or after a weekend) misses nothing.
# Alerts that arrive after a run are picked up by the next one even if /mail-check marked them read (a fixed
# newer_than:1d window can drop alerts that came in after the morning run).
GMAIL_QUERY_SINCE = "from:jobalerts-noreply@linkedin.com after:{since}"
LAST_READ_KEY = "alerts_last_read"
# Re-read the last hour before the previous read: an email can land while the browser script runs.
OVERLAP_SECONDS = 3600
# A job can be posted a day or two before the alert that mentions it.
EXTRA_HOURS = 48


def gmail_url(search: str = "") -> str:
    """Gmail web URL in the job-search account (settings.job_email; Gmail accepts the address in place of the account
    index, so the right account opens whatever the browser's account order), else the first account (/u/0/)."""
    email = settings.get().job_email
    url = f"https://mail.google.com/mail/u/{quote(email, safe='@') if email else 0}/"
    return url + ("#search/" + quote(search) if search else "")


def query(conn: sqlite3.Connection, days: int | None = None, now: float | None = None) -> dict:
    """Gmail search for the alert emails to read. Explicit days → newer_than:Nd; otherwise everything since the
    last import (minus OVERLAP_SECONDS); no import yet → the last day. `days` is what `import` should use."""
    now = time.time() if now is None else now
    last = get_meta(conn, LAST_READ_KEY)
    if days is None and last:
        since = int(float(last)) - OVERLAP_SECONDS
        q = GMAIL_QUERY_SINCE.format(since=since)
        days = window_days(float(last), now)
        out = {"since": datetime.fromtimestamp(since, timezone.utc).isoformat(timespec="seconds")}
    else:
        days = days or 1
        q = GMAIL_QUERY.format(days=days)
        out = {}
    return {"query": q, "url": gmail_url(q), "days": days, **out}


def window_days(last_read: float, now: float) -> int:
    """Whole days covering last_read..now (plus the overlap), at least 1."""
    return max(1, math.ceil((now - last_read + OVERLAP_SECONDS) / 86400))


def split_place(place: str) -> tuple[str, bool]:
    """'Berlin (Hybrid)' -> ('Berlin', False); 'Germany (Remote)' -> ('Germany', True);
    'Berlin, Berlin, Germany (On-site)' -> ('Berlin', False)."""
    mode = re.search(r"\(([^)]*)\)\s*$", place or "")
    city = re.sub(r"\s*\([^)]*\)\s*$", "", place or "").split(",")[0].strip()
    return city, bool(mode and "remote" in mode.group(1).lower())


def to_job(item: dict) -> Job:
    city, remote = split_place(item.get("place", ""))
    return Job(
        source="linkedin", source_id=str(item["source_id"]), title=item.get("title", ""),
        company=item.get("company", ""), url=item.get("url") or f"https://www.linkedin.com/jobs/view/{item['source_id']}",
        location=city, remote=remote, salary=item.get("salary", ""), posted_at=item.get("posted_at", ""),
        category=f"alert:{item['alert']}" if item.get("alert") else "alert",
    )


def load(path: Path) -> tuple[dict, list[Job], list[dict]]:
    raw = json.loads(Path(path).read_text())
    stats = raw.get("stats", {}) if isinstance(raw, dict) else {}
    items = raw.get("jobs", []) if isinstance(raw, dict) else raw
    searches = raw.get("searches", []) if isinstance(raw, dict) else []
    return stats, [to_job(i) for i in items if i.get("source_id")], searches


# Query params of the email's "See all jobs" link that define the alert itself; tracking params are dropped and the
# absolute "since" window (f_TPR=a<timestamp>-) is replaced by a relative one the guest API understands.
SEARCH_PARAMS = {"keywords", "geoId", "location", "distance", "f_WT", "f_E", "f_JT", "f_C", "f_I", "f_F", "f_SB2",
                 "f_AL", "f_EA"}


WORK_WORDS = re.compile(r"\s+(remote|hybrid|on-?site)\s*$", re.I)
LEVEL_WORDS = re.compile(r"\s+((internship|entry level|associate)(\s+or\s+(internship|entry level|associate))*)\s*$", re.I)
WORK_TYPE = {"onsite": "1", "remote": "2", "hybrid": "3"}
EXP_LEVEL = {"internship": "1", "entry level": "2", "associate": "3"}


def search_params(url: str) -> dict:
    params = {k: v.strip() for k, v in parse_qsl(urlsplit(url).query) if k in SEARCH_PARAMS}
    # Some alert links carry their filters as plain words at the end of the query ("... Internship or Entry level
    # Remote"). LinkedIn then searches those words, not the filters: a Europe-wide alert returned ~900 on-site jobs
    # in other countries and made the import take 10+ minutes. Turn them back into filters.
    kw = params.get("keywords", "")
    m = WORK_WORDS.search(kw)
    if m and "f_WT" not in params:
        params["f_WT"] = WORK_TYPE[m.group(1).lower().replace("-", "")]
        kw = kw[:m.start()].rstrip()
    m = LEVEL_WORDS.search(kw)
    if m and "f_E" not in params:
        params["f_E"] = ",".join(sorted({EXP_LEVEL[w.lower()] for w in re.findall(r"internship|entry level|associate", m.group(0), re.I)}))
        kw = kw[:m.start()].rstrip()
    if kw:
        params["keywords"] = kw
    # One alert arrives with distance 49.7 and 50 on different days: round so it runs once.
    if params.get("distance"):
        try:
            params["distance"] = str(round(float(params["distance"])))
        except ValueError:
            pass
    return params


# LinkedIn's public search stops around 1000 results. A limit of 100 cut off most alert searches (broad ones such as
# "software engineer OR developer" have 300+ jobs in 24 h).
MAX_SEARCH_RESULTS = 1000


# LinkedIn geo ids wider than one country.
WIDE_GEO = {"100506914": "Europe", "91000000": "European Union", "91000002": "EMEA", "92000000": "Worldwide"}


def wide_area(params: dict) -> str | None:
    """The region/world an alert searches ('Europe', 'Worldwide'...), or None for a city/country alert. Known geo ids,
    or a location text naming one of settings.region_names or the whole world."""
    if params.get("geoId") in WIDE_GEO:
        return WIDE_GEO[params["geoId"]]
    place = (params.get("location") or "").strip().lower()
    if place and (place in settings.get().region_names or place in ("worldwide", "anywhere", "world")):
        return params["location"].strip()
    return None


def expand_searches(http: Http, searches: list[dict], days: int,
                    max_results: int = MAX_SEARCH_RESULTS) -> tuple[list[Job], list[str]]:
    """Run each alert's full search ("See all jobs") on LinkedIn's public guest endpoint — the email itself lists only
    the top ~6 matches."""
    jobs: dict[str, Job] = {}
    errors = []
    seen = set()
    for s in searches:
        params = search_params(s.get("url", ""))
        key = tuple(sorted(params.items()))
        if not params.get("keywords") and not params.get("geoId") or key in seen:
            continue
        seen.add(key)
        area = wide_area(params)
        if area:
            # The guest endpoint ignores the remote filter, so a region-wide "Remote" alert's see-all list is mostly
            # on-site jobs in other countries plus remote jobs pinned to one: over a thousand jobs in a few days, all
            # dropped by the remote rules whatever settings.remote allows, and most of the import time. The
            # email's own cards still count and go through those rules (prefilter, settings.remote/region_names).
            errors.append(f"see-all skipped ({area}): {params.get('keywords', '')[:60]}")
            continue
        for start in range(0, max_results, 10):
            try:
                batch = linkedin._parse_cards(linkedin.search_page(http, {
                    **params, "f_TPR": f"r{days * 86400}", "sortBy": "DD", "start": start}))
            except Exception as e:
                errors.append(f"search {params.get('keywords')}: {e}")
                break
            for j in batch:
                if params.get("f_WT") == "2":
                    j.remote = True
                j.category = f"alert:{s.get('alert') or params.get('keywords', '')}"
                jobs.setdefault(j.source_id, j)
            if len(batch) < 10:
                break
    return list(jobs.values()), errors


def _revive(conn: sqlite3.Connection, http: Http, jobs: list[Job], hours: int, max_years: int) -> list[int]:
    """Jobs the daily scan already dropped only for age ('older than 24h') come back when an alert mentions them
    again within the alert window; they are re-checked with the full rules after fetching the description."""
    revived = []
    for job in jobs:
        row = conn.execute("SELECT * FROM jobs WHERE source = 'linkedin' AND source_id = ? AND status = 'filtered' "
                           "AND filter_reason LIKE 'older than%'", (job.source_id,)).fetchone()
        if not row:
            continue
        job.description = row["description"] or ""
        if not job.description:
            try:
                linkedin.enrich(http, job)
            except Exception:
                continue
        reason = prefilter.check_listing(job, hours, mode="full") or prefilter.check_details(job, max_years)
        conn.execute("UPDATE jobs SET status = ?, filter_reason = ?, description = ?, posted_at = ?, category = ?, "
                     "seniority = COALESCE(NULLIF(?, ''), seniority), "
                     "employer_url = COALESCE(NULLIF(?, ''), employer_url), apply_url = COALESCE(NULLIF(?, ''), apply_url) "
                     "WHERE id = ?",
                     ("filtered" if reason else "new", reason, job.description, job.posted_at, job.category,
                      job.seniority, job.employer_url, job.apply_url, row["id"]))
        if not reason:
            revived.append(row["id"])
    conn.commit()
    return revived


def run(conn: sqlite3.Connection, path: Path, days: int | None = None, max_years: int | None = None,
        enrich_limit: int = 200) -> dict:
    stats, jobs, searches = load(path)
    # The browser script wrote the file right after reading Gmail: that moment becomes the next run's start.
    read_at = path.stat().st_mtime
    if days is None:
        last = get_meta(conn, LAST_READ_KEY)
        days = window_days(float(last), read_at) if last else 1
    hours = days * 24 + EXTRA_HOURS
    max_years = max_years if max_years is not None else prefilter.MAX_YEARS["full"]
    http = Http()
    started = time.monotonic()
    in_email = {j.source_id for j in jobs}
    more, search_errors = expand_searches(http, searches, days)
    more = [j for j in more if j.source_id not in in_email]
    jobs += more
    revived = _revive(conn, http, jobs, hours, max_years)
    report = pipeline.Report()
    report.fetched["alert cards"] = len(in_email)
    report.fetched["see-all searches"] = len({tuple(sorted(search_params(s.get("url", "")).items())) for s in searches})
    report.fetched["see-all extra jobs"] = len(more)
    report.errors.extend(search_errors)
    # Fetch descriptions here (not in process) so a removed posting (guest page 404) is stored as closed
    # instead of lingering as a "new" job with no description.
    known = {r[0] for r in conn.execute("SELECT dedupe_key FROM jobs")}
    closed = set()
    # Only jobs that pass the cheap listing rules are worth a detail request (see-all lists include senior roles).
    todo = [j for j in jobs if j.dedupe_key not in known and not prefilter.check_listing(j, hours, mode="full")]
    for job in todo[:enrich_limit]:
        try:
            linkedin.enrich(http, job)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                pipeline.store(conn, job, "closed", "linkedin: posting removed (404)")
                closed.add(job.source_id)
        except Exception as e:
            report.errors.append(f"enrich linkedin {job.source_id}: {e}")
    report.fetched["closed"] = len(closed)
    jobs = [j for j in jobs if j.source_id not in closed] + [j for j in jobs if j.source_id in closed]
    pipeline.process(conn, http, jobs, hours, max_years, report, enrich_limit=enrich_limit, mode="full")
    prescore.run(conn)
    report.fetched["429 waits"] = http.throttled
    report.fetched["429 wait minutes"] = round(http.throttle_wait / 60, 1)
    if http.blocked:  # hosts that sent a Retry-After of hours: their requests failed at once instead of waiting
        report.fetched["blocked hosts"] = {host: f"Retry-After {secs} s" for host, secs in http.blocked.items()}
    report.fetched["minutes"] = round((time.monotonic() - started) / 60, 1)

    # Where every alert job ended up (new today, revived, already seen by the daily scan, or filtered and why).
    alert_of = {j.source_id: j.category.removeprefix("alert:") for j in jobs}
    keys = [j.dedupe_key for j in jobs]
    rows = conn.execute(
        f"SELECT id, source, source_id, title, company, location, remote, status, filter_reason, prescore, score, tier "
        f"FROM jobs WHERE dedupe_key IN ({','.join('?' * len(keys))}) OR (source = 'linkedin' AND source_id IN "
        f"({','.join('?' * len(jobs))}))", [*keys, *[j.source_id for j in jobs]]).fetchall() if jobs else []
    by_status: dict[str, list[dict]] = {}
    filtered_reasons: dict[str, int] = {}
    for r in rows:
        d = dict(r)
        sid = d.pop("source_id")
        d["alert"] = alert_of.get(sid, "")
        if d["status"] == "filtered":
            # Hundreds of "See all" results end here (a 60 KB report): count them by reason and list only the jobs
            # LinkedIn put in the email itself.
            reason = re.sub(r"\s*\(.*$|:.*$| #\d+$", "", d["filter_reason"] or "?")
            filtered_reasons[reason] = filtered_reasons.get(reason, 0) + 1
            if sid not in in_email:
                continue
        by_status.setdefault(d["status"], []).append(d)
    for lst in by_status.values():
        lst.sort(key=lambda d: -(d["score"] or d["prescore"] or 0))
    # Only after a successful import, and never backwards (re-importing an old file must not reopen old days).
    last = get_meta(conn, LAST_READ_KEY)
    if not last or read_at > float(last):
        set_meta(conn, LAST_READ_KEY, str(int(read_at)))
    counts = {k: len(v) for k, v in by_status.items()}
    counts["filtered"] = sum(filtered_reasons.values())
    return {"emails": stats, "days": days, "pipeline": report.to_dict(), "revived": revived, "counts": counts,
            "filtered_reasons": dict(sorted(filtered_reasons.items(), key=lambda kv: -kv[1])), "jobs": by_status}
