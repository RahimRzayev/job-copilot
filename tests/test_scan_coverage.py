"""Scan coverage: LinkedIn keyword slices past the result limit, JustJoin stop rule with promoted offers, scan window
since the last run, englishjobs listings, company career boards."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobhunt import pipeline
from jobhunt.db import connect, set_meta
from jobhunt.sources import justjoin, linkedin

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.yaml"


def card(i):
    return (f'<div class="base-search-card" data-entity-urn="urn:li:jobPosting:{i}">'
            f'<a class="base-card__full-link" href="x"></a><h3 class="base-search-card__title">Job {i}</h3>'
            f'<h4 class="base-search-card__subtitle">Co</h4><span class="job-search-card__location">Warsaw</span></div>')


class FakeLinkedIn:
    """No keyword: 1000 results (the limit). Keyword "python": 5 more jobs the unfiltered search never reached."""
    def __init__(self):
        self.calls = []

    def get(self, url, params):
        self.calls.append(params)
        start, kw = params["start"], params.get("keywords")
        ids = range(5000, 5005) if kw == "python" else ([] if kw else range(1000))
        page = [i for i in ids][start:start + 10]

        class R:
            text = "".join(card(i) for i in page)
        return R()


def test_linkedin_capped_search_is_split_by_keywords():
    http = FakeLinkedIn()
    jobs = linkedin.fetch(http, hours=24)
    ids = {j.source_id for j in jobs}
    assert len(ids) == 1005 and "5004" in ids
    assert any(c.get("keywords") == "python" for c in http.calls)
    assert all(s["keyword slices"] for s in linkedin.STATS)


def test_linkedin_slices_stop_when_time_budget_is_spent(monkeypatch):
    monkeypatch.setattr(linkedin, "SLICE_BUDGET_MINUTES", 0)
    http = FakeLinkedIn()
    linkedin.fetch(http, hours=72)
    assert not any(c.get("keywords") for c in http.calls)
    assert all(s["slices skipped"] == linkedin.slice_keywords() for s in linkedin.STATS)


def test_linkedin_slices_have_no_time_limit_by_default():
    assert linkedin.SLICE_BUDGET_MINUTES is None
    http = FakeLinkedIn()
    linkedin.fetch(http, hours=72)
    assert all(s["slices skipped"] == [] for s in linkedin.STATS)
    assert {c.get("keywords") for c in http.calls} >= set(linkedin.slice_keywords())


def test_slice_keywords_start_with_the_users_keywords(example_settings):
    from dataclasses import replace

    from jobhunt import settings
    words = linkedin.slice_keywords()
    assert words[:len(example_settings.keywords)] == list(example_settings.keywords)
    assert "python" in words and "programista" in words  # tech areas, then local-language words for PL
    settings.use(replace(example_settings, keywords=(), country_code="DE"))
    words = linkedin.slice_keywords()
    assert words[:3] == ["intern", "working student", "junior"] and "programista" not in words  # from levels
    settings.use(replace(example_settings, keywords=(), levels=(), country_code="DE"))
    assert linkedin.slice_keywords()[:4] == list(linkedin.DEFAULT_KEYWORDS)


def test_justjoin_stops_when_only_promoted_offers_are_fresh():
    now = datetime.now(timezone.utc)
    fresh, old = now.isoformat(), (now - timedelta(days=5)).isoformat()

    def offer(slug, when, promoted=False):
        return {"slug": slug, "title": "Dev", "companyName": "Co", "lastPublishedAt": when, "isPromoted": promoted}

    class FakeJJ:
        pages = 0

        def get(self, url, params):
            FakeJJ.pages += 1
            page = params["from"] // 100
            offers = ([offer(f"new{i}", fresh) for i in range(100)] if page == 0 else
                      [offer(f"p{page}-{i}", fresh, promoted=True) for i in range(5)] +
                      [offer(f"o{page}-{i}", old) for i in range(95)])

            class R:
                def json(self):
                    return {"data": offers}
            return R()

    jobs = justjoin.fetch(FakeJJ(), hours=24)
    # page 0 (100 fresh) + page 1 per query (only promoted fresh) -> stop; no walk through all 30 pages.
    assert FakeJJ.pages == 4
    assert {"new0", "p1-0"} <= {j.source_id for j in jobs}


def test_scan_window_covers_time_since_last_run(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert pipeline.window_hours(conn, pipeline.SCRAPE_META) == pipeline.SCAN_HOURS
    now = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc).timestamp()
    set_meta(conn, pipeline.SCRAPE_META, str(now - 55 * 3600))
    assert pipeline.window_hours(conn, pipeline.SCRAPE_META, now_ts=now) == 57
    set_meta(conn, pipeline.SCRAPE_META, str(now - 3 * 3600))
    assert pipeline.window_hours(conn, pipeline.SCRAPE_META, now_ts=now) == 24


EJ_PAGE = """# 3 English-speaking jobs in Warsaw

