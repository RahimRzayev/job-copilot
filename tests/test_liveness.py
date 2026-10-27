import httpx

from jobhunt import liveness
from jobhunt.liveness import CLOSED, OPEN, UNKNOWN
from jobhunt.sources.base import Http


def fake_http(routes: dict) -> Http:
    """routes: substring of URL -> (status, json-or-text)."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, (status, body) in routes.items():
            if key in url:
                if isinstance(body, (dict, list)):
                    return httpx.Response(status, json=body)
                return httpx.Response(status, text=body)
        return httpx.Response(599, text="unrouted " + url)
    http = Http(delay=0)
    http.client = httpx.Client(transport=httpx.MockTransport(handler))
    return http


def test_smartrecruiters_active_flag():
    http = fake_http({"postings/744000000000111": (200, {"active": False}), "postings/744000000000222": (200, {"active": True})})
    assert liveness.check_employer(http, "https://jobs.smartrecruiters.com/Acme1/744000000000111-associate")[0] == CLOSED
    assert liveness.check_employer(http, "https://jobs.smartrecruiters.com/Globex/744000000000222")[0] == OPEN


def test_workday_url_to_api_and_status():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        code = 404 if "R0001" in str(request.url) else 403 if "R0003" in str(request.url) else 200
        return httpx.Response(code, json={"jobPostingInfo": {"canApply": True}})
    http = Http(delay=0)
    http.client = httpx.Client(transport=httpx.MockTransport(handler))
    url = "https://initech.wd103.myworkdayjobs.com/en-US/InitechCareers/job/Warsaw/Some-Role_R0001/apply"
    assert liveness.check_employer(http, url)[0] == CLOSED
    assert seen[0] == "https://initech.wd103.myworkdayjobs.com/wday/cxs/initech/InitechCareers/job/Warsaw/Some-Role_R0001"
    assert liveness.check_employer(http, url.replace("R0001", "R0002"))[0] == OPEN
    assert liveness.check_employer(http, url.replace("R0001", "R0003"))[0] == UNKNOWN  # blocked, not removed


def test_greenhouse_lever_404():
    http = fake_http({"boards-api.greenhouse.io": (404, {}), "api.lever.co": (404, {})})
    assert liveness.check_employer(http, "https://job-boards.greenhouse.io/globex/jobs/123")[0] == CLOSED
    assert liveness.check_employer(http, "https://jobs.lever.co/northwind/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b")[0] == CLOSED


def test_generic_page_text():
    http = fake_http({"peopleforce": (200, "<h1>Closed vacancy</h1>"), "darwinbox": (200, "<app-root></app-root>")})
    assert liveness.check_employer(http, "https://contoso.peopleforce.io/careers/v/1")[0] == CLOSED
    assert liveness.check_employer(http, "https://umbrella.darwinbox.com/jobs/1")[0] == UNKNOWN


def test_board_then_employer():
    http = fake_http({"justjoin.it/api": (200, {"isActive": True, "applyUrl": "https://jobs.smartrecruiters.com/X/744000000000999"}),
                      "postings/744000000000999": (200, {"active": False})})
    job = {"source": "justjoin", "source_id": "x", "url": "", "apply_url": "", "employer_url": None}
    state, detail, employer = liveness.check_job(http, job)
    assert state == CLOSED and "smartrecruiters" in detail and employer.endswith("/744000000000999")


def test_expired_date_closes_without_requests():
    http = fake_http({})
    job = {"source": "pracuj", "source_id": "1", "url": "", "expires_at": "2020-01-01T00:00:00Z"}
    assert liveness.check_job(http, job)[0] == CLOSED


def test_board_closed_markers():
    http = fake_http({"nofluffjobs.com/api/posting/a": (200, {"status": "EXPIRED"}),
                      "jobPosting/5": (200, "<span>No longer accepting applications</span>")})
    assert liveness.check_board(http, {"source": "nofluffjobs", "source_id": "a"})[0] == CLOSED
    assert liveness.check_board(http, {"source": "linkedin", "source_id": "5"})[0] == CLOSED


def test_employer_text_and_flags():
    """The employer's own posting can ask 3+ years when the board copy does not say so."""
    desc = "<p>Requirements</p><ul><li>Minimum of 3 years of experience in application support</li>" + "<li>Java</li>" * 60 + "</ul>"
    http = fake_http({"wday/cxs/acme/Careers/job/Warsaw/Support_R1": (200, {"jobPostingInfo": {"jobDescription": desc}}),
                      "boards-api.greenhouse.io/v1/boards/globex/jobs/9": (200, {"content": "&lt;p&gt;Fluent Polish required&lt;/p&gt;" + " team" * 80})})
    text = liveness.employer_text(http, "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/Warsaw/Support_R1/apply")
    assert "3 years" in text and "<li>" not in text
    assert liveness.employer_flags(text) == ["3+ years"]
    assert liveness.employer_flags("Has 2–5&#43; years of experience in technical operations. " + "team " * 60) == []  # raw text
    two = liveness._html_text("<li>Has 2–5&#43; years of experience in technical operations</li>" + "<p>team</p>" * 60)
    assert liveness.employer_flags(two) == []  # below settings max_years (3)
    assert liveness.employer_flags(two, warn_years=2) == ["2+ years"]
    gh = liveness.employer_text(http, "https://job-boards.greenhouse.io/embed/job_app?for=globex&token=9")
    assert "<p>" not in gh and "Polish required" in liveness.employer_flags(gh)
    assert liveness.employer_flags("short") == []


def test_polish_is_no_blocker_for_a_polish_speaker(example_settings):
    from dataclasses import replace

    from jobhunt import settings
    text = "Fluent Polish required. " + "team " * 80
    assert "Polish required" in liveness.employer_flags(text)
    settings.use(replace(example_settings, languages=("english", "polish")))
    assert liveness.employer_flags(text) == []


def test_linkedin_easy_apply_detected():
    """Easy Apply-only jobs must be recognised before they reach the shortlist."""
    http = fake_http({"jobPosting/7": (200, '<button data-tracking-control-name="public_jobs_apply-link-onsite">Apply</button>'),
                      "jobPosting/8": (200, '<a data-tracking-control-name="public_jobs_apply-link-offsite">Apply</a>')})
    state, detail, _ = liveness.check_board(http, {"source": "linkedin", "source_id": "7"})
    assert state == "open" and liveness.EASY_APPLY in detail
    assert "employer site" in liveness.check_board(http, {"source": "linkedin", "source_id": "8"})[1]


def test_linkedin_missing_apply_button_is_reported(monkeypatch):
    """A page that comes back without an Apply button on every retry is reported, not passed as plain "accepting"
    (it can be Easy Apply only)."""
    monkeypatch.setattr(liveness.time, "sleep", lambda s: None)
    http = fake_http({"jobPosting/9": (200, "<h1>AI Engineer</h1>")})
    state, detail, _ = liveness.check_board(http, {"source": "linkedin", "source_id": "9"})
    assert state == "open" and liveness.NO_APPLY_BUTTON in detail
