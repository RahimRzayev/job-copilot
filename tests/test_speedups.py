"""Four no-loss speed-ups: LinkedIn beside the other sources, parallel enrich per host, the exact see-all window of
the alert import, and the history index. Each test checks that the parallel/cached path gives
exactly what the old one-after-another path gave, only faster."""
import threading
import time
from datetime import datetime, timezone

from jobhunt import alerts, history, pipeline
from jobhunt.db import connect, set_meta
from jobhunt.normalize import Job
from jobhunt.sources.base import Http


def _job(source: str, sid: str, **kw) -> Job:
    base = dict(source=source, source_id=sid, title=f"Junior Python Developer {sid}", company=f"Company {sid}",
                url=f"https://example.com/{source}/{sid}", location="Warszawa",
                posted_at=datetime.now(timezone.utc).isoformat())
    base.update(kw)
    return Job(**base)


# --- 1. LinkedIn in its own thread ---

def test_linkedin_runs_beside_other_sources_and_keeps_source_order(monkeypatch):
    seen_http = {}

    def slow(name, jobs, fail=False):
        def fetch(http, **kw):
            seen_http[name] = http
            time.sleep(0.3)
            if fail:
                raise RuntimeError("board down")
            if name == "linkedin":
                http.throttled += 2  # its fork's 429 count must reach the scrape report
            return jobs
        return fetch

    monkeypatch.setattr(pipeline.linkedin, "STATS", [])  # another test's LinkedIn search stats
    monkeypatch.setattr(pipeline.justjoin, "fetch", slow("justjoin", [_job("justjoin", "a")]))
    monkeypatch.setattr(pipeline.linkedin, "fetch", slow("linkedin", [_job("linkedin", "l1"), _job("linkedin", "l2")]))
    monkeypatch.setattr(pipeline.nofluffjobs, "fetch", slow("nofluffjobs", [], fail=True))
    monkeypatch.setattr(pipeline.bulldogjob, "fetch", slow("bulldogjob", [_job("bulldogjob", "b")]))
    http = Http()
    report = pipeline.Report()
    started = time.monotonic()
    jobs = pipeline.fetch_all(http, ("justjoin", "linkedin", "nofluffjobs", "bulldogjob"), 24, report)
    elapsed = time.monotonic() - started

    assert elapsed < 1.1  # one after another: 1.2 s
    assert [j.source_id for j in jobs] == ["a", "l1", "l2", "b"]  # same order as before
    assert report.errors == ["nofluffjobs: board down"]
    assert [k for k in report.fetched if k != "minutes by step"] == ["justjoin", "linkedin", "nofluffjobs", "bulldogjob"]
    assert report.fetched["linkedin"] == 2
    assert "all sources (LinkedIn in parallel)" in report.fetched["minutes by step"]
    assert seen_http["linkedin"] is not http and seen_http["justjoin"] is http
    assert http.throttled == 2


def test_linkedin_alone_runs_on_the_shared_client(monkeypatch):
    got = []
    monkeypatch.setattr(pipeline.linkedin, "fetch", lambda http, **kw: got.append(http) or [])
    http = Http()
    report = pipeline.Report()
    pipeline.fetch_all(http, ("linkedin",), 24, report)
    assert got == [http] and list(report.fetched["minutes by step"]) == ["linkedin"]


def test_fork_keeps_host_state_and_absorb_brings_it_back():
    http = Http()
    http._delays["www.linkedin.com"] = 3.0
    http.blocked["apply.workable.com"] = 50000
    fork = http.fork()
    assert fork.client is not http.client
    assert fork.delay_for("www.linkedin.com") == 3.0 and "apply.workable.com" in fork.blocked
    fork._delays["www.linkedin.com"] = 1.25
    fork._last["www.linkedin.com"] = time.monotonic()
    fork.blocked["boards-api.greenhouse.io"] = 9000
    fork.throttled, fork.throttle_wait, fork.refused = 3, 45.0, 1
    http.absorb(fork)
    assert http.delay_for("www.linkedin.com") == 1.25
    assert set(http.blocked) == {"apply.workable.com", "boards-api.greenhouse.io"}
    assert (http.throttled, http.throttle_wait, http.refused) == (3, 45.0, 1)
    assert fork.client.is_closed


# --- 4. parallel enrich, one thread per host ---

def _batch() -> list[Job]:
    """Jobs of four hosts, with a same-batch duplicate, a too-many-years description and a failing detail page."""
    jobs = [_job("linkedin", f"li{i}") for i in range(4)]
    jobs += [_job("justjoin", "jj1"), _job("justjoin", "jj2", title="Junior Python Developer li1", company="Company li1",
                                              location="Kraków", remote=True)]
    jobs += [_job("workday", "wd1", employer_url="https://acme.wd3.myworkdayjobs.com/ext/job/1"),
             _job("workday", "wd2", employer_url="https://globex.wd5.myworkdayjobs.com/ext/job/2")]
    return jobs


def _fake_enrichers(monkeypatch, calls: list):
    lock = threading.Lock()

    def enrich(http, job):
        time.sleep(0.05)
        with lock:
            calls.append((pipeline._enrich_host(job), job.source_id, threading.get_ident()))
        if job.source_id == "li2":
            raise RuntimeError("404")
        job.description = ("Minimum 5 years of experience required." if job.source_id == "wd2"
                           else f"Junior Python role {job.source_id}, English.")
        return job

    for source in ("linkedin", "justjoin", "workday"):
        monkeypatch.setitem(pipeline.ENRICHERS, source, enrich)


