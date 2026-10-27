"""Scrape every source, drop known jobs, prefilter, fetch details, store."""

import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import prefilter, prescore, settings
from .db import ROOT, get_meta, now, set_meta
from .normalize import Job, company_variant, role_key, title_key
from .sources import ats_boards, bulldogjob, englishjobs, justjoin, linkedin, nofluffjobs
from .sources.base import Http, pl_city, slug

COMPANIES_FILE = ROOT / "companies.yaml"
ENRICHERS = {
    "justjoin": justjoin.enrich,
    "nofluffjobs": nofluffjobs.enrich,
    "bulldogjob": bulldogjob.enrich,
    "linkedin": linkedin.enrich,
    "workday": ats_boards.enrich_workday,
    "smartrecruiters": ats_boards.enrich_smartrecruiters,
}
# Sources `scrape` can run; each runs only when settings.enabled(name). Pracuj is scraped in the browser (`ingest`).
ALL_SOURCES = ("justjoin", "nofluffjobs", "bulldogjob", "englishjobs", "linkedin", "company_boards")


@dataclass
class Report:
    fetched: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    known: int = 0
    filtered: dict[str, int] = field(default_factory=dict)
    added: int = 0
    disabled: list[str] = field(default_factory=list)  # requested sources the settings switch off

    def to_dict(self) -> dict:
        return self.__dict__


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _companies() -> list[dict]:
    if not COMPANIES_FILE.exists():
        return []
    return yaml.safe_load(COMPANIES_FILE.read_text()).get("companies") or []


def fetch_all(http: Http, sources: tuple[str, ...], hours: int, report: Report, mode: str = "full") -> list[Job]:
    jobs: list[Job] = []
    plan = {
        "justjoin": lambda: justjoin.fetch(http, mode=mode, hours=hours),
        "nofluffjobs": lambda: nofluffjobs.fetch(http, mode=mode, hours=hours),
        "bulldogjob": lambda: bulldogjob.fetch(http, mode=mode, hours=hours),
        "englishjobs": lambda: englishjobs.fetch(http, hours=hours, mode=mode),
        "linkedin": lambda: linkedin.fetch(http, hours=max(hours, 24), mode=mode),
    }
    for name in sources:
        try:
            if name == "company_boards":
                batch, errs = ats_boards.fetch(http, _companies())
                report.errors.extend(errs)
            else:
                batch = plan[name]()
        except Exception as e:  # a broken source must not stop the others
            report.errors.append(f"{name}: {e}")
            batch = []
        report.fetched[name] = len(batch)
        if name == "linkedin" and linkedin.STATS:
            report.fetched["linkedin searches"] = [dict(s) for s in linkedin.STATS]
        _log(f"{name}: {len(batch)} listings")
        jobs.extend(batch)
    return jobs


_LANGUAGES = set(settings.LANGUAGE_ALIASES.values())  # English language names, lower-case


def _bucket(reason: str) -> str:
    """Group reasons for the summary: 'location Berlin' and 'location Kraków' count together, and so do all reasons
    about one language ('German required (listing)', 'description in German', 'German title')."""
    if reason.startswith("location"):
        return "location"
    if reason.startswith("seniority"):
        return "seniority"
    if reason.endswith("years required"):
        return "too many years"
    m = re.match(r"(?:description in )?(\w+)", reason)
    if m and m.group(1).lower() in _LANGUAGES:
        return m.group(1).title()
    if reason.startswith("duplicate"):
        return "duplicate"
    return reason


