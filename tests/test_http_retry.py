import httpx

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
