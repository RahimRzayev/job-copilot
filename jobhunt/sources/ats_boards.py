"""Company career boards with public JSON APIs. Companies come from companies.yaml.

Boards that can search by place are searched for the configured country (settings.country / country_code) and, where
a board only finds some postings by city, the configured city too; their rows from other countries are dropped here.
Boards without a place filter (Greenhouse, Lever, Ashby) return every job and the prefilter drops other locations.
"""

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from selectolax.parser import HTMLParser

from .. import settings
from ..normalize import Job, html_to_text, ms_to_iso
from .base import Http


def _greenhouse(http: Http, company: str, slug: str) -> list[Job]:
    data = http.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}).json()
    return [Job(
        source="greenhouse", source_id=str(j["id"]), title=j["title"], company=company,
        url=j["absolute_url"], apply_url=j["absolute_url"],
        location=(j.get("location") or {}).get("name", ""),
        description=html_to_text(html.unescape(j.get("content", ""))),
        posted_at=j.get("first_published") or j.get("updated_at") or "",
    ) for j in data.get("jobs", [])]


def _lever(http: Http, company: str, slug: str, host: str = "api.lever.co") -> list[Job]:
    data = http.get(f"https://{host}/v0/postings/{slug}", params={"mode": "json"}).json()
    return [Job(
        source="lever", source_id=j["id"], title=j["text"], company=company,
        url=j["hostedUrl"], apply_url=j.get("applyUrl", j["hostedUrl"]),
        location=(j.get("categories") or {}).get("location", ""),
        remote=(j.get("workplaceType") == "remote"),
        description=j.get("descriptionPlain", "") + "\n" + "\n".join(
            f"{l['text']}: {html_to_text(l['content'])}" for l in j.get("lists", [])),
        posted_at=ms_to_iso(j.get("createdAt")),
    ) for j in data]


def _lever_eu(http: Http, company: str, slug: str) -> list[Job]:
    # Lever's EU data centre: same API on another host.
    return _lever(http, company, slug, host="api.eu.lever.co")


def _ashby(http: Http, company: str, slug: str) -> list[Job]:
    data = http.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json()
    return [Job(
        source="ashby", source_id=j["id"], title=j["title"], company=company,
        url=j["jobUrl"], apply_url=j.get("applyUrl", j["jobUrl"]),
        location=j.get("location", ""), remote=bool(j.get("isRemote")),
        description=j.get("descriptionPlain", ""), posted_at=j.get("publishedAt", ""),
    ) for j in data.get("jobs", [])]


def _smartrecruiters(http: Http, company: str, slug: str) -> list[Job]:
    postings, offset = [], 0
    country = settings.get().country_code.lower()
    while True:  # 100 per page; big employers have more than one page per country
        data = http.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                        params={"limit": 100, "offset": offset, "country": country}).json()
        page = data.get("content", [])
        postings += page
        offset += 100
        if len(page) < 100 or offset >= data.get("totalFound", 0) or offset >= 1000:
            break
    jobs = []
    for j in postings:
        loc = j.get("location") or {}
        jobs.append(Job(
            source="smartrecruiters", source_id=j["id"], title=j["name"], company=company,
            url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
            apply_url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
            location=loc.get("city", ""), remote=bool(loc.get("remote")),
            seniority=(j.get("experienceLevel") or {}).get("id", ""),
            posted_at=j.get("releasedDate", ""),
        ))
    return jobs


def _recruitee(http: Http, company: str, slug: str) -> list[Job]:
    data = http.get(f"https://{slug}.recruitee.com/api/offers/").json()
    country = settings.get().country_code.upper()
    return [Job(
        source="recruitee", source_id=str(j["id"]), title=j["title"], company=company,
        url=j["careers_url"], apply_url=j.get("careers_apply_url") or j["careers_url"],
        location=j.get("city") or j.get("location", ""), remote=bool(j.get("remote")),
        description=html_to_text(f"{j.get('description', '')}\n{j.get('requirements', '')}"),
        posted_at=(j.get("published_at") or "").replace(" UTC", "+00:00").replace(" ", "T"),
    ) for j in data.get("offers", []) if (j.get("country_code") or country).upper() == country]


