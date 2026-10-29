"""Rate limits: a host that blocks for hours fails fast, each host keeps its own delay (and gets it back), a LinkedIn
page with a lasting 429 waits longer instead of ending the source, and resolve retries jobs a blocked host skipped."""
import httpx
import pytest

from jobhunt import resolve
from jobhunt.db import connect
from jobhunt.sources import base, linkedin


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "t.db")


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(base.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(linkedin.time, "sleep", lambda s: slept.append(s))
    return slept


def mock_http(handler) -> base.Http:
    http = base.Http(delay=0)
    http.client = httpx.Client(transport=httpx.MockTransport(handler))
    return http


def test_blocked_host_gets_no_more_requests_other_hosts_still_do(no_sleep):
    sent = []

    def handler(request):
        sent.append(request.url.host)
        if request.url.host == "apply.workable.com":
            return httpx.Response(429, headers={"Retry-After": "47989"})
        return httpx.Response(200, text="ok")
    http = mock_http(handler)
    for _ in range(3):
        with pytest.raises(base.HostBlocked):
            http.post("https://apply.workable.com/api/v3/accounts/x/jobs")
    assert http.get("https://api.ashbyhq.com/posting-api/job-board/x").text == "ok"
    assert sent == ["apply.workable.com", "api.ashbyhq.com"] and http.refused == 3 and not no_sleep


def test_probe_skips_a_blocked_ats_for_every_later_slug(no_sleep):
    sent = []

    def handler(request):
        sent.append(request.url.host)
        if request.url.host == "apply.workable.com":
            return httpx.Response(429, headers={"Retry-After": "47989"})
        return httpx.Response(404)
    http = mock_http(handler)
    company = "Northwind Research Institute Poland"
    assert len(resolve.slugs(company)) > 1
    assert resolve._probe(http, company, "Junior Developer") is None
    assert sent.count("apply.workable.com") == 1 and not no_sleep


def test_429_slows_only_its_own_host(no_sleep):
    answers = iter([httpx.Response(429), httpx.Response(200)])
    http = mock_http(lambda request: next(answers) if request.url.host == "www.linkedin.com" else httpx.Response(200))
    http.delay = 0.6
    http.get("https://www.linkedin.com/jobs")
    http.get("https://boards-api.greenhouse.io/v1/boards/x/jobs")
    assert http.delay_for("www.linkedin.com") > 0.6
    assert http.delay_for("boards-api.greenhouse.io") == 0.6


def test_slowed_host_gets_its_delay_back_after_a_429_free_streak(no_sleep):
    http = mock_http(lambda request: httpx.Response(200))
    http.delay = 0.6
    http._delays["www.linkedin.com"] = 3.0
    for _ in range(base.RECOVER_AFTER * 2):
        http.get("https://www.linkedin.com/jobs")
    assert http.delay_for("www.linkedin.com") == pytest.approx(2.5)


def test_linkedin_page_waits_longer_on_a_lasting_429_instead_of_failing(no_sleep):
    class Http:
        calls = 0

        def get(self, url, params=None):
            self.calls += 1
            if self.calls <= 2:
                request = httpx.Request("GET", url)
                raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))

            class R:
                text = "cards"
            return R()
    assert linkedin.search_page(Http(), {"start": 0}) == "cards"
    assert no_sleep == list(linkedin.PAGE_PAUSES)


def test_resolve_retries_jobs_a_blocked_host_could_not_check(conn, monkeypatch):
    conn.execute("INSERT INTO jobs (dedupe_key, source, title, company, url, scraped_at, status, score) "
                 "VALUES ('k', 'linkedin', 'Data Analyst', 'Nowhere Ltd', 'https://x', '2026-01-01', 'scored', 70)")
    conn.commit()

    def blocked_probe(http, company, title):
        http.blocked["apply.workable.com"] = 47989
        http.refused += 1
        return None
    monkeypatch.setattr(resolve, "_watchlist", lambda http, c, t: None)
    monkeypatch.setattr(resolve, "_probe", blocked_probe)
    out = resolve.resolve(conn)
    assert out["unresolved"][0]["retry_next_run"] is True
    assert out["blocked_hosts"] == {"apply.workable.com": "429, Retry-After 47989 s"}
    assert conn.execute("SELECT resolve_tried_at FROM jobs").fetchone()[0] is None
    assert resolve.resolve(conn)["tried"] == 1  # tried again next run, not after RETRY_DAYS


def test_workable_probe_uses_widget_feed_not_the_v3_search(monkeypatch):
    """The v3 search can block an IP for hours while the widget feed keeps answering."""
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    sent = []

    def handler(request):
        sent.append(request.url.path)
        if "/api/v3/" in request.url.path:
            return httpx.Response(429, headers={"Retry-After": "17530"})
        if request.url.path == "/api/v1/widget/accounts/northwind":
            return httpx.Response(200, json={"jobs": [
                {"title": "Go Engineer (remote work)", "shortcode": "AAA111", "country": "Poland"},
                {"title": "Graduate Engineer (remote work)", "shortcode": "BBB222", "country": "Poland"}]})
        return httpx.Response(404)
    http = mock_http(handler)
    assert resolve._probe(http, "Northwind", "Graduate Engineer (remote work)") == (
        "https://apply.workable.com/northwind/j/BBB222/", "workable")
    assert not any("/api/v3/" in p for p in sent)
