"""Shared HTTP client for scrapers: real browser UA, polite delay, retries. Plus the city spelling the country-only
boards expect."""

import time
import unicodedata

import httpx

from .. import settings
from ..settings import LANGUAGE_ALIASES

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def accept_language() -> str:
    """English first, then the country's language when its ISO code doubles as a language code ("PL" -> pl)."""
    code = settings.get().country_code.lower()
    return "en-GB,en;q=0.9" + (f",{code};q=0.8" if code in LANGUAGE_ALIASES and code != "en" else "")


# A 429 with a Retry-After above this is a block for hours, not a pause: one ATS once answered Retry-After ~13 h to
# every `jobhunt resolve` probe, and four capped 60 s waits per company slug turned a 2-minute run into ~3 hours.
# Such a host gets no more requests in this run.
BLOCK_AFTER = 600
# A slowed-down host gets 0.25 s of its delay back after this many answers in a row without a 429: otherwise one early
# burst of 429s keeps LinkedIn at the 3 s cap for thousands of requests.
RECOVER_AFTER = 100


class HostBlocked(httpx.HTTPError):
    """The host answered 429 with a Retry-After above BLOCK_AFTER; nothing more is sent to it in this run."""


class Http:
    def __init__(self, delay: float = 0.6, timeout: float = 20.0):
        self.delay = delay  # base gap between two requests to the same host
        # Delay, last request and 429-free streak per host: one Http serves the whole scrape, and a single shared delay
        # let LinkedIn's slowdown slow the ATS boards and the enrich step after it too.
        self._delays: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._ok: dict[str, int] = {}
        self.blocked: dict[str, int] = {}  # host -> Retry-After seconds it sent
        self.refused = 0  # requests not sent (or given up) because their host is blocked
        # Seconds slept on 429 answers, reported by long runs: otherwise a slow import cannot be explained afterwards.
        self.throttled = 0
        self.throttle_wait = 0.0
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": accept_language()},
            timeout=timeout,
            follow_redirects=True,
        )

    def delay_for(self, host: str) -> float:
        return self._delays.get(host, self.delay)

    def _wait(self, host: str) -> None:
        gap = time.monotonic() - self._last.get(host, 0.0)
        delay = self.delay_for(host)
        if gap < delay:
            time.sleep(delay - gap)
        self._last[host] = time.monotonic()

    def _blocked(self, host: str) -> HostBlocked:
        self.refused += 1
        return HostBlocked(f"{host} blocked us: 429 with Retry-After {self.blocked[host]} s")

    def _answered(self, host: str) -> None:
        if host not in self._delays:
            return
        self._ok[host] = self._ok.get(host, 0) + 1
        if self._ok[host] >= RECOVER_AFTER:
            self._ok[host] = 0
            self._delays[host] = max(self.delay, self._delays[host] - 0.25)

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        # 429 (rate limit) gets long waits: LinkedIn guest search still answers 429 after 3 quick retries.
        # Retry-After is honored when the server sends it, capped at 60 s: an uncapped value once put a run to sleep
        # for 16 hours.
        host = httpx.URL(url).host
        if host in self.blocked:
            raise self._blocked(host)
        for attempt in range(5):
            self._wait(host)
            try:
                resp = self.client.request(method, url, **kwargs)
            except httpx.TransportError:
                # A read timeout or dropped connection on one page of a long scan: without a retry it ended the whole
                # LinkedIn source with 0 jobs.
                if attempt >= 2:
                    raise
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code == 429:
                wait = resp.headers.get("Retry-After", "")
                if wait.isdigit() and int(wait) > BLOCK_AFTER:
                    self.blocked[host] = int(wait)
                    raise self._blocked(host)
            if resp.status_code == 429 and attempt < 4:
                pause = min(float(wait), 60.0) if wait.isdigit() else 15 * (attempt + 1)
                self.throttled += 1
                self.throttle_wait += pause
                # Slow this host down for the rest of the run: at 0.8 s LinkedIn answered 429 to about 1 in 9 requests.
                self._delays[host] = min(self.delay_for(host) * 1.5 + 0.2, 3.0)
                self._ok[host] = 0
                time.sleep(pause)
                continue
            if resp.status_code in (502, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            self._answered(host)
            return resp
        raise RuntimeError("unreachable")

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)


# Country-only boards (COUNTRY_SOURCES) search by the city's local spelling, which settings may only list among
# city_names. English/ASCII spellings of Polish cities -> the Polish name the Polish boards use.
PL_CITY_NAMES = {"warsaw": "warszawa", "cracow": "kraków", "krakow": "kraków", "wroclaw": "wrocław",
                 "gdansk": "gdańsk", "poznan": "poznań", "lodz": "łódź", "bialystok": "białystok",
                 "rzeszow": "rzeszów", "torun": "toruń", "czestochowa": "częstochowa",
                 "bielsko-biala": "bielsko-biała"}


def pl_city() -> str:
    """The configured city as Polish boards spell it, lower-case: 'Warsaw' -> 'warszawa', 'Krakow' -> 'kraków'."""
    s = settings.get()
    names = [n.lower() for n in (*s.city_names, s.city) if n]
    native = [n for n in names if n in PL_CITY_NAMES.values()]
    if native:
        return native[0]
    return PL_CITY_NAMES.get(names[0], names[0]) if names else ""


def slug(text: str) -> str:
    """URL slug of a place name: 'Kraków' -> 'krakow', 'Łódź' -> 'lodz', 'Bielsko-Biała' -> 'bielsko-biala'."""
    folded = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(c for c in folded if not unicodedata.combining(c)).replace(" ", "-")
