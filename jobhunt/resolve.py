"""Find the employer's own application page for jobs found on aggregators (LinkedIn, JustJoin, Bulldogjob...).

Hunting each aggregator job's real form by hand eats most of the /apply time. This module tries the cheap,
deterministic routes first and stores the hit in jobs.employer_url:

1. the company is on the companies.yaml watchlist -> search that board for the title;
2. probe public ATS APIs with slugs guessed from the company name (Greenhouse, Lever, Ashby, SmartRecruiters,
   Workable, Recruitee).

Anything still unresolved is left for Claude (WebSearch "<title>" "<company>" + ATS names, aggregator pages such as
dreamworkhq "View original posting"), who saves the result with `jobhunt set-employer <id> <url>`.
"""

import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import settings
from .db import now
from .normalize import _COMPANY_SUFFIXES, _fold
from .pipeline import _companies
from .sources.ats_boards import BOARDS
from .sources.base import Http

TITLE_NOISE = re.compile(r"\b(m|k|f|x|d|n|w|all genders|she|he|they)\b")


def _title_tokens(title: str) -> set[str]:
    return set(TITLE_NOISE.sub(" ", _fold(title)).split())


def title_match(a: str, b: str) -> float:
    """Jaccard similarity of folded title words (0..1)."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def slugs(company: str) -> list[str]:
    """'Northwind Travel Group' -> northwindtravelgroup, northwind-travel-group, northwindtravel, northwind..."""
    out = []
    for words in (_COMPANY_SUFFIXES.sub(" ", _fold(company)).split(), _fold(company).split()):
        out += ["".join(words), "-".join(words)]
    words = _COMPANY_SUFFIXES.sub(" ", _fold(company)).split() or _fold(company).split()
    out += ["".join(words[:2]), words[0] if words else ""]
    return [s for i, s in enumerate(out) if s and s not in out[:i]]


def _best(title: str, postings: list[tuple[str, str, str]], threshold: float = 0.6) -> tuple[str, float] | None:
    """postings: (title, location, url). Prefer postings in the configured country/city or remote on ties."""
    home = settings.get()
    scored = []
    for t, loc, url in postings:
        s = title_match(title, t)
        if s >= threshold:
            near = home.in_country(loc) or home.in_city(loc) or "remote" in (loc or "").lower()
            scored.append((s + (0.05 if near else 0), url))
    return max(scored)[::-1] if scored else None


def _probe(http: Http, company: str, title: str) -> tuple[str, str] | None:
    for slug in slugs(company):
        probes = {
            "greenhouse": lambda: [(j["title"], j.get("location", {}).get("name", ""), j["absolute_url"])
                                   for j in http.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs").json()["jobs"]],
            "lever": lambda: [(j["text"], j.get("categories", {}).get("location", ""), j["hostedUrl"])
                              for j in http.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}).json()],
            "ashby": lambda: [(j["title"], j.get("location", ""), j["jobUrl"])
                              for j in http.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json()["jobs"]],
            # The widget feed, not the api/v3 search: v3 can block an IP for hours with 429 while the widget feed
            # keeps answering.
            "workable": lambda: [(j["title"], j.get("country", ""), f"https://apply.workable.com/{slug}/j/{j['shortcode']}/")
                                 for j in http.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}").json()
                                 .get("jobs", [])],
            "recruitee": lambda: [(j["title"], j.get("location", ""), j["careers_url"])
                                  for j in http.get(f"https://{slug}.recruitee.com/api/offers/").json()["offers"]],
            "smartrecruiters": lambda: [(j["name"], (j.get("location") or {}).get("city", ""),
                                         f"https://jobs.smartrecruiters.com/{slug}/{j['id']}")
                                        for j in http.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                                                          params={"q": title, "limit": 100}).json().get("content", [])],
        }
        for ats, fn in probes.items():
            try:
                hit = _best(title, fn())
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                continue
            if hit:
                return hit[0], ats
    return None


def _watchlist(http: Http, company: str, title: str) -> tuple[str, str] | None:
    want = _COMPANY_SUFFIXES.sub(" ", _fold(company)).strip()
    for c in _companies():
        name = _COMPANY_SUFFIXES.sub(" ", _fold(c["name"])).strip()
        if not name or not (name == want or want.startswith(name + " ") or name.startswith(want + " ")):
            continue
        handler = BOARDS.get(c.get("ats", ""))
        try:
            if c.get("ats") == "workday":
                # Search the title itself: the board's country/city lists can miss a posting by its location text.
                jobs = handler(http, c["name"], c.get("slug") or "", searches=(title,), max_offset=40)
            else:
                jobs = handler(http, c["name"], c.get("slug") or "") if handler else []
        except Exception:
            continue
        hit = _best(title, [(j.title, j.location, j.employer_url or j.url) for j in jobs])
        if hit:
            return hit[0], c["ats"]
    return None


# An unresolved job is searched again only after this many days: otherwise the same unresolved jobs are searched on
# every scan, which takes over 10 minutes.
RETRY_DAYS = 7


def resolve(conn: sqlite3.Connection, ids: list[int] | None = None, min_score: int = 55) -> dict:
    """Resolve employer_url for the given jobs, or for shortlisted / scored >= min_score jobs that lack one and were
    not tried in the last RETRY_DAYS days."""
    if ids:
        rows = conn.execute(f"SELECT * FROM jobs WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETRY_DAYS)).isoformat(timespec="seconds")
        rows = conn.execute("SELECT * FROM jobs WHERE (employer_url IS NULL OR employer_url = '') AND "
                            "(status = 'shortlisted' OR (status = 'scored' AND score >= ?)) AND "
                            "(resolve_tried_at IS NULL OR resolve_tried_at < ?)", (min_score, cutoff)).fetchall()
    http = Http(delay=0.3, timeout=12)
    out = {"tried": len(rows), "resolved": [], "unresolved": []}
    for n, r in enumerate(rows, 1):
        if r["employer_url"] and not ids:
            continue
        start, refused = time.monotonic(), http.refused
        hit = _watchlist(http, r["company"], r["title"]) or _probe(http, r["company"], r["title"])
        # A probe refused by a blocked host proves nothing: leave the job unstamped so the next run tries it again
        # instead of waiting RETRY_DAYS.
        missed = not hit and http.refused > refused
        if not missed:
            conn.execute("UPDATE jobs SET resolve_tried_at = ? WHERE id = ?", (now(), r["id"]))
        if hit:
            url, ats = hit
            conn.execute("UPDATE jobs SET employer_url = ? WHERE id = ?", (url, r["id"]))
            out["resolved"].append({"id": r["id"], "company": r["company"], "ats": ats, "url": url})
        else:
            out["unresolved"].append({"id": r["id"], "company": r["company"], "title": r["title"],
                                      **({"retry_next_run": True} if missed else {})})
        # One stderr line per job: a slow run otherwise prints nothing for hours.
        print(f"resolve {n}/{len(rows)} #{r['id']} {r['company'][:40]}: {hit[1] if hit else 'no link'}"
              f" ({time.monotonic() - start:.0f} s)", file=sys.stderr, flush=True)
    conn.commit()
    if http.blocked:
        out["blocked_hosts"] = {host: f"429, Retry-After {secs} s" for host, secs in http.blocked.items()}
    return out


def set_employer(conn: sqlite3.Connection, job_id: int, url: str) -> dict:
    conn.execute("UPDATE jobs SET employer_url = ? WHERE id = ?", (url, job_id))
    conn.commit()
    return {"id": job_id, "employer_url": url}
