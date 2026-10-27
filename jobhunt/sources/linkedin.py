"""LinkedIn public guest job search — read-only discovery, low volume, no login.

Applying through LinkedIn is never automated (ToS / account ban risk).
"""

import re
import sys
import time
from urllib.parse import unquote

from selectolax.parser import HTMLParser

from .. import settings
from ..normalize import Job, html_to_text
from .base import Http

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"

# A search returning this many jobs has hit LinkedIn's result limit: it is re-run once per slice keyword.
CAP = 900
# Slice keywords, in order (until the time budget is spent, if one is set): the user's search keywords (these
# words for their levels when none are set; "student" also matches "working student"), then the user's target roles
# (tech areas when `tech_only` and no roles are set), then local-language tech job words when `tech_only`.
DEFAULT_KEYWORDS = ("intern", "junior", "graduate", "trainee")
# settings.levels -> search words, when settings.keywords is empty.
LEVEL_KEYWORDS = {"working student": "working student", "student": "student", "internship": "intern",
                  "intern": "intern", "trainee": "trainee", "graduate": "graduate", "junior": "junior",
                  "entry level": "entry level", "mid": "mid"}


def level_keywords() -> list[str]:
    """Search words for the user's levels, in their order; DEFAULT_KEYWORDS when no level maps to one."""
    words = [LEVEL_KEYWORDS[lv] for lv in settings.get().levels if lv in LEVEL_KEYWORDS]
    return list(dict.fromkeys(words)) or list(DEFAULT_KEYWORDS)
TECH_SLICES = (
    "python", "software", "developer", "engineer", "data", "analyst", "javascript", "typescript", "react", "java",
    ".net", "devops", "cloud", "security", "qa", "tester", "it support", "helpdesk", "administrator", "ai",
    "machine learning",
)
LOCAL_SLICES = {"PL": ("programista", "analityk")}  # tech words by settings.country_code
# None = every slice runs; a number of minutes caps the slicing time. All slices of a 72 h window can take over two
# hours with 429 waits, but a budget silently skips keywords, so it is off by default.
SLICE_BUDGET_MINUTES: float | None = None
STATS: list[dict] = []  # per base search of the last fetch(): jobs found, whether keyword slices ran

TECH_KEYWORDS = [
    "software engineer", "developer", "python", "frontend", "backend", "full stack",
    "data", "devops", "cloud", "security", "qa", "it support",
]


def narrow_keywords() -> list[str]:
    """Narrow mode searches: the user's target roles, else tech areas when `tech_only`, else the search keywords."""
    s = settings.get()
    return list(s.roles) or (TECH_KEYWORDS if s.tech_only else list(s.keywords or level_keywords()))


def slice_keywords() -> list[str]:
    s = settings.get()
    roles = s.roles or (TECH_SLICES if s.tech_only else ())
    local = LOCAL_SLICES.get(s.country_code.upper(), ()) if s.tech_only else ()
    words = (*(s.keywords or level_keywords()), *roles, *local)
    return list(dict.fromkeys(w.lower() for w in words))


def location_searches() -> list[dict]:
    """Full-mode base searches: every job in the city, and every job in the country (remote ones among them)."""
    s = settings.get()
    out = []
    if s.city:
        out.append({"location": f"{s.city}, {s.country}" if s.country else s.city})
    if s.country:
        out.append({"location": s.country, "f_WT": "2"})
    return out


def _text(node) -> str:
    return " ".join(node.text().split()) if node else ""


def _parse_cards(markup: str) -> list[Job]:
    jobs = []
    for card in HTMLParser(markup).css("div.base-search-card"):
        urn = card.attributes.get("data-entity-urn", "")
        job_id = urn.rsplit(":", 1)[-1]
        link = card.css_first("a.base-card__full-link")
        when = card.css_first("time")
        if not job_id or not link:
            continue
        location = _text(card.css_first(".job-search-card__location"))
        jobs.append(Job(
            source="linkedin",
            source_id=job_id,
            title=_text(card.css_first(".base-search-card__title")),
            company=_text(card.css_first(".base-search-card__subtitle")),
            url=f"https://www.linkedin.com/jobs/view/{job_id}",
            location=location.split(",")[0],
            remote="remote" in location.lower(),
            posted_at=(when.attributes.get("datetime") or "") if when else "",
        ))
    return jobs


def _progress(msg: str) -> None:
    """One stderr line per search: a full scan runs 20+ minutes and otherwise prints nothing until the end."""
    print(f"linkedin: {msg}", file=sys.stderr, flush=True)