def _workable(http: Http, company: str, slug: str) -> list[Job]:
    data = http.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"}).json()
    s = settings.get()
    return [Job(
        source="workable", source_id=j["shortcode"], title=j["title"], company=company,
        url=j["url"], apply_url=j.get("application_url") or j["url"],
        location=j.get("city") or j.get("country", ""), remote=str(j.get("telecommuting")).lower() == "true",
        description=html_to_text(j.get("description", "")),
        posted_at=j.get("published_on") or j.get("created_at") or "",
    ) for j in data.get("jobs", []) if not j.get("country") or s.in_country(j["country"])]


def _capgemini(http: Http, company: str, slug: str) -> list[Job]:
    """Capgemini's own search API (slug = country code, e.g. pl-pl). It has no publish date (updated_at changes on
    every re-index), so freshness comes from first-seen time, like Bulldogjob."""
    api = "https://cg-job-search-microservices.azurewebsites.net/api/job-search"
    s = settings.get()
    jobs = []
    for page in range(1, 20):
        data = http.get(api, params={"country_code": slug, "size": 100, "page": page}).json().get("data") or []
        for j in data:
            cities = [c.strip() for c in (j.get("location") or "").split(",") if c.strip()]
            loc = next((c for c in cities if s.in_city(c)), cities[0] if cities else "")
            jobs.append(Job(
                source="capgemini", source_id=j["id"], title=j["title"], company=company,
                url=j["apply_job_url"], apply_url=j["apply_job_url"], location=loc,
                remote="remote" in (j.get("location") or "").lower(),
                seniority="intern" if "intern" in (j.get("experience_level") or "").lower() else "",
                description=html_to_text(html.unescape(j.get("description") or "")),
            ))
        if len(data) < 100:
            break
    return jobs


def _oracle_hcm(http: Http, company: str, slug: str) -> list[Job]:
    """Oracle Recruiting Cloud. slug = "host|siteNumber", e.g. "acme.fa.oraclecloud.com|CX_1001".
    An unknown location value returns every job, so the country is checked on each row."""
    host, site = slug.split("|")
    s = settings.get()
    api = f"https://{host}/hcmRestApi/resources/latest"
    rows, offset = [], 0
    while offset < 1000:
        d = http.get(f"{api}/recruitingCEJobRequisitions", params={
            "onlyData": "true", "expand": "requisitionList.secondaryLocations",
            "finder": f"findReqs;siteNumber={site},limit=200,offset={offset},keyword=,location={s.country}"}).json()
        item = (d.get("items") or [{}])[0]
        page = item.get("requisitionList") or []
        rows += page
        offset += 200
        if len(page) < 200 or offset >= item.get("TotalJobsCount", 0):
            break
    jobs = []
    for r in rows:
        if (r.get("PrimaryLocationCountry") or "").upper() != s.country_code.upper():
            continue
        try:
            det = (http.get(f"{api}/recruitingCEJobRequisitionDetails", params={
                "expand": "all", "onlyData": "true", "finder": f'ById;Id="{r["Id"]}",siteNumber={site}'}).json()
                .get("items") or [{}])[0]
        except Exception:
            det = {}
        url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}"
        jobs.append(Job(
            source="oracle_hcm", source_id=str(r["Id"]), title=r["Title"], company=company, url=url, apply_url=url,
            employer_url=url, location=(r.get("PrimaryLocation") or "").split(",")[0],
            remote="remote" in (r.get("WorkplaceType") or "").lower(),
            description=html_to_text("\n".join(str(det.get(k) or "") for k in (
                "ExternalDescriptionStr", "ExternalQualificationsStr", "ExternalResponsibilitiesStr")))
                or r.get("ShortDescriptionStr") or "",
            posted_at=r.get("PostedDate") or "",
        ))
    return jobs