[### Working Student IT Support](/clickout/aaa111?ql=l&amp;sig=x)

* Acme
* Warszawa, mazowieckie
* October 19
* student
* part-time

Help users with laptops. English only.

report problem

[### Customer Success Specialist](/clickout_alt/bbb222?ql=l)

* Globex
* Warsaw
* October 2

![Logo](https://img.example/logo.png)

report problem
"""


def test_englishjobs_markdown_listing_is_parsed():
    from datetime import date

    from jobhunt.sources import englishjobs
    items = englishjobs.parse(EJ_PAGE, date(2026, 10, 19))
    (a, a_day), (b, b_day) = items
    assert (a.source_id, a.company, a.location, a.posted_at) == ("aaa111", "Acme", "Warszawa", "2026-10-19")
    assert a.seniority == "intern" and "Tags: student, part-time" in a.description and "&amp;" not in a.url
    assert b.description == "" and b_day == date(2026, 10, 2)


def test_student_roles_rank_above_the_same_role_without_student(monkeypatch):
    from jobhunt import prescore
    monkeypatch.setattr(prescore, "PROFILE", FIXTURE_PROFILE)
    plain = {"title": "IT Support Specialist", "description": "Help users."}
    student = {"title": "Working Student IT Support", "description": "Help users."}
    assert prescore.prescore(student, [])[0] > prescore.prescore(plain, [])[0]
    assert "student role" in prescore.prescore(student, [])[1]


class FakeJson:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        data = self.pages(url, params or {})

        class R:
            headers = {"content-type": "application/json"}
            text = ""

            def json(self):
                return data
        return R()


def test_oracle_hcm_keeps_only_home_country_rows():
    from jobhunt.sources import ats_boards

    def pages(url, params):
        if "Details" in url:
            return {"items": [{"ExternalDescriptionStr": "<p>Python internship</p>"}]}
        return {"items": [{"TotalJobsCount": 2, "requisitionList": [
            {"Id": "1", "Title": "Intern", "PrimaryLocation": "Warszawa, Mazowieckie, Poland",
             "PrimaryLocationCountry": "PL", "PostedDate": "2026-10-18"},
            {"Id": "2", "Title": "Analyst", "PrimaryLocation": "London, UK", "PrimaryLocationCountry": "GB"}]}]}
    jobs = ats_boards._oracle_hcm(FakeJson(pages), "Acme", "acme.fa.oraclecloud.com|CX_1001")
    assert [(j.title, j.location) for j in jobs] == [("Intern", "Warszawa")]
    assert "Python internship" in jobs[0].description and jobs[0].employer_url.endswith("/job/1")


def test_sf_rmk_reads_sites_served_under_a_path():
    from jobhunt.sources import ats_boards
    row = ('<tr class="data-row"><a href="/globex/job/Warszawa-Praktykant/1234/" class="jobTitle-link">Praktykant</a>'
           '<td class="colLocation"><span class="jobLocation">Warszawa, PL</span></td></tr>')

    class Html:
        def __init__(self):
            self.n = 0

        def get(self, url, params=None):
            self.n += 1
            text = row if self.n == 1 else ""

            class R:
                pass
            r = R()
            r.text = text
            return r
    jobs = ats_boards._sf_rmk(Html(), "Globex", "careers.globex.example/globex|")
    assert jobs[0].url == "https://careers.globex.example/globex/job/Warszawa-Praktykant/1234/"
    assert jobs[0].location == "Warszawa"


def test_phenom_keeps_home_country_rows_and_reads_the_job_page():
    from jobhunt.sources import ats_boards
    page = ('<script>phApp.ddo = {"jobDetail": {"data": {"job": {"description": "<p>SQL, Python. Students welcome.</p>"}}}};'
            ' phApp.x = 1</script>')

    class Http:
        def post(self, url, json):
            class R:
                def json(self):
                    return {"refineSearch": {"totalHits": 2, "data": {"jobs": [
                        {"jobId": "7", "title": "Intern in Finance", "city": "Warsaw", "country": "Poland",
                         "location": "Warsaw, Poland", "postedDate": "2026-10-16T15:58:27.000+0000"},
                        {"jobId": "8", "title": "Sales", "city": "Vienna", "country": "Austria", "location": "Vienna"}]}}}
            return R()

        def get(self, url):
            class R:
                text = page
            return R()
    jobs = ats_boards._phenom(Http(), "Initech", "careers.initech.example|gb/en")
    assert [(j.title, j.location, j.url) for j in jobs] == [
        ("Intern in Finance", "Warsaw", "https://careers.initech.example/gb/en/job/7")]
    assert "Students welcome" in jobs[0].description and jobs[0].posted_at.endswith("+00:00")