def fetch(http: Http, location: str | None = None, hours: int = 24, pages: int = 2,
          mode: str = "full") -> list[Job]:
    """full: no keyword — every job posted in the city, plus every job in the country (remote ones among them),
    within `hours`. A search that reaches LinkedIn's ~1000-result limit is repeated per slice_keywords().
    narrow: narrow_keywords() x `pages` pages in `location` (default "<city>, <country>"), entry-level/internship only."""
    jobs: dict[str, Job] = {}
    if mode == "narrow":
        s = settings.get()
        location = location or ", ".join(x for x in (s.city, s.country) if x)
        for kw in narrow_keywords():
            for page in range(pages):
                params = {"keywords": kw, "location": location, "f_TPR": f"r{hours * 3600}",
                          "f_E": "1,2", "start": page * 25}  # 1 = internship, 2 = entry level
                batch = _parse_cards(http.get(SEARCH, params=params).text)
                for j in batch:
                    jobs.setdefault(j.source_id, j)
                if len(batch) < 10:
                    break
        return list(jobs.values())

    # Keyword "remote" does not narrow the country search: it matches any mention, so both searches use the same
    # slices. A slice can still hit the limit on a long window.
    searches = location_searches()
    keywords = slice_keywords()
    window = {"f_TPR": f"r{hours * 3600}"}
    found = []
    for base in searches:
        found.append(_search(http, {**base, **window}))
        _progress(f"{base['location']}: {len(found[-1])} jobs" + (" (limit reached: keyword slices next)"
                                                                     if len(found[-1]) >= CAP else ""))
    capped = [len(f) >= CAP for f in found]
    sizes: list[list[int]] = [[] for _ in searches]
    skipped: list[list[str]] = [[] for _ in searches]
    # One search stops at ~990 results even when a country has thousands of jobs in the window, and the guest
    # endpoint ignores the level/job-type/remote filters, so only keywords split it into smaller lists. Keywords run
    # for both searches in turn, so a spent budget skips the least relevant keywords of both (city first would use
    # the whole budget and leave the country search no slice).
    deadline = None if SLICE_BUDGET_MINUTES is None else time.monotonic() + SLICE_BUDGET_MINUTES * 60
    for kw in keywords:
        for i, base in enumerate(searches):
            if not capped[i]:
                continue
            if deadline is not None and time.monotonic() > deadline:
                skipped[i].append(kw)
                continue
            part = _search(http, {**base, "keywords": kw, **window})
            sizes[i].append(len(part))
            found[i].update(part)
            _progress(f"{base['location']} · {kw!r}: {len(part)} jobs ({len(found[i])} so far)")
    STATS.clear()
    for i, base in enumerate(searches):
        STATS.append({"search": base["location"], "jobs": len(found[i]), "keyword slices": capped[i],
                      "slices at limit": sum(1 for n in sizes[i] if n >= CAP), "slices skipped": skipped[i]})
        for j in found[i].values():
            if base.get("f_WT") == "2" and not settings.get().in_city(j.location):
                # The guest endpoint ignores f_WT (same results with f_WT=1, 2 or none), so this is really the whole
                # country. remote=True keeps other-city jobs until their description is read:
                # prefilter.work_mode_reason then drops LinkedIn jobs pinned to another city without remote wording.
                # Jobs in the user's city need no such pass, and the flag would show them as remote in the report.
                j.remote = True
            jobs.setdefault(j.source_id, j)
    return list(jobs.values())


def _search(http: Http, params: dict) -> dict[str, Job]:
    """Every card of one guest search, 10 per request, until two empty pages or LinkedIn's ~1000-result end."""
    found: dict[str, Job] = {}
    empty = 0
    for start in range(0, 1000, 10):
        batch = _parse_cards(http.get(SEARCH, params={**params, "start": start}).text)
        for j in batch:
            found.setdefault(j.source_id, j)
        empty = empty + 1 if not batch else 0
        if empty >= 2:
            break
    return found


def enrich(http: Http, job: Job) -> Job:
    tree = HTMLParser(http.get(DETAIL + job.source_id).text)
    desc = tree.css_first(".show-more-less-html__markup")
    job.description = html_to_text(desc.html) if desc else ""
    criteria = [_text(n) for n in tree.css(".description__job-criteria-text")]
    if criteria:
        level = criteria[0].lower()
        job.seniority = {"internship": "intern", "entry level": "junior"}.get(level, level)
    apply = tree.css_first("a.apply-button, code#applyUrl")
    if apply is not None:
        raw = apply.attributes.get("href") or apply.text()
        m = re.search(r"url=([^&\"]+)", raw or "")
        job.apply_url = m.group(1) if m else ""
        job.employer_url = unquote(job.apply_url) if job.apply_url else ""
    return job
