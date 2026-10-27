"""Common job shape shared by every source, plus dedupe and text helpers."""

import hashlib
import html
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

from . import settings


@dataclass
class Job:
    source: str
    source_id: str
    title: str
    company: str
    url: str
    location: str = ""
    remote: bool = False
    seniority: str = ""            # intern | junior | mid | senior | "" (unknown)
    apply_url: str = ""
    description: str = ""
    skills: list[str] = field(default_factory=list)
    salary: str = ""
    posted_at: str = ""            # ISO 8601 UTC, "" if the source doesn't say
    languages: list[str] = field(default_factory=list)  # required spoken languages, e.g. ["German:C1"]
    category: str = ""
    employer_url: str = ""         # employer's own ATS posting, when the board exposes it
    expires_at: str = ""           # ISO 8601, "" if unknown

    @property
    def dedupe_key(self) -> str:
        # Remote jobs are often posted once per city (one ad x 13 cities): one job.
        return dedupe_key(self.company, self.title, "remote" if self.remote else self.location)

    def to_dict(self) -> dict:
        return asdict(self)


_LEGAL_SUFFIXES = r"sp\.?\s*z\s*o\.?\s*o\.?|s\.?a\.?|spółka.*|ltd\.?|llc|inc\.?|gmbh|group|grupa"


@lru_cache(maxsize=8)
def _suffixes(country_names: tuple[str, ...]) -> re.Pattern:
    names = "|".join(re.escape(n) for n in sorted({_fold(n) for n in country_names} - {""}, key=len, reverse=True))
    return re.compile(rf"\b({_LEGAL_SUFFIXES}{'|' + names if names else ''})\b")


class _CompanySuffixes:
    """Legal-form words plus the user's country names ("Acme <Country>" is Acme); used like a compiled regex."""

    def __getattr__(self, name):
        return getattr(_suffixes(settings.get().country_names), name)


_COMPANY_SUFFIXES = _CompanySuffixes()


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("ł", "l")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def dedupe_key(company: str, title: str, location: str) -> str:
    """Same company + title + city on two boards should collapse into one job."""
    company = _COMPANY_SUFFIXES.sub(" ", _fold(company))
    title = re.sub(r"\b(m|k|f|x|d)\b", " ", _fold(title))  # "(m/k)", "(f/m/d)"
    city = _city_key(_fold(location).split(" ")[0]) if location else ""
    raw = "|".join(" ".join(p.split()) for p in (company, title, city))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# Spellings of one city that should dedupe together (the user's own city comes from the settings).
_EXONYMS = {"cracow": "krakow", "munich": "munchen", "cologne": "koln", "prague": "praha", "vienna": "wien",
            "lisbon": "lisboa", "rome": "roma", "milan": "milano", "copenhagen": "kobenhavn", "brussels": "bruxelles"}


def _city_key(word: str) -> str:
    """One spelling per city: every spelling of the user's city becomes its configured name."""
    s = settings.get()
    if s.city and word in {_fold(n).split(" ")[0] for n in s.city_names}:
        return _fold(s.city).split(" ")[0]
    return _EXONYMS.get(word, word)


_LEGAL_WORDS = {"sp", "z", "o", "oo", "s", "a", "sa", "spolka", "ograniczona", "odpowiedzialnoscia", "ltd", "llc",
                "inc", "gmbh", "spk", "k", "sk", "co", "plc", "ag", "bv", "nv", "group", "grupa", "the", "and", "of"}
# Words that never identify a company alone ("Data" must not equal "Data Art").
_GENERIC_WORDS = {"data", "digital", "software", "tech", "technology", "technologies", "solutions", "systems",
                  "consulting", "services", "it", "global", "international", "labs", "group", "europe", "bank"}


@lru_cache(maxsize=8)
def _country_words(country_names: tuple[str, ...]) -> frozenset[str]:
    """One-word country names: "Acme <Country>" is the local branch of Acme."""
    return frozenset(w for w in map(_fold, country_names) if w and " " not in w)


def company_words(company: str) -> list[str]:
    skip = _LEGAL_WORDS | _country_words(settings.get().country_names)
    return [w for w in _fold(company).split() if w not in skip]


def title_key(title: str) -> str:
    return " ".join(re.sub(r"\b(m|k|f|x|d|n)\b", " ", _fold(title)).split())


def company_variant(a: str, b: str) -> bool:
    """Same employer under two spellings: 'Initech Holding Sp. z o.o.' on one board vs 'Initech' on another, 'Globex
    Northwind Corporation' vs 'GNC'. Without this, a second board's copy of an already-applied role reached scoring."""
    wa, wb = company_words(a), company_words(b)
    if not wa or not wb:
        return False
    if wa == wb:
        return True
    short, long_ = sorted((wa, wb), key=len)
    if set(short) <= set(long_) and not set(short) <= _GENERIC_WORDS and max(len(w) for w in short) >= 3:
        return True
    acronym = "".join(w[0] for w in long_)
    return len(short) == 1 and len(long_) >= 2 and short[0] == acronym


def role_key(company: str, title: str) -> str:
    """Company + title, ignoring location: one role posted for several locations (a country-wide copy and a city
    copy of the same ad) is one application."""
    company = " ".join(_COMPANY_SUFFIXES.sub(" ", _fold(company)).split())
    title = " ".join(re.sub(r"\b(m|k|f|x|d)\b", " ", _fold(title)).split())
    return f"{company}|{title}"


def html_to_text(markup: str) -> str:
    if not markup:
        return ""
    text = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", markup, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def ms_to_iso(ms: int | float | None) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)  # .NET 7-digit fractions
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
