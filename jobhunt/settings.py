"""Search settings: where the user looks for work and what they can do.

Read from the `search:` section of profile/profile.yaml, which /setup writes from the user's answers. Nothing about a
country, city or language is hard-coded elsewhere: filters, sources and skills ask this module.

Tests install their own settings with `use(Settings(...))`.
"""

import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROFILE = Path(os.environ.get("JOBHUNT_PROFILE", ROOT / "profile" / "profile.yaml"))

# Job boards that only list jobs in one country; they are switched on automatically for that country.
COUNTRY_SOURCES = {"PL": ("justjoin", "nofluffjobs", "bulldogjob", "pracuj", "englishjobs")}
# Boards that list only IT jobs: switched on automatically only when `tech_only` is set.
TECH_BOARDS = {"justjoin", "nofluffjobs", "bulldogjob"}
# Sources that work for any country.
GLOBAL_SOURCES = ("linkedin", "company_boards")


@dataclass(frozen=True)
class Settings:
    country: str = ""                     # "Germany"
    country_code: str = ""                # ISO 3166-1 alpha-2, "DE"
    country_names: tuple[str, ...] = ()   # lower-case spellings seen in job locations: ("germany", "deutschland")
    city: str = ""                        # the one city where on-site / hybrid work is fine: "Munich"
    city_names: tuple[str, ...] = ()      # lower-case spellings: ("munich", "münchen", "monaco di baviera")
    other_cities: tuple[str, ...] = ()    # lower-case prefixes of other cities in the country ("berlin", "hamburg"):
                                          # a job there counts only when it is remote
    relocate: bool = False                # True: on-site anywhere in the country is fine
    remote: str = "country"               # "country": remote only within the country
                                          # "region":  also remote open to the whole region_names area
                                          # "worldwide": any remote job not limited to another country's residents
    region_names: tuple[str, ...] = ()    # with remote "region": lower-case names of the user's region as job
                                          # locations write it: ("europe", "emea", "eu") or ("latam", "latin america")
    languages: tuple[str, ...] = ("english",)  # languages the user can WORK in, lower-case English names
    max_years: int = 2                    # drop jobs requiring more years of experience than this
    roles: tuple[str, ...] = ()           # target roles, lower case: ("data analyst", "backend developer")
    levels: tuple[str, ...] = ()          # target levels: ("internship", "working student", "junior", "mid")
    tech_only: bool = False               # True: drop non-tech titles (sales, HR, legal...) and add IT-only boards
    exclude_titles: tuple[str, ...] = ()  # lower-case title words that always drop a job: ("sales", "recruiter")
    keywords: tuple[str, ...] = ()        # search keywords for LinkedIn / board searches, most important first
    sources: tuple[str, ...] = ()         # enabled sources; empty = GLOBAL_SOURCES + COUNTRY_SOURCES[country_code]
    currency: str = ""                    # "EUR"
    salary_monthly: int | None = None     # expected gross monthly salary, for form fields only (never a filter)
    job_email: str = ""                   # Gmail inbox used for the job search (registrations, replies)
    password_service: str = "jobhunt-default"  # OS keyring entry holding the default ATS password

    @property
    def configured(self) -> bool:
        return bool(self.country and self.city)

    @property
    def students(self) -> bool:
        """Student / working-student roles are among the user's levels."""
        return any("student" in level for level in self.levels)

    @property
    def mid(self) -> bool:
        """Mid-level roles are among the user's levels."""
        return any(level.startswith(("mid", "regular", "intermediate")) for level in self.levels)

    def speaks(self, language: str) -> bool:
        """True when the user can work in this language. Accepts English names, native names and ISO codes:
        "Polish", "polski", "pl" and "PL: C1" all mean polish."""
        return language_name(language) in {language_name(lang) for lang in self.languages}

    def enabled(self, source: str) -> bool:
        if self.sources:
            return source in self.sources
        if source in TECH_BOARDS and not self.tech_only:
            return False
        return source in GLOBAL_SOURCES or source in COUNTRY_SOURCES.get(self.country_code.upper(), ())

    def in_city(self, text: str) -> bool:
        low = (text or "").lower()
        return any(name in low for name in self.city_names or ((self.city.lower(),) if self.city else ()))

    def in_country(self, text: str) -> bool:
        low = (text or "").lower()
        names = self.country_names or ((self.country.lower(),) if self.country else ())
        return any(name in low for name in names)

    def other_city(self, text: str) -> str | None:
        low = (text or "").lower()
        return next((c for c in self.other_cities if c in low), None)


