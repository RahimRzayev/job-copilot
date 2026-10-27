"""justjoin.it — public candidate API (JSON). Polish board: the configured city plus remote jobs."""

from datetime import datetime, timedelta, timezone

from ..normalize import Job, html_to_text, parse_iso
from .base import Http, pl_city

API = "https://justjoin.it/api/candidate-api/offers"
LEVELS = ("intern", "junior")


def _salary(employment_types: list[dict]) -> str:
    for et in employment_types or []:
        if et.get("currency") == "PLN" and et.get("from"):
            return f"{et['from']:.0f}-{et.get('to') or et['from']:.0f} PLN/{et.get('unit', 'month')} ({et.get('type')})"
    return ""


def _languages(items: list[dict]) -> list[str]:
    return [f"{l.get('name') or l.get('code')}:{l.get('level', '')}" for l in items or []]


def _to_job(o: dict) -> Job:
    return Job(
        source="justjoin",
        source_id=o["slug"],
        title=o["title"],
        company=o["companyName"],
        url=f"https://justjoin.it/job-offer/{o['slug']}",
        location=o.get("city", ""),
        remote=o.get("workplaceType") == "remote",
        seniority=o.get("experienceLevel", ""),
        apply_url=o.get("applyUrl") or "",
        skills=[s["name"] for s in o.get("requiredSkills") or []],
        salary=_salary(o.get("employmentTypes")),
        posted_at=o.get("lastPublishedAt") or o.get("publishedAt") or "",
        languages=_languages(o.get("languages")),
        category=(o.get("category") or {}).get("key", ""),
        expires_at=o.get("expiredAt") or "",
        employer_url=o.get("applyUrl") or "",
    )


def fetch(http: Http, city: str | None = None, max_pages: int = 5, mode: str = "full", hours: int = 24) -> list[Job]:
    """full: every level, newest first, until listings are older than `hours`.
    narrow: intern + junior only. `city` defaults to the configured one in Polish ("Warszawa")."""
    city = city or pl_city().title()
    jobs: dict[str, Job] = {}
    queries = [{"city": city}, {"workplaceType": "remote"}]
    if mode == "narrow":
        for base in queries:
            for level in LEVELS:
                for page in range(max_pages):
                    params = {**base, "experienceLevels": level, "itemsCount": 100,
                              "from": page * 100, "orderBy": "descending", "sortBy": "publishedAt"}
                    data = http.get(API, params=params).json()
                    for offer in data.get("data", []):
                        jobs.setdefault(offer["slug"], _to_job(offer))
                    if len(data.get("data", [])) < 100:
                        break
        return list(jobs.values())

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for base in queries:
        for page in range(30):
            data = http.get(API, params={**base, "itemsCount": 100, "from": page * 100, "sortBy": "newest"}).json()
            offers = data.get("data", [])
            fresh = 0
            for offer in offers:
                published = parse_iso(offer.get("lastPublishedAt") or offer.get("publishedAt") or "")
                if published and published < cutoff:
                    continue
                jobs.setdefault(offer["slug"], _to_job(offer))
                if not (offer.get("isPromoted") or offer.get("isSuperOffer")):
                    fresh += 1
            # Promoted offers are mixed into every page regardless of date (10-30 per page), so only ordinary offers
            # decide when the window is over: counting promoted ones walked all 30 pages every run.
            if len(offers) < 100 or fresh == 0:
                break
    return list(jobs.values())


def enrich(http: Http, job: Job) -> Job:
    d = http.get(f"{API}/{job.source_id}").json()
    job.description = html_to_text(d.get("body", ""))
    job.languages = _languages(d.get("languages")) or job.languages
    job.skills = [s["name"] for s in d.get("requiredSkills") or []] or job.skills
    job.apply_url = d.get("applyUrl") or job.apply_url
    return job