def _run_process(tmp_path, monkeypatch, workers: int, name: str):
    calls: list = []
    _fake_enrichers(monkeypatch, calls)
    monkeypatch.setattr(pipeline, "ENRICH_WORKERS", workers)
    conn = connect(tmp_path / f"{name}.db")
    report = pipeline.Report()
    started = time.monotonic()
    pipeline.process(conn, Http(), _batch(), 24, 3, report)
    elapsed = time.monotonic() - started
    rows = [tuple(r) for r in conn.execute(
        "SELECT source, source_id, status, filter_reason, description FROM jobs ORDER BY id")]
    return rows, report.to_dict(), calls, elapsed


def test_parallel_enrich_stores_exactly_what_one_by_one_stored(tmp_path, monkeypatch):
    seq_rows, seq_report, seq_calls, seq_time = _run_process(tmp_path, monkeypatch, 1, "seq")
    par_rows, par_report, par_calls, par_time = _run_process(tmp_path, monkeypatch, 8, "par")

    assert par_rows == seq_rows
    assert par_report == seq_report
    assert par_report["errors"] == ["enrich linkedin li2: 404"]
    assert par_report["fetched"]["enriched"] == 8
    assert {r[3] for r in par_rows} >= {"5+ years required", "duplicate (same batch)"}
    # Every host got the same requests in the same order, one thread per host.
    per_host = lambda calls: {h: [sid for hh, sid, _ in calls if hh == h] for h, _, _ in calls}
    assert per_host(par_calls) == per_host(seq_calls)
    assert len({t for h, _, t in par_calls if h == "linkedin"}) == 1
    assert len({t for _, _, t in seq_calls}) == 1 and len({t for _, _, t in par_calls}) > 1
    assert par_time < seq_time


def test_enrich_limit_takes_the_same_jobs(tmp_path, monkeypatch):
    calls: list = []
    _fake_enrichers(monkeypatch, calls)
    conn = connect(tmp_path / "t.db")
    report = pipeline.Report()
    pipeline.process(conn, Http(), _batch(), 24, 3, report, enrich_limit=3)
    assert report.fetched["enriched"] == 3 and len(calls) == 3


def test_workday_tenants_are_separate_hosts():
    a, b = _batch()[-2:]
    assert pipeline._enrich_host(a) != pipeline._enrich_host(b)
    assert pipeline._enrich_host(_job("workday", "x")) == "workday"


# --- 2. exact see-all window ---

def test_search_window_counts_seconds_since_the_last_import():
    now = 1_800_000_000.0
    last = now - 27 * 3600
    since = last - alerts.SEARCH_OVERLAP_SECONDS
    assert alerts.search_window(2, since, now) == f"r{30 * 3600}"  # whole days: r172800 (48 h)
    assert alerts.search_window(2, None, now) == "r172800"  # explicit --days / first import: whole days
    assert alerts.search_window(1, now - 60, now) == "r3600"  # never below an hour


def test_import_searches_from_the_last_read_minus_overlap(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    read_at = time.time()
    path = tmp_path / "alerts.json"
    path.write_text('{"jobs": [], "searches": [{"url": "https://www.linkedin.com/comm/jobs/search?keywords=python'
                    '&geoId=105072130", "alert": "python"}]}')
    set_meta(conn, alerts.LAST_READ_KEY, str(int(read_at - 27 * 3600)))
    windows = []

    def page(http, params):
        windows.append(params["f_TPR"])
        return ""

    monkeypatch.setattr(alerts.linkedin, "search_page", page)
    out = alerts.run(conn, path)
    secs = int(windows[0][1:])
    assert 30 * 3600 <= secs <= 30 * 3600 + 60  # 27 h gap + 3 h overlap, not 2 days
    assert out["days"] == 2  # the posting-age window stays in whole days (+ EXTRA_HOURS)
    assert out["pipeline"]["fetched"]["see-all window hours"] == 30.0


# --- 3. history index ---

def test_history_index_gives_the_same_hits(tmp_path):
    conn = connect(tmp_path / "t.db")
    history.import_rows(conn, [
        {"thread_id": "1", "date": "2025-03-01", "sender_name": "Initech Careers", "sender_email": "jobs@initech.com",
         "subject": "Your application for Junior Data Analyst", "snippet": "Thank you for applying to Initech"},
        {"thread_id": "2", "date": "2025-04-01", "sender_name": "Initech", "sender_email": "no-reply@smartrecruiters.com",
         "subject": "Initech: update on your application", "snippet": "for the position of QA Engineer we regret"},
        {"thread_id": "3", "date": "2025-05-01", "sender_name": "Globex", "sender_email": "hr@globex.com",
         "subject": "Interview invitation", "snippet": "Globex would like to invite you"},
    ])
    index = history.HistoryIndex(conn)
    for company, title in [("Initech Sp. z o.o.", "Junior Data Analyst"), ("Initech Poland", "QA Engineer"),
                           ("Initech", ""), ("Globex", "Data Analyst"), ("Hooli", "Developer"), ("AB", "x")]:
        assert history.for_company(conn, company, title, index) == history.for_company(conn, company, title), company
    assert set(index.by_core) == {"initech", "globex", "hooli"}  # one company scan per core, shared by its jobs
    hits = history.for_company(conn, "Initech", "Junior Data Analyst", index)
    assert [h["same_role"] for h in hits] == [False, True] and hits[0]["names_other_role"]