def _sf_csb(http: Http, company: str, slug: str) -> list[Job]:
    """SuccessFactors Career Site Builder JSON search, which sf_rmk cannot read.
    slug = "host|location|locale" (empty location = the configured country). Descriptions come from the job page."""
    host, location, locale = slug.split("|")
    s = settings.get()
    location = location or s.country
    rows, page = [], 0
    while page < 20:
        d = http.post(f"https://{host}/services/recruiting/v1/jobs", json={
            "locale": locale, "pageNumber": page, "keywords": "", "location": location, "facetFilters": {},
            "sortBy": "", "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""}).json()
        batch = [r.get("response") or {} for r in d.get("jobSearchResult") or []]
        rows += batch
        page += 1
        if not batch or len(rows) >= d.get("totalJobs", 0):
            break
    jobs = []
    for r in rows:
        url = f"https://{host}/job/{r.get('urlTitle', 'job')}/{r['id']}-{locale}/"
        try:
            tree = HTMLParser(http.get(url).text)
            desc = "\n".join(n.text(separator="\n") for n in tree.css('[itemprop="description"]'))
        except Exception:
            desc = ""
        locs = [x.strip() for x in r.get("jobLocationShort") or []]
        where = next((x for x in locs if s.in_city(x)), locs[0] if locs else "")
        jobs.append(Job(
            source="sf_csb", source_id=str(r["id"]), title=r.get("unifiedStandardTitle") or r.get("urlTitle", ""),
            company=company, url=url, apply_url=url, employer_url=url,
            location=where.split("-")[-1].strip() if "-" in where else where,
            remote=any("remote" in x.lower() for x in locs + (r.get("custWorkMode") or [])), description=desc,
        ))
    return jobs


_PHENOM_DDO = re.compile(r"phApp\.ddo = (\{.*?\});\s*phApp", re.S)


def _phenom(http: Http, company: str, slug: str) -> list[Job]:
    """Phenom career sites: the public /widgets refineSearch API, filtered to the configured country, then each job
    page's embedded phApp.ddo JSON for the full description.
    slug = "host|locale path", e.g. "careers.acme.com|gb/en". Some sites ignore the country facet and return other
    countries, so each row's country is checked."""
    host, loc_path = slug.split("|")
    s = settings.get()
    rows, start = [], 0
    while start < 500:
        d = http.post(f"https://{host}/widgets", json={
            "ddoKey": "refineSearch", "pageName": "search-results", "deviceType": "desktop", "siteType": "external",
            "sortBy": "Most recent", "from": start, "size": 50, "jobs": True, "counts": False, "keywords": "",
            "global": True, "selected_fields": {"country": [s.country]}, "locationData": {}}).json()
        r = d.get("refineSearch") or {}
        batch = (r.get("data") or {}).get("jobs") or []
        rows += batch
        start += len(batch)
        if not batch or start >= r.get("totalHits", 0):
            break
    jobs = []
    for j in rows:
        places = [j.get("location") or ""] + list(j.get("multi_location") or [])
        home = s.in_country(j.get("country") or "")
        if not home and not any(s.in_country(p) for p in places):
            continue
        url = f"https://{host}/{loc_path}/job/{j['jobId']}"
        desc = j.get("descriptionTeaser") or ""
        try:
            m = _PHENOM_DDO.search(http.get(url).text)
            detail = ((json.loads(m.group(1)).get("jobDetail") or {}).get("data") or {}).get("job") or {} if m else {}
            desc = html_to_text(detail.get("description") or "") or desc
        except Exception:
            pass
        city = j.get("city") or ""
        if not home:  # multi-location job listed under another country first
            city = next((p.split(",")[0] for p in places if s.in_country(p)), city)
        jobs.append(Job(
            source="phenom", source_id=str(j["jobId"]), title=j.get("title", ""), company=company,
            url=url, apply_url=j.get("applyUrl") or url, employer_url=url, location=city.title(),
            remote="remote" in f"{j.get('location', '')} {j.get('type', '')}".lower(), description=desc,
            posted_at=(j.get("postedDate") or "").replace("+0000", "+00:00"),
        ))
    return jobs


_POSTED = re.compile(r"Posted (Today|Yesterday|(\d+)\+? Days? Ago)", re.I)


def _workday_posted(text: str) -> str:
    m = _POSTED.search(text or "")
    if not m:
        return ""
    days = 0 if m.group(1).lower() == "today" else 1 if m.group(1).lower() == "yesterday" else int(m.group(2))
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _workday(http: Http, company: str, slug: str, searches: tuple[str, ...] | None = None,
             max_offset: int = 400) -> list[Job]:
    """slug = 'tenant/wdN/site', e.g. 'acme/wd5/AcmeCareers'. Lists jobs matching the configured country
    or city: the country alone misses postings whose location names only a site in the city ("<CITY> PLANT")."""
    s = settings.get()
    searches = searches or tuple(x for x in (s.country, s.city) if x)
    tenant, wd, site = slug.split("/")
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}/jobs"
    jobs, paths = [], set()
    for text in searches:
        offset = 0
        while offset < max_offset:
            d = http.post(api, json={"limit": 20, "offset": offset, "searchText": text, "appliedFacets": {}}).json()
            batch = d.get("jobPostings", [])
            for j in batch:
                # One posting without a title ('title' KeyError) used to abort the whole board; skip just that entry.
                if not j.get("title") or not j.get("externalPath") or j["externalPath"] in paths:
                    continue
                path = j["externalPath"]
                paths.add(path)
                loc = j.get("locationsText", "")
                if re.match(r"\d+ Locations", loc):
                    loc = f"{loc} ({s.country} search)"  # exact locations come from enrich()
                jobs.append(Job(
                    source="workday", source_id=f"{tenant}{path}", title=j["title"], company=company,
                    url=f"{host}/{site}{path}", apply_url=f"{host}/{site}{path}/apply",
                    employer_url=f"{host}/{site}{path}", location=loc, posted_at=_workday_posted(j.get("postedOn", "")),
                ))
            offset += 20
            if len(batch) < 20 or offset >= d.get("total", 0):
                break
    return jobs


def enrich_workday(http: Http, job: Job) -> Job:
    """Description + real locations from the CXS job endpoint."""
    p = job.employer_url.split(".myworkdayjobs.com/")
    host = p[0] + ".myworkdayjobs.com"
    tenant = host.split("//")[1].split(".")[0]
    site, path = p[1].split("/", 1)
    info = http.get(f"{host}/wday/cxs/{tenant}/{site}/{path}", headers={"Accept": "application/json"}).json().get("jobPostingInfo", {})
    job.description = html_to_text(html.unescape(info.get("jobDescription", "")))
    locs = [info.get("location", "")] + (info.get("additionalLocations") or [])
    job.location = "; ".join(l for l in locs if l) or job.location
    job.remote = job.remote or "remote" in (info.get("remoteType") or "").lower()
    if info.get("startDate"):
        job.posted_at = info["startDate"] + "T00:00:00+00:00"
    return job


# Some sites are served under a path (host/<path>, links /<path>/job/...), so a one-segment prefix is allowed.
_RMK_TITLE = re.compile(r'<a href="((?:/[\w-]+)?/job/[^"]+/(\d+)/)" class="jobTitle-link">([^<]+)</a>')
_RMK_LOC = re.compile(r'<td class="colLocation[^>]*>\s*<span class="jobLocation">\s*([^<]*?)\s*</span>', re.S)
_RMK_DATE = re.compile(r'<span class="jobDate[^"]*">\s*([^<]*?)\s*</span>', re.S)


def _sf_rmk(http: Http, company: str, slug: str) -> list[Job]:
    """SuccessFactors career site (RMK): plain HTML search, paged with startrow.

    slug = "host[/path]|location", e.g. "jobs.acme.com|Berlin" (empty location = the configured country).
    """
    host, _, where = slug.partition("|")
    jobs, seen, start = [], set(), 0
    while start < 500:
        page = http.get(f"https://{host}/search/", params={"q": "", "locationsearch": where or settings.get().country,
                                                           "startrow": start}).text
        chunks = page.split('<tr class="data-row">')[1:]
        if not chunks:
            break
        for chunk in chunks:
            t = _RMK_TITLE.search(chunk)
            if not t or t.group(2) in seen:
                continue
            seen.add(t.group(2))
            loc, date = _RMK_LOC.search(chunk), _RMK_DATE.search(chunk)
            try:
                # "11 Sept 2026" / "3 Oct 2026": keep the first three letters of the month.
                d, mon, yr = date.group(1).split()
                posted = datetime.strptime(f"{d} {mon[:3]} {yr}", "%d %b %Y").replace(tzinfo=timezone.utc).isoformat()
            except (AttributeError, ValueError):  # no date cell / unexpected format
                posted = ""
            url = f"https://{host.split('/')[0]}{t.group(1)}"
            jobs.append(Job(source="sf_rmk", source_id=t.group(2), title=html.unescape(t.group(3)).strip(),
                            company=company, url=url, apply_url=url, employer_url=url, posted_at=posted,
                            location=html.unescape(loc.group(1)).split(",")[0] if loc else ""))
        start += len(chunks)
    return jobs


# Big-tech career sites: their internships/graduate roles are not always on LinkedIn.
# slug = the country searched (empty = the configured country).
# ISO alpha-2 -> alpha-3, which amazon.jobs filters by; a 3-letter slug is used as is.
ALPHA3 = {"AT": "AUT", "BE": "BEL", "CA": "CAN", "CH": "CHE", "CZ": "CZE", "DE": "DEU", "DK": "DNK", "ES": "ESP",
          "FI": "FIN", "FR": "FRA", "GB": "GBR", "IE": "IRL", "IN": "IND", "IT": "ITA", "LU": "LUX", "NL": "NLD",
          "NO": "NOR", "PL": "POL", "PT": "PRT", "RO": "ROU", "SE": "SWE", "TR": "TUR", "US": "USA"}


def _amazon(http: Http, company: str, slug: str) -> list[Job]:
    s = settings.get()
    where = slug or s.country
    if len(where) == 3:
        country = where.upper()
    elif s.in_country(where):
        country = ALPHA3.get(s.country_code.upper(), where)
    else:
        country = where
    jobs, offset = [], 0
    while offset < 500:
        d = http.get("https://www.amazon.jobs/en/search.json", params={
            "normalized_country_code[]": country, "result_limit": 100, "offset": offset, "sort": "recent"}).json()
        batch = d.get("jobs", [])
        for j in batch:
            try:
                posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                posted = ""
            url = f"https://www.amazon.jobs{j['job_path']}"
            jobs.append(Job(
                source="amazon", source_id=str(j.get("id_icims") or j["id"]), title=j["title"],
                company=j.get("company_name") or company, url=url, apply_url=url, employer_url=url,
                location=j.get("city") or j.get("normalized_location", ""), posted_at=posted,
                seniority="intern" if j.get("is_intern") else "",
                description=html_to_text("\n".join(j.get(k) or "" for k in
                                                    ("description", "basic_qualifications", "preferred_qualifications"))),
            ))
        offset += len(batch)
        if len(batch) < 100 or offset >= d.get("hits", 0):
            break
    return jobs


def _pcsx(http: Http, company: str, host: str, domain: str, country: str, source: str) -> list[Job]:
    """Eightfold's PCSX career API: search by location, then one details call per job."""
    api = f"https://{host}/api/pcsx"
    jobs, start = [], 0
    while start < 300:
        d = http.get(f"{api}/search", params={"domain": domain, "query": "", "location": country,
                                              "start": start, "sort_by": "timestamp"}).json().get("data") or {}
        batch = d.get("positions") or []
        for p in batch:
            if not any(country.lower() in loc.lower() for loc in p.get("locations") or []):
                continue  # the search also returns nearby countries and remote roles elsewhere
            url = f"https://{host}/careers/job/{p['id']}"
            try:
                info = http.get(f"{api}/position_details", params={"position_id": p["id"], "domain": domain}).json()
                desc = html_to_text((info.get("data") or {}).get("jobDescription", ""))
            except Exception:
                desc = ""
            jobs.append(Job(
                source=source, source_id=str(p["id"]), title=p["name"], company=company, url=url, apply_url=url,
                employer_url=url, location=_eightfold_city(p.get("locations") or [], country),
                remote=p.get("workLocationOption") == "remote", description=desc,
                posted_at=datetime.fromtimestamp(p["postedTs"], timezone.utc).isoformat() if p.get("postedTs") else "",
            ))
        start += len(batch)
        if not batch or start >= d.get("count", 0):
            break
    return jobs


def _eightfold_city(locations: list[str], country: str) -> str:
    """"Berlin,Berlin,Germany" -> "Berlin" (the configured city first when a job lists several cities in the
    country)."""
    here = [loc for loc in locations if country.lower() in loc.lower()] or locations or [""]
    best = next((loc for loc in here if settings.get().in_city(loc)), here[0])
    return best.split(",")[0].strip()


def _microsoft(http: Http, company: str, slug: str) -> list[Job]:
    return _pcsx(http, company, "apply.careers.microsoft.com", "microsoft.com", slug or settings.get().country,
                 "microsoft")


def _eightfold(http: Http, company: str, slug: str) -> list[Job]:
    """slug = "host|domain" (e.g. "jobs.acme.com|acme.com"). Newer sites answer the PCSX API; older ones
    only the apply/v2 API, which needs one details call per job for the description."""
    host, domain = slug.split("|")
    s = settings.get()
    try:
        probe = http.get(f"https://{host}/api/pcsx/search", params={"domain": domain, "query": "", "location": s.country})
        pcsx = probe.headers.get("content-type", "").startswith("application/json") and "data" in probe.json()
    except Exception:  # 403 on apply/v2-only sites
        pcsx = False
    if pcsx:
        return _pcsx(http, company, host, domain, s.country, "eightfold")
    api = f"https://{host}/api/apply/v2/jobs"
    jobs, start = [], 0
    while start < 500:
        d = http.get(api, params={"domain": domain, "location": s.country, "start": start, "num": 50}).json()
        batch = d.get("positions") or []
        for p in batch:
            if not any(s.in_country(loc) for loc in p.get("locations") or []):
                continue
            try:
                desc = html_to_text(http.get(f"{api}/{p['id']}", params={"domain": domain}).json().get("job_description", ""))
            except Exception:
                desc = ""
            url = p.get("canonicalPositionUrl") or f"https://{host}/careers/job/{p['id']}"
            jobs.append(Job(
                source="eightfold", source_id=str(p["id"]), title=p["name"], company=company, url=url, apply_url=url,
                employer_url=url, location=_eightfold_city(p.get("locations") or [], s.country),
                remote=p.get("work_location_option") == "remote", description=desc,
                posted_at=datetime.fromtimestamp(int(p["t_create"]), timezone.utc).isoformat() if p.get("t_create") else "",
            ))
        start += len(batch)
        if not batch or start >= d.get("count", 0):
            break
    return jobs


_GOOGLE_DATA = re.compile(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(.*?), sideChannel", re.S)


def _google(http: Http, company: str, slug: str) -> list[Job]:
    """Google Careers results page: the job list is embedded as JSON in the 'ds:1' data callback, 20 per page."""
    import json
    jobs: list[Job] = []
    for page in range(1, 20):
        m = _GOOGLE_DATA.search(http.get("https://www.google.com/about/careers/applications/jobs/results",
                                         params={"location": slug or settings.get().country, "sort_by": "date",
                                                 "page": page}).text)
        rows = (json.loads(m.group(1)) or [[]])[0] if m else []
        for r in rows or []:
            text = lambda i: (r[i][1] if isinstance(r[i], list) and len(r[i]) > 1 else "") or ""
            url = f"https://www.google.com/about/careers/applications/jobs/results/{r[0]}"
            locs = [l[0] for l in (r[9] or []) if l]
            jobs.append(Job(
                source="google", source_id=str(r[0]), title=r[1], company=company, url=url, apply_url=url,
                employer_url=url, location="; ".join(locs),
                description=html_to_text("\n".join([text(10), text(3), text(4), text(19)])),
                posted_at=datetime.fromtimestamp(r[12][0], timezone.utc).isoformat() if r[12] else "",
            ))
        if not rows or len(rows) < 20:
            break
    return jobs


BOARDS = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "lever_eu": _lever_eu,
    "recruitee": _recruitee,
    "workable": _workable,
    "capgemini": _capgemini,
    "eightfold": _eightfold,
    "oracle_hcm": _oracle_hcm,
    "sf_csb": _sf_csb,
    "phenom": _phenom,
    "ashby": _ashby,
    "smartrecruiters": _smartrecruiters,
    "workday": _workday,
    "sf_rmk": _sf_rmk,
    "amazon": _amazon,
    "microsoft": _microsoft,
    "google": _google,
}


def fetch(http: Http, companies: list[dict], workers: int = 8) -> tuple[list[Job], list[str]]:
    """companies: [{name, ats, slug}]. Returns jobs + per-company error messages.
    Boards live on different hosts, so `workers` fetch them in parallel, each with its own Http (polite delay per
    host): one by one, a watchlist of 200+ companies would take most of an hour. workers=1 uses `http` itself
    (tests)."""
    def one(c: dict) -> tuple[list[Job], str | None]:
        handler = BOARDS.get(c.get("ats", ""))
        if not handler:
            return [], f"{c.get('name')}: unsupported ats {c.get('ats')!r}"
        own = http if workers == 1 else Http()
        try:
            return handler(own, c["name"], c.get("slug") or ""), None
        except Exception as e:  # one broken board must not stop the rest
            return [], f"{c['name']}: {e}"
        finally:
            if own is not http:
                own.client.close()

    jobs, errors = [], []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for batch, err in pool.map(one, companies):
            jobs.extend(batch)
            if err:
                errors.append(err)
    return jobs, errors
