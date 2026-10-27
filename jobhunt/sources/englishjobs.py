"""englishjobs.pl — Polish aggregator of English-speaking jobs (no Polish required by default).

Every page is served as Markdown with `format=md` (documented in the site's llms.txt), newest first, 20 per page.
Job links are paid "clickout" redirects (Adzuna etc., utm_medium=ppc) and often answer 403 to scripts, so they are
never followed in bulk: the listing summary is the description, and the posting is opened only when we apply.
"""

import re
from datetime import date, datetime, timedelta, timezone

from .. import settings
from ..normalize import Job
from .base import Http, pl_city, slug

BASE = "https://englishjobs.pl"
MAX_PAGES = 40


def searches(city: str | None = None) -> list[tuple[str, bool, bool]]:
    """(path, remote, student): every English job in the city (board slug, "warszawa"), remote jobs in the country,
    and, when student roles are among settings.levels, the city's student tag. Student-tagged jobs count as
    programmes (14-day window) even when the title says "Risk Analyst"."""
    city = city or slug(pl_city())
    out = [(f"/in/{city}", False, False), ("/jobs/remote", True, False)]
    if settings.get().students:
        out.append((f"/in/{city}?tags=student", False, True))
    return out


_ITEM = re.compile(r"\[### (?P<title>.+?)\]\((?P<url>/clickout\w*/(?P<id>\w+)[^)]*)\)\s*\n\n(?P<meta>(?:\* .*\n)+)"
                   r"(?P<rest>[\s\S]*?)(?=report problem|\[### |\Z)")
_DATE = re.compile(r"^(January|February|March|April|May|June|July|August|September|October|November|December) "
                   r"(\d{1,2})$")
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


def _posted(text: str, today: date) -> date | None:
    m = _DATE.match(text)
    if not m:
        return None
    day = date(today.year, _MONTHS.index(m.group(1)) + 1, int(m.group(2)))
    return day if day <= today else day.replace(year=today.year - 1)  # "December 30" seen in January


def parse(markdown: str, today: date, remote: bool = False, student: bool = False) -> list[tuple[Job, date | None]]:
    out = []
    for m in _ITEM.finditer(markdown):
        bullets = [b[2:].strip() for b in m.group("meta").strip().splitlines()]
        when_at = next((i for i, b in enumerate(bullets) if _posted(b, today)), None)
        head = bullets[:when_at] if when_at is not None else bullets[:2]
        tags = bullets[when_at + 1:] if when_at is not None else []
        when = _posted(bullets[when_at], today) if when_at is not None else None
        company = head[0] if head else ""
        location = head[1].split(",")[0] if len(head) > 1 else ""
        url = BASE + m.group("url").replace("&amp;", "&")
        # Some items have only a company logo instead of a summary.
        summary = " ".join(ln.strip() for ln in m.group("rest").splitlines() if ln.strip() and not ln.startswith("!["))
        out.append((Job(
            source="englishjobs", source_id=m.group("id"), title=m.group("title").strip(), company=company,
            url=url, apply_url=url, location=location,
            remote=remote or "remote" in location.lower(),
            seniority="intern" if student or "student" in tags else "",
            description="\n".join([summary, "Tags: " + ", ".join(tags) if tags else ""]).strip(),
            posted_at=when.isoformat() if when else "",
        ), when))
    return out


def fetch(http: Http, hours: int = 24, mode: str = "full") -> list[Job]:
    """Pages of each search until a page has nothing posted inside the window (dates are day-only, so the window
    starts at the beginning of its first day). The student tag is read whole: it is small (a few dozen jobs)."""
    now = datetime.now(timezone.utc)
    today = now.date()
    first_day = (now - timedelta(hours=hours)).date()
    jobs: dict[str, Job] = {}
    for path, remote, student in searches():
        for page in range(1, MAX_PAGES + 1):
            sep = "&" if "?" in path else "?"
            url = f"{BASE}{path}{sep}format=md" + (f"&page={page}" if page > 1 else "")
            items = parse(http.get(url).text, today, remote=remote, student=student)
            if not items:
                break
            fresh = 0
            for job, when in items:
                if student or when is None or when >= first_day:
                    fresh += 1
                    jobs.setdefault(job.source_id, job)
            if not fresh:
                break
    return list(jobs.values())