# Native names and ISO 639-1 codes job boards use for languages -> lower-case English name.
LANGUAGE_ALIASES = {
    "en": "english", "angielski": "english", "englisch": "english", "anglais": "english", "inglés": "english",
    "pl": "polish", "polski": "polish", "polnisch": "polish",
    "de": "german", "deutsch": "german", "niemiecki": "german", "allemand": "german", "alemán": "german",
    "fr": "french", "français": "french", "francais": "french", "francuski": "french", "französisch": "french",
    "es": "spanish", "español": "spanish", "espanol": "spanish", "hiszpański": "spanish", "spanisch": "spanish",
    "it": "italian", "italiano": "italian", "włoski": "italian", "nl": "dutch", "nederlands": "dutch",
    "pt": "portuguese", "português": "portuguese", "cs": "czech", "čeština": "czech", "czeski": "czech",
    "sk": "slovak", "hu": "hungarian", "ro": "romanian", "fi": "finnish", "sv": "swedish", "svenska": "swedish",
    "no": "norwegian", "da": "danish", "dansk": "danish", "uk": "ukrainian", "українська": "ukrainian",
    "ru": "russian", "русский": "russian", "tr": "turkish", "türkçe": "turkish", "az": "azerbaijani",
    "azərbaycan": "azerbaijani", "zh": "chinese", "ja": "japanese", "ko": "korean", "ar": "arabic",
    "he": "hebrew", "el": "greek", "hi": "hindi",
}


def language_name(value: str) -> str:
    """'PL: C1' -> 'polish', 'Deutsch' -> 'german', 'English' -> 'english'."""
    name = (value or "").split(":")[0].split("(")[0].strip().lower()
    return LANGUAGE_ALIASES.get(name, name)


def _tuple(value) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def from_dict(data: dict) -> Settings:
    known = {f.name: f for f in fields(Settings)}
    kwargs = {}
    for key, value in (data or {}).items():
        if key not in known or value is None:
            continue
        default = known[key].default
        if isinstance(default, tuple):
            value = tuple(str(v).lower() for v in _tuple(value)) if key != "keywords" else _tuple(value)
        kwargs[key] = value
    s = Settings(**kwargs)
    # Fill the spellings from the plain names when /setup left them empty.
    extra = {}
    if not s.city_names and s.city:
        extra["city_names"] = (s.city.lower(),)
    if not s.country_names and s.country:
        extra["country_names"] = (s.country.lower(),)
    return Settings(**{**kwargs, **extra}) if extra else s


@lru_cache(maxsize=1)
def _load(path: str) -> Settings:
    p = Path(path)
    if not p.exists():
        return Settings()
    data = yaml.safe_load(p.read_text()) or {}
    return from_dict(data.get("search") or {})


_override: Settings | None = None


def get() -> Settings:
    """The active settings: the test override if one is installed, else profile/profile.yaml."""
    return _override if _override is not None else _load(str(PROFILE))


def use(settings: Settings | None) -> None:
    """Install settings for this process (tests, dry runs). None goes back to the profile."""
    global _override
    _override = settings
    _load.cache_clear()


def require() -> Settings:
    """Settings for commands that cannot work without a country and city (scrape, filters)."""
    s = get()
    if not s.configured:
        raise SystemExit("No search settings yet: run /setup in Claude Code (it writes the `search:` section of "
                         "profile/profile.yaml).")
    return s