def store(conn: sqlite3.Connection, job: Job, status: str, reason: str | None = None) -> bool:
    cur = conn.execute(
        """INSERT OR IGNORE INTO jobs
           (dedupe_key, source, source_id, title, company, location, remote, seniority, url, apply_url,
            description, skills, salary, posted_at, scraped_at, languages, category, filter_reason, status,
            employer_url, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (job.dedupe_key, job.source, job.source_id, job.title, job.company, job.location, int(job.remote),
         job.seniority, job.url, job.apply_url or job.url, job.description, json.dumps(job.skills),
         job.salary, job.posted_at, now(), json.dumps(job.languages), job.category, reason, status,
         # A company-board job IS the employer's posting: no resolve/WebSearch needed before /apply.
         job.employer_url or (job.url if job.source in prefilter.COMPANY_BOARDS else None), job.expires_at or None),
    )
    return cur.rowcount == 1


# The first scan of a newly added watchlist company takes every opening it has (open postings stay up for weeks);
# after that only the usual window.
NEW_BOARD_HOURS = 365 * 24


def new_companies(conn: sqlite3.Connection) -> frozenset[str]:
    """Watchlist companies with no job from their career site in the database yet: just added."""
    boards = sorted(prefilter.COMPANY_BOARDS)
    known = {r[0] for r in conn.execute(
        f"SELECT DISTINCT company FROM jobs WHERE source IN ({','.join('?' * len(boards))})", boards)}
    return frozenset(str(c.get("name")) for c in _companies()) - known


def process(conn: sqlite3.Connection, http: Http | None, jobs: list[Job], hours: int,
            max_years: int, report: Report, enrich_limit: int = 300, mode: str = "full",
            new_companies: frozenset[str] = frozenset()) -> None:
    rows = conn.execute("SELECT id, dedupe_key, source, source_id, company, title, status FROM jobs").fetchall()
    known = {r["dedupe_key"] for r in rows}
    # dedupe_key changed format over time, so also match the board's own id (boards re-list under a new key).
    known_ids = {(r["source"], r["source_id"]) for r in rows if r["source_id"]}
    # Kept roles (not filtered) by company + title: a second city/board copy of a role we already have is a duplicate.
    kept_roles = {role_key(r["company"], r["title"]): r["id"] for r in rows if r["status"] != "filtered"}
    # Same title under another spelling of the company ("Acme Sp. z o.o." vs "Acme").
    kept_titles: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        if r["status"] != "filtered":
            kept_titles.setdefault(title_key(r["title"]), []).append((r["company"], r["id"]))
    seen: set[str] = set()
    survivors: list[Job] = []

    def window(job: Job) -> int:
        new_board = job.source in prefilter.COMPANY_BOARDS and job.company in new_companies
        return NEW_BOARD_HOURS if new_board else hours

    def drop(job: Job, reason: str) -> None:
        bucket = _bucket(reason)
        report.filtered[bucket] = report.filtered.get(bucket, 0) + 1
        store(conn, job, "filtered", reason)

    for job in jobs:
        key = job.dedupe_key
        if key in known or key in seen or (job.source_id and (job.source, job.source_id) in known_ids):
            report.known += 1
            continue
        seen.add(key)
        reason = prefilter.check_listing(job, window(job), mode=mode)
        if reason:
            drop(job, reason)
        else:
            survivors.append(job)

    # Detail pages cost one request each (LinkedIn rate-limits), so fetch the most promising first.
    skills = prescore._profile_skills()
    survivors.sort(key=lambda j: prescore.prescore(j.to_dict(), skills)[0], reverse=True)
    enriched = 0
    for job in survivors:
        enrich = ENRICHERS.get(job.source)
        if http and enrich and not job.description and enriched < enrich_limit:
            enriched += 1
            try:
                enrich(http, job)
            except Exception as e:
                report.errors.append(f"enrich {job.source} {job.source_id}: {e}")
        # Re-run listing checks too: enrichment can fill in the publish date (Bulldogjob).
        reason = prefilter.check_listing(job, window(job), mode=mode) or prefilter.check_details(job, max_years)
        rk = role_key(job.company, job.title)
        if not reason and rk in kept_roles:
            reason = f"duplicate of #{kept_roles[rk]}" if kept_roles[rk] else "duplicate (same batch)"
        if not reason:
            twin = next((i for c, i in kept_titles.get(title_key(job.title), []) if company_variant(c, job.company)), None)
            if twin is not None:
                reason = f"duplicate of #{twin}" if twin else "duplicate (same batch)"
        if reason:
            drop(job, reason)
        elif store(conn, job, "new"):
            report.added += 1
            kept_roles[rk] = 0
            kept_titles.setdefault(title_key(job.title), []).append((job.company, 0))
    report.fetched["enriched"] = enriched
    conn.commit()


def scrape(conn: sqlite3.Connection, sources: tuple[str, ...] = ALL_SOURCES, hours: int = 24,
           max_years: int | None = None, mode: str = "full", enrich_limit: int = 300) -> Report:
    s = settings.require()
    started = time.monotonic()
    report = Report()
    report.disabled = [name for name in sources if not s.enabled(name)]
    sources = tuple(name for name in sources if s.enabled(name))
    http = Http()
    max_years = max_years if max_years is not None else prefilter.MAX_YEARS[mode]
    new = new_companies(conn) if "company_boards" in sources else frozenset()
    jobs = fetch_all(http, sources, hours, report, mode)
    process(conn, http, jobs, hours, max_years, report, enrich_limit=enrich_limit, mode=mode, new_companies=new)
    if new:
        report.fetched["new watchlist companies (all open jobs)"] = sorted(new)
    report.fetched["prescored"] = prescore.run(conn)["scored"]
    report.fetched["429 waits"] = http.throttled
    report.fetched["429 wait minutes"] = round(http.throttle_wait / 60, 1)
    report.fetched["minutes"] = round((time.monotonic() - started) / 60, 1)
    return report


def ingest(conn: sqlite3.Connection, path: Path, hours: int | None = None, max_years: int | None = None,
           mode: str = "full") -> Report:
    """Load jobs Claude collected elsewhere (Indeed MCP, browser-scraped Pracuj/theprotocol).

    File: JSON list of objects with Job fields (source, source_id, title, company, url required), or
    {window_hours, stats, jobs} from browser/pracuj_full.js. Without `hours` the file's own window is used, else 24
    (a 72h scrape ingested with the 24h default would drop every offer older than a day).
    """
    report = Report()
    raw = json.loads(path.read_text())
    host = None
    if isinstance(raw, dict):
        hours = hours or raw.get("window_hours")
        host = raw.get("host")
        raw = raw.get("jobs", [])
    hours = hours or SCAN_HOURS
    fields = set(Job.__dataclass_fields__)
    jobs = [Job(**{k: v for k, v in item.items() if k in fields}) for item in raw]
    report.fetched["ingest"] = len(jobs)
    max_years = max_years if max_years is not None else prefilter.MAX_YEARS[mode]
    process(conn, None, jobs, hours, max_years, report, mode=mode)
    prescore.run(conn)
    if host:
        # The browser script wrote the file right after reading Pracuj: that moment starts the next window.
        set_meta(conn, f"{PRACUJ_META}:{host}", str(path.stat().st_mtime))
    return report


SCAN_HOURS = 24
# When the last scan started (Unix time). A scan with no --hours covers everything since then, so a skipped day is
# never lost and the window never has to be picked by hand.
SCRAPE_META = "scrape_last_start"
PRACUJ_META = "pracuj_last_read"  # + ":<host>", the browser file's write time; set by ingest
OVERLAP_HOURS = 2
MAX_WINDOW_HOURS = 14 * 24


def window_hours(conn: sqlite3.Connection, key: str, now_ts: float | None = None) -> int:
    """Hours since the run recorded under `key` (+ overlap), at least one day; SCAN_HOURS when never recorded."""
    import math
    import time
    last = get_meta(conn, key)
    if not last:
        return SCAN_HOURS
    since = ((now_ts or time.time()) - float(last)) / 3600
    return max(SCAN_HOURS, min(math.ceil(since) + OVERLAP_HOURS, MAX_WINDOW_HOURS))


def pracuj_window_hours(conn: sqlite3.Connection) -> int:
    """The larger window of the two Pracuj scripts (it.pracuj.pl and the www.pracuj.pl keyword searches)."""
    return max(window_hours(conn, f"{PRACUJ_META}:{host}") for host in ("it.pracuj.pl", "www.pracuj.pl"))


def pracuj_city() -> str:
    """The configured city as Pracuj's URL slug ("warszawa"): browser/pracuj_full.js's __CITY__ (as a JSON string)."""
    return slug(pl_city())


# Polish search words per level for the www.pracuj.pl keyword searches: student roles are often filed outside the
# IT section.
PRACUJ_LEVEL_WORDS = {"student": ("praca dla studenta",), "working student": ("praca dla studenta",),
                      "internship": ("staż", "praktyki"), "intern": ("staż", "praktyki"), "trainee": ("staż",)}


def pracuj_keywords() -> list[str]:
    """Keyword searches for www.pracuj.pl (browser/pracuj_full.js's __KEYWORDS__): the user's roles (tech areas when
    `tech_only` and none are set), their search keywords, then Polish words for their levels."""
    from .sources import linkedin
    s = settings.get()
    roles = s.roles or ((*linkedin.TECH_SLICES, *linkedin.LOCAL_SLICES["PL"]) if s.tech_only else ())
    local = [w for lv in s.levels for w in PRACUJ_LEVEL_WORDS.get(lv, ())]
    words = (*roles, *(s.keywords or linkedin.level_keywords()), *local)
    return list(dict.fromkeys(w.lower() for w in words))


def refilter(conn: sqlite3.Connection, hours: int = 24, max_years: int | None = None, mode: str = "full",
             all_roles: bool = False) -> dict:
    """Re-run current prefilter rules on filtered jobs (rules or the age window changed).

    A longer `hours` window only applies to internship/graduate programmes (they stay open for weeks); ordinary
    jobs keep the daily scan window, so `--hours 168` does not revive a week of LinkedIn noise (about a thousand
    jobs nobody reads). `all_roles=True` applies the window to every job.
    """
    # Reasons starting with "manual:" are human decisions; never undo them automatically. Duplicates are not a rule
    # result either: re-checking them revives copies of jobs already scored/applied.
    rows = conn.execute("SELECT * FROM jobs WHERE status = 'filtered' "
                        "AND COALESCE(filter_reason, '') NOT LIKE 'manual:%' "
                        "AND COALESCE(filter_reason, '') NOT LIKE 'duplicate of%'").fetchall()
    revived = []
    for r in rows:
        job = Job(source=r["source"], source_id=r["source_id"] or "", title=r["title"], company=r["company"],
                  url=r["url"], location=r["location"] or "", remote=bool(r["remote"]),
                  seniority=r["seniority"] or "", apply_url=r["apply_url"] or "", description=r["description"] or "",
                  posted_at=r["posted_at"] or "", languages=json.loads(r["languages"] or "[]"))
        window = hours if all_roles or prefilter.is_programme_job(job) else min(hours, SCAN_HOURS)
        reason = prefilter.check_listing(job, window, mode=mode) or (
            prefilter.check_details(job, max_years if max_years is not None else prefilter.MAX_YEARS[mode])
            if job.description else None)
        if reason is None:
            conn.execute("UPDATE jobs SET status = 'new', filter_reason = NULL WHERE id = ?", (r["id"],))
            revived.append(r["id"])
        elif reason != r["filter_reason"]:
            conn.execute("UPDATE jobs SET filter_reason = ? WHERE id = ?", (reason, r["id"]))
    conn.commit()
    # A revived job can duplicate a job stored later under another board/city: collapse those right away.
    merged = [m["id"] for m in dedupe_existing(conn)["merged"]] if revived else []
    return {"checked": len(rows), "revived": [i for i in revived if i not in merged], "merged_duplicates": merged}


def update_details(conn: sqlite3.Connection, path: Path) -> dict:
    """Merge extra fields scraped later (e.g. Pracuj employer formUrl) by (source, source_id)."""
    allowed = {"employer_url", "expires_at", "apply_url", "description"}
    n = 0
    for item in json.loads(path.read_text()):
        fields = {k: v for k, v in item.items() if k in allowed and v}
        if not fields:
            continue
        sets = ", ".join(f"{k} = ?" for k in fields)
        n += conn.execute(f"UPDATE jobs SET {sets} WHERE source = ? AND source_id = ?",
                          [*fields.values(), item["source"], str(item["source_id"])]).rowcount
    conn.commit()
    return {"updated": n}


def enrich_missing(conn: sqlite3.Connection, limit: int = 300, max_years: int | None = None, mode: str = "full") -> dict:
    """Fetch descriptions for status=new jobs that have none yet (highest pre-score first), then re-run the
    detail rules (language, years) on them."""
    max_years = max_years if max_years is not None else prefilter.MAX_YEARS[mode]
    http = Http()
    rows = conn.execute("SELECT * FROM jobs WHERE status = 'new' AND (description IS NULL OR description = '') "
                        "ORDER BY prescore DESC NULLS LAST LIMIT ?", (limit,)).fetchall()
    out = {"tried": len(rows), "enriched": 0, "dropped": {}, "errors": 0}
    for r in rows:
        enrich = ENRICHERS.get(r["source"])
        if not enrich:
            continue
        job = Job(source=r["source"], source_id=r["source_id"] or "", title=r["title"], company=r["company"],
                  url=r["url"], location=r["location"] or "", remote=bool(r["remote"]), seniority=r["seniority"] or "",
                  apply_url=r["apply_url"] or "", posted_at=r["posted_at"] or "", employer_url=r["employer_url"] or "",
                  languages=json.loads(r["languages"] or "[]"), skills=json.loads(r["skills"] or "[]"))
        try:
            enrich(http, job)
        except Exception:
            out["errors"] += 1
            continue
        out["enriched"] += 1
        reason = prefilter.check_listing(job, 24 * 14, mode=mode) if job.source == "workday" else None
        reason = reason or prefilter.check_details(job, max_years)
        conn.execute("UPDATE jobs SET description = ?, languages = ?, skills = ?, seniority = ?, location = ?, "
                     "employer_url = COALESCE(NULLIF(?, ''), employer_url), apply_url = COALESCE(NULLIF(?, ''), apply_url), "
                     "status = ?, filter_reason = ? WHERE id = ?",
                     (job.description, json.dumps(job.languages), json.dumps(job.skills), job.seniority, job.location,
                      job.employer_url, job.apply_url, "filtered" if reason else "new", reason, r["id"]))
        if reason:
            b = _bucket(reason)
            out["dropped"][b] = out["dropped"].get(b, 0) + 1
    conn.commit()
    prescore.run(conn)
    return out


STATUS_RANK = {"applied": 0, "interview": 0, "offer": 0, "rejected": 0, "manual": 1, "skipped": 2, "shortlisted": 3,
               "applying": 3, "ready": 3, "needs_input": 3, "scored": 4, "new": 5, "closed": 6}


def dedupe_existing(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Collapse duplicates already stored: same board id, or same company + title. Keeps the most advanced row
    (applied > shortlisted > scored > new, then the oldest) and marks the others filtered 'duplicate of #id'."""
    rows = conn.execute("SELECT id, source, source_id, company, title, status FROM jobs WHERE status != 'filtered'").fetchall()
    groups: dict[str, list] = {}
    parent: dict[int, int] = {}

    def find(i: int) -> int:
        while parent.get(i, i) != i:
            i = parent[i]
        return i

    for r in rows:
        parent[r["id"]] = r["id"]
        for k in (f"id|{r['source']}|{r['source_id']}" if r["source_id"] else None, "role|" + role_key(r["company"], r["title"])):
            if k:
                groups.setdefault(k, []).append(r["id"])
    for ids in groups.values():
        for other in ids[1:]:
            a, b = find(ids[0]), find(other)
            if a != b:
                parent[b] = a
    by_title: dict[str, list] = {}
    for r in rows:
        by_title.setdefault(title_key(r["title"]), []).append(r)
    for same in by_title.values():
        for i, x in enumerate(same):
            for y in same[i + 1:]:
                if company_variant(x["company"], y["company"]):
                    a, b = find(x["id"]), find(y["id"])
                    if a != b:
                        parent[b] = a
    by_root: dict[int, list] = {}
    status = {r["id"]: r["status"] for r in rows}
    for r in rows:
        by_root.setdefault(find(r["id"]), []).append(r["id"])
    merged = []
    for ids in by_root.values():
        if len(ids) < 2:
            continue
        keep = min(ids, key=lambda i: (STATUS_RANK.get(status[i], 9), i))
        for i in ids:
            if i == keep or STATUS_RANK.get(status[i], 9) <= 1:  # never hide an applied/manual record
                continue
            merged.append({"id": i, "status": status[i], "duplicate_of": keep, "kept_status": status[keep]})
            if not dry_run:
                conn.execute("UPDATE jobs SET status = 'filtered', filter_reason = ? WHERE id = ?",
                             (f"duplicate of #{keep}", i))
    conn.commit()
    return {"groups": sum(1 for v in by_root.values() if len(v) > 1), "merged": merged}
