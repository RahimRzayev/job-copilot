import json

from jobhunt.alerts import load, search_params, split_place, to_job


def test_split_place():
    assert split_place("Warsaw (Hybrid)") == ("Warsaw", False)
    assert split_place("Poland (Remote)") == ("Poland", True)
    assert split_place("Warsaw, Mazowieckie, Poland (On-site)") == ("Warsaw", False)
    assert split_place("Warsaw") == ("Warsaw", False)
    assert split_place("") == ("", False)


def test_to_job_keeps_alert_keyword_and_salary():
    job = to_job({"source_id": "4367931628", "title": "Mid Frontend Developer (Angular)", "company": "Acme",
                  "place": "Poland (Remote)", "salary": "PLN 15K-PLN 18K / month",
                  "posted_at": "2026-10-02T05:01:00.000Z", "alert": "software engineer"})
    assert job.source == "linkedin" and job.remote and job.location == "Poland"
    assert job.url == "https://www.linkedin.com/jobs/view/4367931628"
    assert job.category == "alert:software engineer"
    assert job.salary.startswith("PLN")


def test_load_payload_shape(tmp_path):
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"stats": {"emails": 1}, "jobs": [
        {"source_id": "1", "title": "Python Intern", "company": "X", "place": "Warsaw (Hybrid)", "alert": "intern"},
        {"title": "no id, skipped"},
    ]}))
    stats, jobs, searches = load(f)
    assert searches == []
    assert stats == {"emails": 1}
    assert [j.source_id for j in jobs] == ["1"]


def test_search_params_drop_tracking_and_time_window():
    url = ("https://www.linkedin.com/comm/jobs/search?keywords=Marketing+Assistant&distance=25&geoId=123456789"
           "&f_TPR=a1700000000-&f_WT=1%2C3%2C2&sortBy=R&origin=JOB_ALERT_EMAIL&originToLandingJobPostings=1,2"
           "&lipi=urn%3Ali&midToken=AQ&trk=eml&otpToken=MT")
    assert search_params(url) == {"keywords": "Marketing Assistant", "distance": "25", "geoId": "123456789",
                                  "f_WT": "1,3,2"}


def test_search_params_new_search_results_link():
    # Newer link format /comm/jobs/search-results/; the salary filter is dropped (never filter on salary).
    url = ("https://www.linkedin.com/comm/jobs/search-results/?keywords=nurse+OR+care+assistant&distance=49%2E7"
           "&f_TPR=a1700000001-&f_SAL=f_SA_id_100001%3A200001&geoId=987654321&origin=SEMANTIC_SEARCH_JOB_ALERT_EMAIL"
           "&lipi=urn%3Ali&midToken=AQ&midSig=3b&trk=eml")
    assert search_params(url) == {"keywords": "nurse OR care assistant", "distance": "50", "geoId": "987654321"}
    assert search_params(url.replace("distance=49%2E7", "distance=50")) == search_params(url)
    assert search_params("https://x/?keywords=+IT+support&geoId=1")["keywords"] == "IT support"


def test_query_since_last_read(tmp_path):
    from jobhunt.alerts import LAST_READ_KEY, OVERLAP_SECONDS, query
    from jobhunt.db import connect, set_meta
    conn = connect(tmp_path / "t.db")
    assert query(conn, now=1_000_000)["query"].endswith("newer_than:1d")  # no import yet
    set_meta(conn, LAST_READ_KEY, "1000000")
    q = query(conn, now=1_000_000 + 30 * 3600)  # next run 30 h later
    assert q["query"] == f"from:jobalerts-noreply@linkedin.com after:{1_000_000 - OVERLAP_SECONDS}"
    assert q["days"] == 2
    assert query(conn, days=3)["query"].endswith("newer_than:3d")  # explicit override


def test_gmail_url_opens_the_job_search_account(example_settings):
    from dataclasses import replace

    from jobhunt import settings
    from jobhunt.alerts import gmail_url
    assert gmail_url() == "https://mail.google.com/mail/u/jobs.example@gmail.com/"
    assert gmail_url("from:a b").endswith("/u/jobs.example@gmail.com/#search/from%3Aa%20b")
    settings.use(replace(example_settings, job_email=""))
    assert gmail_url("x") == "https://mail.google.com/mail/u/0/#search/x"


def test_window_days():
    from jobhunt.alerts import window_days
    assert window_days(0, 3600) == 1
    assert window_days(0, 86400) == 2  # a full day plus the overlap
    assert window_days(0, 3 * 86400 - 7200) == 3


def test_filter_words_in_alert_keywords_become_filters():
    from jobhunt.alerts import search_params
    p = search_params("https://www.linkedin.com/comm/jobs/search-results/?keywords=%22junior+python%22+OR+%22junior+react%22"
                      "+Internship+or+Entry+level+Remote&geoId=100506914&trk=x")
    assert p["keywords"] == '"junior python" OR "junior react"' and p["f_WT"] == "2" and p["f_E"] == "1,2"
    assert search_params("https://www.linkedin.com/comm/jobs/search?keywords=python&geoId=90009828") == {
        "keywords": "python", "geoId": "90009828"}


def test_europe_wide_alert_search_is_not_expanded():
    """A Europe-wide 'Remote' alert's see-all list is over a thousand jobs, all filtered: it is not searched."""
    from jobhunt.alerts import expand_searches, wide_area

    class NoHttp:
        def get(self, *a, **k):
            raise AssertionError("must not search")

    url = ("https://www.linkedin.com/comm/jobs/search?keywords=%22junior+python%22+Internship+or+Entry+level+Remote"
           "&geoId=100506914")
    jobs, notes = expand_searches(NoHttp(), [{"url": url, "alert": "junior python"}], days=1)
    assert jobs == [] and notes and "Europe" in notes[0]
    # A region named by location text (settings.region_names) counts too; a country does not.
    assert wide_area({"keywords": "python", "location": "EMEA"}) == "EMEA"
    assert wide_area({"keywords": "python", "location": "Poland"}) is None
