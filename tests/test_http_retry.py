import httpx
import pytest

from jobhunt.sources import base


def test_retry_after_is_capped(monkeypatch):
    """A 429 with a huge Retry-After must not put a run to sleep for hours."""
    slept = []
    monkeypatch.setattr(base.time, "sleep", lambda s: slept.append(s))
    calls = iter([httpx.Response(429, headers={"Retry-After": "86400"}), httpx.Response(200, text="ok")])
    http = base.Http(delay=0)
    http.client = httpx.Client(transport=httpx.MockTransport(lambda request: next(calls)))
    assert http.get("https://example.com").text == "ok"
    assert max(slept) <= 60


def test_read_timeouts_are_retried(monkeypatch):
    """One slow page must not end a whole source (a LinkedIn scan lost every job to one read timeout)."""
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, text="ok")

    http = base.Http(delay=0)
    http.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert http.get("https://example.com").text == "ok" and len(calls) == 3


def test_a_dead_host_still_fails_after_the_retries(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)

    def handler(request):
        raise httpx.ConnectError("down", request=request)

    http = base.Http(delay=0)
    http.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ConnectError):
        http.get("https://example.com")
