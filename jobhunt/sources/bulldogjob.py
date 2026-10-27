"""bulldogjob.pl — server-rendered Next.js page; listing data is in __NEXT_DATA__.

The listing has no publish date, so freshness comes from first-seen time in our DB.
"""

import json
import re

from ..normalize import Job, html_to_text
from .base import Http, pl_city

LIST_URL = "https://bulldogjob.pl/companies/jobs/s/city,{city}/experienceLevel,{level}"
_NEXT = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def _next_data(html: str) -> dict:
    m = _NEXT.search(html)
    return json.loads(m.group(1)) if m else {}


def _to_job(j: dict) -> Job:
    sal = j.get("denominatedSalaryLong") or {}
    return Job(
        source="bulldogjob",
        source_id=j["id"],
        title=j["position"],
        company=(j.get("company") or {}).get("name", ""),
        url=f"https://bulldogjob.pl/companies/jobs/{j['id']}",
        apply_url=j.get("redirectTo") or "",
        location=j.get("city", ""),
        remote=bool(j.get("remote")),
        seniority=j.get("experienceLevel", ""),
        skills=j.get("technologyTags") or [],
        salary=f"{sal['money']} {sal.get('currency', '')}" if sal.get("money") and not sal.get("hidden") else "",
        # Listing written in Polish is a strong hint the job needs Polish.
        languages=["Polish:listing"] if j.get("locale") == "pl" else [],
    )


def fetch(http: Http, city: str | None = None, mode: str = "full", hours: int = 24) -> list[Job]:
    """full: every level, the city (Polish spelling, "Warszawa") + Remote, all pages. The listing has no dates, so
    freshness comes from our dedupe (only unseen jobs are processed) and from publishedAt filled in by enrich().
    narrow: junior + intern, first page only."""
    city = city or pl_city().title()
    jobs: dict[str, Job] = {}
    if mode == "narrow":
        urls = [LIST_URL.format(city=city, level=level) for level in ("junior", "intern")]
    else:
        urls = [f"https://bulldogjob.pl/companies/jobs/s/city,{c}" for c in (city, "Remote")]
    for base in urls:
        for page in range(1, 2 if mode == "narrow" else 20):
            url = base if page == 1 else f"{base}/page,{page}"
            batch = _next_data(http.get(url).text).get("props", {}).get("pageProps", {}).get("jobs", []) or []
            for j in batch:
                jobs.setdefault(j["id"], _to_job(j))
            if len(batch) < 50:
                break
    return list(jobs.values())


def enrich(http: Http, job: Job) -> Job:
    data = _next_data(http.get(job.url).text)
    j = data.get("props", {}).get("pageProps", {}).get("data", {}).get("job") or {}
    job.description = html_to_text("\n".join(
        str(j.get(k) or "") for k in ("details", "requirements", "offer", "recruitmentProcess")))
    if j.get("minExperienceInYears"):
        job.description += f"\nMinimum {j['minExperienceInYears']} years of experience required."
    job.posted_at = j.get("publishedAt") or job.posted_at
    job.expires_at = j.get("endsAt") or ""
    if j.get("redirectTo"):
        job.employer_url = j["redirectTo"]
    job.apply_url = j.get("redirectTo") or job.apply_url or job.url
    return job
