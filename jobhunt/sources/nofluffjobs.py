"""nofluffjobs.com — public search + posting API (JSON). Polish board: the configured city plus remote jobs."""

import time

from ..normalize import Job, html_to_text, ms_to_iso
from .base import Http, pl_city, slug

SEARCH = "https://nofluffjobs.com/api/search/posting"
POSTING = "https://nofluffjobs.com/api/posting/"
PARAMS = {"salaryCurrency": "PLN", "salaryPeriod": "month", "region": "pl", "language": "en-GB"}


def _to_job(p: dict) -> Job:
    places = p.get("location", {}).get("places", [])
    city = next((pl.get("city") for pl in places if pl.get("city")), "")
    sal = p.get("salary") or {}
    salary = (f"{sal['from']:.0f}-{(sal.get('to') or sal['from']):.0f} {sal.get('currency')}/{sal.get('period', '')} "
              f"({sal.get('type')})") if sal.get("from") else ""
    return Job(
        source="nofluffjobs",
        source_id=p["url"],
        title=p["title"],
        company=p["name"],
        url=f"https://nofluffjobs.com/pl/job/{p['url']}",
        location=city or ("Remote" if p.get("fullyRemote") else ""),
        remote=bool(p.get("fullyRemote")),
        seniority=(p.get("seniority") or [""])[0].lower(),
        skills=[t["value"] for t in p.get("tiles", {}).get("values", []) if t.get("type") == "requirement"],
        salary=salary,
        posted_at=ms_to_iso(p.get("renewed") or p.get("posted")),
        category=p.get("category", ""),
    )


def fetch(http: Http, city: str | None = None, max_pages: int = 5, mode: str = "full", hours: int = 24) -> list[Job]:
    """full: every seniority, the city + remote, all pages (the API has no date sort), keep only postings
    posted/renewed within `hours`. narrow: trainee + junior only. `city` is the board's slug ("warszawa")."""
    city = city or slug(pl_city())
    jobs: dict[str, Job] = {}
    if mode == "narrow":
        for crit in ({"city": [city]}, {"remote": True}):
            for page in range(1, max_pages + 1):
                body = {"criteriaSearch": {**crit, "seniority": ["trainee", "junior"]}}
                # `page` only works as a URL parameter; in the JSON body it is silently ignored.
                data = http.post(SEARCH, params={**PARAMS, "page": page}, json=body).json()
                for p in data.get("postings", []):
                    jobs.setdefault(p["url"], _to_job(p))
                if page >= data.get("totalPages", 1):
                    break
        return list(jobs.values())

    cutoff_ms = (time.time() - hours * 3600) * 1000
    page, total = 1, 1
    while page <= min(total, 150):
        # `page` only works as a URL parameter; in the JSON body it is silently ignored.
        data = http.post(SEARCH, params={**PARAMS, "page": page}, json={"criteriaSearch": {"city": [city, "remote"]}}).json()
        total = data.get("totalPages", 1)
        for p in data.get("postings", []):
            # "renewed" is bumped every few hours on most postings, so only "posted" means new.
            if (p.get("posted") or 0) >= cutoff_ms:
                try:
                    jobs.setdefault(p["url"], _to_job(p))
                except (KeyError, TypeError, ValueError):
                    continue  # one malformed posting must not kill the whole source
        page += 1
    return list(jobs.values())


def enrich(http: Http, job: Job) -> Job:
    d = http.get(POSTING + job.source_id, params={"language": "en-GB"}).json()
    req = d.get("requirements") or {}
    parts = [
        html_to_text((d.get("details") or {}).get("description", "")),
        html_to_text(req.get("description", "")),
    ]
    job.description = "\n".join(p for p in parts if p)
    job.skills = [m["value"] for m in req.get("musts", [])] or job.skills
    job.languages = [f"{l.get('code') or l.get('name')}:{l.get('level', '')}"
                     for l in req.get("languages", []) if l.get("type") == "MUST"]
    job.apply_url = job.url  # NoFluff applications start on its own posting page
    job.expires_at = d.get("expiresAt") or ""
    return job
