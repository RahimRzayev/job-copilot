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


class Http:
    def __init__(self, delay: float = 0.6, timeout: float = 20.0):
        self.delay = delay
        self._last = 0.0
        # Seconds slept on 429 answers, reported by long runs: otherwise a slow import cannot be explained afterwards.
        self.throttled = 0
        self.throttle_wait = 0.0
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": accept_language()},
            timeout=timeout,
            follow_redirects=True,
        )

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        # 429 (rate limit) gets long waits: LinkedIn guest search still answers 429 after 3 quick retries.
        # Retry-After is honored when the server sends it, capped at 60 s: an uncapped value once put a run to sleep
        # for 16 hours.
        for attempt in range(5):
            self._wait()
            resp = self.client.request(method, url, **kwargs)
            if resp.status_code == 429 and attempt < 4:
                wait = resp.headers.get("Retry-After", "")
                pause = min(float(wait), 60.0) if wait.isdigit() else 15 * (attempt + 1)
                self.throttled += 1
                self.throttle_wait += pause
                # Slow down for the rest of the run: at 0.8 s LinkedIn answered 429 to about 1 in 9 requests.
                self.delay = min(self.delay * 1.5 + 0.2, 3.0)
                time.sleep(pause)
                continue
            if resp.status_code in (502, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
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
