"""Cheap rule-based filtering before Claude spends effort scoring.

Each check returns a reason string when the job should be dropped. Stage 1 uses
listing data only; stage 2 runs after the description has been fetched. Where the user can work (city, country,
remote scope), the languages they speak and the experience limit come from `settings`.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from . import settings
from .normalize import Job, parse_iso
from .settings import COUNTRY_SOURCES, LANGUAGE_ALIASES, Settings, language_name

NEVER = re.compile(r"(?!)")


def alternation(names) -> str:
    """Regex alternatives for literal names, longest first."""
    return "|".join(re.escape(n) for n in sorted(set(names), key=len, reverse=True))


SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|lead|leader|principal|staff|head|manager|architect|expert|director|"
    r"mid|regular|starszy|kierownik|vp|intermediate|iii|iv|cto|cfo|supervising)\b", re.I)
# Full mode keeps mid-level titles: 2-3 year roles are worth a try when the rest fits.
SENIOR_TITLE_FULL = re.compile(
    r"\b(senior|sr\.?|lead|leader|principal|staff|head|manager|architect|expert|director|"
    r"starszy|kierownik|vp|iii|iv|cto|cfo|team leader|tech lead|supervising)\b", re.I)
JUNIOR_TITLE = re.compile(
    r"\b(intern|internship|trainee|junior|jr\.?|graduate|grad|entry|entry[- ]level|praktyk\w*|staż\w*|stażyst\w*|"
    r"absolwent\w*|student\w*|young talent|associate|apprentice\w*|early career|młodsz\w*|mlodsz\w*|"
    r"początkując\w*|working student|werkstudent|new grad|campus|academy|akademi\w*|bootcamp)\b", re.I)
# Junior words strong enough to override a senior word ("Senior Associate" stays senior, "Graduate Lead..." does not).
JUNIOR_OVERRIDE = re.compile(JUNIOR_TITLE.pattern.replace("associate|", ""), re.I)


# ---------- language packs ----------

@dataclass(frozen=True)
class LanguagePack:
    """What gives away a job that needs one language. The requirement checks (required, stopwords, title_words) run
    only when the user doesn't speak the language; `non_tech` is title vocabulary and applies to everyone."""
    required: re.Pattern | None = None     # "fluent X required" wording; None = the generic pattern (required_rx)
    stopwords: re.Pattern | None = None    # common words: many of them = the description is written in X
    title_words: re.Pattern | None = None  # job-title words in X: a title written only in X = a role for X speakers
    tech_title: re.Pattern | None = None   # tech titles in X, left to the description check
    letters: re.Pattern | None = None      # letters typical of X
    places: re.Pattern | None = None       # place names written in X, removed before judging a title
    non_tech: str = ""                     # non-tech title words in X (regex alternatives)


POLISH = LanguagePack(
    required=re.compile(
        r"(fluent|native|very good|excellent|good|proficient|communicative)\s+(command of\s+|knowledge of\s+)?"
        r"(both\s+)?polish"
        r"|(command|knowledge)\s+of\s+(both\s+)?polish"
        r"|polish\s*\(\s*(minimum|min\.?|at least)?\s*(b2|c1|c2)"
        r"|\b(in|of)\s+(both\s+)?(english\s+and\s+polish|polish\s+and\s+english)\b"
        r"|(english|angielski)\w*\s+(and|i)\s+polish\w*\s*(\(|-|–|:)?\s*(minimum|min\.?|at least)?\s*(b2|c1|c2|fluent)"
        r"|polish\s+(language\s+)?(is\s+)?(required|mandatory|must|c1|c2|b2)"
        # "Professional English and Polish language skills (C1)"
        r"|polish\s+(language\s+)?skills\s*\(\s*(minimum|min\.?|at least)?\s*(b2|c1|c2)"
        r"|(biegła|bardzo dobra|dobra|komunikatywna)\s+znajomość\s+języka\s+polskiego"
        r"|język\s+polski\s*[-–:(]?\s*(c1|c2|b2|biegł|ojczyst)", re.I),
    stopwords=re.compile(
        r"\b(oraz|jest|będzie|dla|nasz\w*|wymagania|doświadczeni\w*|znajomość|pracy|praca|"
        r"zespole|oferujemy|zakres|obowiązków|umiejętnoś\w*|się|których|który|która)\b", re.I),
    # "Recepcjonista", "Młodszy Specjalista ds. ...": a role for Polish speakers. Bilingual titles
    # ("Informatyk / IT Specialist") stay.
    title_words=re.compile(
        r"(pracowni[kc]|specjalist|asystent|pomocni[kc]|kierowni[kc]|młodsz|starsz|recepcjonist|monter|konserwator|"
        r"księgow|kasjer|magazynier|kierowc|referent|koordynator|doradc|opiekun|praktykant|stażyst|projektant|"
        r"\bds\.|\bdo spraw\b|obsług)", re.I),
    tech_title=re.compile(r"(programist|inżynier|tester|analityk|informatyk|administrator|devops|wdrożeni)", re.I),
    letters=re.compile(r"[ąćęłńśźżó]", re.I),
    places=re.compile(r"(kraków|łódź|poznań|wrocław|gdańsk|białystok|rzeszów|toruń|bielsko-biała|częstochowa|"
                      r"śląsk\w*|małopolsk\w*|warszawa|ul\.\s*\S+)", re.I),
    non_tech=(r"\brekruter|sprzedaw|sprzedaży|handlow|księgow|prawnik|kierowca|magazyn|grafik|budowlan|glazur|"
              r"mechanik|mechaniczk|diagnosta|kurier|spedytor|pielęgniar|kopark|ładowark|wózka widłow|kucharz|"
              r"sprzątan|technik serwisu|recepcjonist|elektromonter|konserwator"),
)
ENGLISH = LanguagePack(stopwords=re.compile(
    r"\b(the|and|with|you|will|our|team|experience|requirements|we|offer|skills|your|of|to)\b", re.I))
GERMAN = LanguagePack(
    stopwords=re.compile(
        r"\b(und|der|das|wir|sie|ihre|mit|für|eine|einen|einer|nicht|sowie|bei|unser\w*|aufgaben|anforderungen|"
        r"kenntnisse|erfahrung\w*|bieten|deine|dein|du|dich|dir|oder|auch|werden)\b", re.I),
    # "Mitarbeiter*in IT-Support", "Sachbearbeiter (m/w/d)": roles for German speakers. Werkstudent is left out on
    # purpose: English-speaking working-student roles exist and users search for them.
    title_words=re.compile(
        r"(mitarbeiter|sachbearbeiter|berater|fachkraft|kauf(mann|frau|leute)|leiter|referent|assistenz|betreuer|"
        r"verkäufer|monteur|elektroniker|azubi|ausbildung|duales studium|praktikant|spezialist|techniker)", re.I),
    tech_title=re.compile(r"(entwickler|informatiker|ingenieur|administrator|devops|tester|analyst|software|daten|it-)",
                          re.I),
    letters=re.compile(r"[äöüß]", re.I),
    places=re.compile(r"(münchen|köln|düsseldorf|nürnberg|zürich|österreich|württemberg|straße|str\.)", re.I),
    non_tech=(r"pflege|verkäufer|vertrieb|fahrer|lagerist|buchhalt|steuerfach|erzieher|reinigung|köchin|\bkoch\b|"
              r"kassierer|friseur|elektriker|zusteller|kommissionier"),
)
# Other big job-market languages: only "the description is written in X" for now.
FRENCH = LanguagePack(stopwords=re.compile(
    r"\b(nous|vous|votre|vos|avec|pour|les|des|une|est|sont|dans|sur|notre|nos|poste|missions|profil|expérience|"
    r"compétences)\b", re.I))
SPANISH = LanguagePack(stopwords=re.compile(
    r"\b(nosotros|para|con|los|las|una|del|que|por|tus|nuestr\w*|experiencia|empresa|equipo|puesto|requisitos|"
    r"ofrecemos)\b", re.I))
DUTCH = LanguagePack(stopwords=re.compile(
    r"\b(wij|jij|je|jouw|het|een|van|voor|met|zijn|onze|ons|bij|ervaring|functie|werkzaamheden|kennis)\b", re.I))
ITALIAN = LanguagePack(stopwords=re.compile(
    r"\b(noi|siamo|della|delle|degli|per|con|una|che|nostr\w*|esperienza|azienda|requisiti|offriamo|lavoro)\b", re.I))
PORTUGUESE = LanguagePack(stopwords=re.compile(
    r"\b(nós|você|para|com|uma|dos|das|que|noss\w*|experiência|empresa|vaga|requisitos|oferecemos)\b", re.I))
# Languages whose ads need more than the generic wording below. Add a pack per language as boards need it.
PACKS = {"polish": POLISH, "english": ENGLISH, "german": GERMAN, "french": FRENCH, "spanish": SPANISH,
         "dutch": DUTCH, "italian": ITALIAN, "portuguese": PORTUGUESE}
POLISH_REQUIRED_TEXT = POLISH.required  # imported by name elsewhere

# How job text names a language: its English name, plus the Polish adjective Polish ads use ("z j. niemieckim").
# Polish itself is covered by its pack.
LANGUAGE_WORDS = {
    "english": ("english", r"angielsk\w*"), "german": ("german", r"niemieck\w*", "deutsch"),
    "french": ("french", r"francusk\w*"), "spanish": ("spanish", r"hiszpańsk\w*"), "italian": ("italian", r"włosk\w*"),
    "czech": ("czech", r"czesk\w*"), "dutch": ("dutch",), "slovak": ("slovak",), "hungarian": ("hungarian",),
    "romanian": ("romanian",), "finnish": ("finnish",), "swedish": ("swedish",), "norwegian": ("norwegian",),
    "danish": ("danish",), "portuguese": ("portuguese",), "greek": ("greek",), "hebrew": ("hebrew",),
    "arabic": ("arabic",), "ukrainian": ("ukrainian",), "russian": ("russian",), "turkish": ("turkish",),
    "azerbaijani": ("azerbaijani",), "chinese": ("chinese",), "japanese": ("japanese",), "korean": ("korean",),
}
# Languages checked only by the generic wording; a job needing one the user doesn't speak is "other language required".
OTHER_LANGUAGES = tuple(lang for lang in LANGUAGE_WORDS if lang not in PACKS)
_KNOWN_LANGUAGES = set(LANGUAGE_ALIASES.values()) | set(LANGUAGE_WORDS) | set(PACKS)


@lru_cache(maxsize=64)
def _language_names(langs: tuple[str, ...]) -> str:
    return r"\b(?:" + "|".join(w for lang in langs for w in LANGUAGE_WORDS.get(lang, (re.escape(lang),))) + r")\b"


@lru_cache(maxsize=64)
def required_rx(langs: tuple[str, ...]) -> re.Pattern:
    """Generic "<language> required" wording: fluent / native / C1 / B2 / required next to the language's name."""
    if not langs:
        return NEVER
    n = _language_names(langs)
    return re.compile(rf"(fluent|fluently|native|proficien\w*|very good|excellent|c1|b2)\b[^.\n]{{0,40}}{n}"
                      rf"|{n}[^.\n]{{0,25}}\b(fluent|fluently|native|required|c1|b2)\b", re.I)


@lru_cache(maxsize=64)
def title_hint_rx(langs: tuple[str, ...]) -> re.Pattern:
    """A title asking for a language: "Service Desk Agent with Czech", "Developer z j. niemieckim"."""
    if not langs:
        return NEVER
    return re.compile(rf"\b(with|z|speaking|\()\s*(j\.\s*)?{_language_names(langs)}", re.I)


class _FromSettings:
    """A regex rebuilt for the active settings on each use, for callers that import it by name."""

    def __init__(self, build):
        self._build = build

    def __getattr__(self, name):
        return getattr(self._build(settings.get()), name)


def _unspoken(s: Settings, langs) -> tuple[str, ...]:
    return tuple(lang for lang in langs if not s.speaks(lang))


def _packs(s: Settings) -> list[tuple[str, LanguagePack]]:
    """Packs of the languages the user doesn't speak: only those can block a job."""
    return [(lang, pack) for lang, pack in PACKS.items() if not s.speaks(lang)]


# Generic "<language> required" wording for the OTHER_LANGUAGES the user doesn't speak.
OTHER_LANG_TEXT = _FromSettings(lambda s: required_rx(_unspoken(s, OTHER_LANGUAGES)))
NICE_TO_HAVE = re.compile(r"(nice to have|is a plus|an advantage|mile widzian|będzie atutem|dodatkowym atutem)", re.I)


# ---------- titles ----------

NON_TECH_TITLE = re.compile(
    r"(\brecruiter|talent acquisition|\bsales\b|marketing|accountant|accounting|controlling|\bhr\b|human resources|"
    r"lawyer|\bdriver\b|warehouse|call center|telemarket|copywriter|graphic designer|"
    r"account executive|business development|sales development|customer success|accounts payable|"
    r"compensation|sourcer|candidate experience|executive assistant|influencer|creative director|"
    r"social growth|renewals|creative designer|"
    # Finance/legal/pharma/retail domains: dropped unless the title also names a tech role.
    r"\bkyc\b|\baml\b|anti[- ]money|sanctions|pharmacovigilance|drug safety|clinical (research|trial|study)|"
    r"regulatory affairs|\btax\b|payroll|\blegal\b|\bprivacy\b|fp&a|\bcredit\b|customer care|customer service|"
    r"\bretail\b|transaction services|\bm&a\b|valuation|restructuring|corporate affairs|founder.?s associate|"
    r"documentation|\btmf\b|constructor technologist|"
    # Manual / service work.
    r"\bcourier\b|\bnurse\b|room attendant|\bporter\b|housekeep|visual merchandiser|\bchef\b|\bcleaner\b|"
    # Finance operations, procurement, admin and front-desk titles.
    r"reconciliation|procurement|purchasing|\bbuyer\b|accounts receivable|middle office|corporate actions|"
    r"litigation|complaints|business controller|real estate|welcome desk|receptionist|"
    r"office assistant|project assistant|project administrator|compliance investigation|customer activation|"
    r"\btreasury\b|transportation operation"
    # The same kinds of titles in other languages.
    + "".join("|" + p.non_tech for p in PACKS.values() if p.non_tech) + ")", re.I)
# "Analyst" alone is not tech (KYC/credit/sanctions analysts): only with a tech word in front of it.
TECH_TITLE = re.compile(
    r"(develop|engineer|programist|programmer|devops|\bqa\b|tester|\bdata\b|it support|"
    r"helpdesk|help desk|service desk|software|cloud|security|erp|consultant|administrator|"
    r"\b(business|system|systems|it|bi|product|cyber|soc|network|technical|reporting|insights?|test|"
    r"operations|quant\w*)\s+analyst|analityk danych)", re.I)
ALWAYS_NON_TECH = re.compile(
    r"(\brecruiter|\brekruter|talent acquisition|ochrony|sales representative|account executive|"
    r"business development|sales development|customer success|creative designer)", re.I)

# Numbered levels: "Software Engineer 5", "Developer 4". 1-3 stay: at some employers "Engineer 2" is mid level.
LEVEL_TITLE = re.compile(r"\b(engineer|developer|scientist|analyst)\s+[4-9]\b", re.I)
PHD_TITLE = re.compile(r"\bph\.?d\b", re.I)
PHD_REQUIRED = re.compile(
    r"(ph\.?d\.?\s+(degree\s+)?(is\s+)?(required|mandatory)|(must|need to) have a ph\.?d|"
    r"(masz|posiadasz)\s+stopień\s+ph\.?d|stopień\s+doktora)", re.I)
PROGRAMME_TITLE = re.compile(r"\b(intern|internship|trainee|graduate|praktyk\w*|staż\w*|student\w*|working student|"
                             r"werkstudent)\b", re.I)
PROGRAMME_HOURS = 14 * 24
COMPANY_BOARDS = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday", "sf_rmk", "amazon", "microsoft", "google",
                  "recruitee", "workable", "capgemini", "eightfold",
                  "oracle_hcm", "sf_csb", "phenom"}

SENIOR_LEVELS = {"mid", "senior", "expert", "lead", "manager", "director", "mid-senior level",
                 "executive", "mid_senior_level"}
SENIOR_LEVELS_FULL = {"senior", "expert", "lead", "manager", "director", "executive"}


def years_limit(mode: str = "narrow") -> int:
    """Most years of experience a job may ask: settings.max_years in full mode, at most 2 in narrow (entry) mode."""
    s = settings.get()
    return s.max_years if mode == "full" else min(2, s.max_years)


class _YearsByMode:
    """`MAX_YEARS[mode]`, read from the settings on each lookup."""

    def __getitem__(self, mode: str) -> int:
        return years_limit(mode)


MAX_YEARS = _YearsByMode()

YEARS = re.compile(
    r"(?:(?:at least|minimum|min\.?|co najmniej|minimum)\s*)?(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*"
    r"(?:years?|yrs?|lat[a]?|roku)\b[^.\n]{0,60}?(?:experience|exp\.|doświadcz)", re.I)
# Experience first: "Proven experience of 5+ years in DevOps", "experience: 3 years".
YEARS_AFTER = re.compile(
    r"(?:experience|doświadczeni\w*)\s*(?:of|:|-|–|\()?\s*(?:at least|minimum|min\.?|co najmniej|ponad)?\s*"
    r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?|lat[a]?)\b", re.I)
# Without the word "experience": "4+ years in commercial software development", "6+ years in DevOps, platform or SRE
# engineering", "9+ years in a corporate IT environment", "10 years of Java". Needs a "+" or a work word right after,
# so "a company with 15 years on the market" stays out.
YEARS_WORK = re.compile(
    r"(?:(?:at least|minimum|min\.?)\s*)?(\d{1,2})\s*(\+)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b"
    r"(?:\s*(?:of|in|with|as|working|developing|building|programming|managing|operating|hands-on|commercial|"
    r"professional|industry|relevant|practical|production|proven|solid)\b)", re.I)
WORK_WORDS = re.compile(
    r"(develop|engineer|programm|administrat|commercial|professional|hands-on|industry|relevant|practical|production|"
    r"environment|\brole|position|testing|\bqa\b|devops|\bsre\b|support|java|python|c#|\.net|react|angular|node|"
    r"cloud|aws|azure|network|security|data|analys|design|consult|it\b|software|project|implementation)", re.I)
COMPANY_AGE = re.compile(r"(\bwe (have|'ve|are (a|an|one))\b|\bour (company|firm|history|experience)\b|company|market|"
                         r"founded|history|clients|customers|business|on the|since|heritage|operating for|in operation)",
                         re.I)


def title_written_in(title: str, lang: str) -> bool:
    """Every part of the title ("A / B") is written in this language. Bilingual titles are not."""
    pack = PACKS[lang]
    parts = re.split(r"\s*[/|]\s*", re.sub(r"\([^)]*\)", " ", title or ""))
    if pack.places:
        parts = [pack.places.sub(" ", p) for p in parts]
    parts = [p for p in parts if p.strip()]
    return bool(parts) and all(pack.title_words.search(p) or (pack.letters and pack.letters.search(p)) for p in parts)


def foreign_title(title: str, s: Settings) -> str | None:
    """Language of a non-tech title written only in a language the user doesn't speak. Tech titles in that language
    are left to the description check."""
    for lang, pack in _packs(s):
        if pack.title_words and title_written_in(title, lang) and not (
                TECH_TITLE.search(title) or (pack.tech_title and pack.tech_title.search(title))):
            return lang
    return None


def check_listing(job: Job, hours: int, now: datetime | None = None, mode: str = "narrow") -> str | None:
    now = now or datetime.now(timezone.utc)
    s = settings.get()
    title = job.title or ""
    senior_title = SENIOR_TITLE_FULL if mode == "full" else SENIOR_TITLE
    senior_levels = SENIOR_LEVELS_FULL if mode == "full" else SENIOR_LEVELS

    if s.tech_only and (ALWAYS_NON_TECH.search(title) or (NON_TECH_TITLE.search(title) and not TECH_TITLE.search(title))):
        return "non-tech role"
    excluded = next((w for w in s.exclude_titles if re.search(rf"\b{re.escape(w)}", title, re.I)), None)
    if excluded:
        return f"excluded title ({excluded})"
    if (senior_title.search(title) or LEVEL_TITLE.search(title)) and not JUNIOR_OVERRIDE.search(title):
        return "senior title"
    if PHD_TITLE.search(title):
        return "PhD required"
    lang = foreign_title(title, s)
    if lang:
        return f"{lang.capitalize()} title"
    if job.seniority and job.seniority.lower() in senior_levels:
        return f"seniority {job.seniority}"

    if not location_ok(job):
        return f"location {job.location}"

    # Internship and graduate programmes stay open for weeks, so a short window misses them.
    # Company career boards (watchlist) post rarely and are targeted employers: 7 days.
    is_programme = is_programme_job(job)
    max_age = max(hours, PROGRAMME_HOURS) if is_programme else hours
    if job.source in COMPANY_BOARDS:
        max_age = max(max_age, 7 * 24)
    posted = parse_iso(job.posted_at)
    # A date without a time (LinkedIn cards) parses as midnight, so a job posted yesterday evening looked 40 h old at
    # an afternoon run and fell out of a 24 h scan. Judge such dates by the end of that day.
    if posted and len((job.posted_at or "").strip()) == 10:
        posted += timedelta(days=1)
    if posted and now - posted > timedelta(hours=max_age):
        return f"older than {max_age}h"

    return listing_language_reason(job, s)


def is_programme_job(job: Job) -> bool:
    return job.seniority == "intern" or bool(PROGRAMME_TITLE.search(job.title or ""))


def check_details(job: Job, max_years: int | None = None) -> str | None:
    s = settings.get()
    asked = [lang for lang in _asked_languages(job, s) if lang in PACKS]
    if asked:
        return f"{asked[0].capitalize()} required (languages)"
    text = job.description or ""
    reason = description_language_reason(text, s)
    if reason:
        return reason
    if _firm(PHD_REQUIRED, text):
        return "PhD required"
    years = required_years(text)
    limit = years_limit("narrow") if max_years is None else max_years
    if years is not None and years > limit:
        return f"{years}+ years required"
    if _firm(SENIOR_TEXT, text):
        return "senior role (description)"
    return remote_residency_reason(job) or work_mode_reason(job)


def _firm(rx, text: str) -> bool:
    """rx matches and no "nice to have" / "is a plus" next to the match softens it."""
    m = rx.search(text)
    return bool(m) and not NICE_TO_HAVE.search(text[max(0, m.start() - 80): m.end() + 80])


# Neutral titles hiding a senior role: "we are looking for a Senior Solution Engineer", "Hands-on MLOps or ML
# engineering at senior level".
SENIOR_TEXT = re.compile(
    r"\b(looking for|seeking|hiring|searching for|join (us|our team) as|we need)\s+(an?\s+)?(experienced\s+|highly "
    r"skilled\s+)?(senior|lead|principal|staff)\b"
    r"(?![-\s]*(minded|/\s*mid|or mid|team|leadership|management|stakeholders|engineers|colleagues))|"
    r"\b(at|on) (a )?senior level\b|\bwork(ing)? as a (technical lead|team lead)\b", re.I)


# ---------- location ----------

# A remote job that is only open to people living/authorized in another country (US, UK, Canada, India...).
RESIDENCY = re.compile(
    r"(must (be|reside|live) (currently )?(based |located |residing )?in (the )?(us|u\.s\.|usa|united states|uk|"
    r"united kingdom|canada|india|australia|brazil|mexico|latam|latin america|apac|north america)|"
    r"(only )?(open to|accepting|considering) (candidates|applicants) (located |based |residing )?(in|from) (the )?"
    r"(us|u\.s\.|usa|united states|uk|united kingdom|canada|india|australia|latam|north america)|"
    r"(us|u\.s\.|uk|canada|india)[- ]based (candidates|applicants|only)|"
    r"(legally )?authori[sz]ed to work in the (us|u\.s\.|usa|united states|uk|united kingdom|canada)|"
    r"(us|u\.s\.|uk) (work authori[sz]ation|citizenship|citizens? only)|"
    r"remote (within|in) (the )?(us|u\.s\.|usa|united states|uk|united kingdom|canada|india|north america|latam)|"
    r"\b(est|pst|cst|mst|et|pt) (time ?zone|hours)|eligible to work in the (us|u\.s\.|united states|uk|canada))", re.I)

BARE_REMOTE = re.compile(r"^\s*(remote|zdalna|praca zdalna|anywhere)?\s*$", re.I)
REMOTE_WORD = re.compile(r"remote|zdaln|home[- ]?office", re.I)
# A location part that names a work mode, not a place.
WORK_MODE_PART = re.compile(r"^(remote|zdaln\w*|hybrid|on[- ]?site|office|home[- ]?office|flexible)\b", re.I)
WORLD = r"worldwide|anywhere|global|international"
# Words of a remote location that name no place; anything else left over is a place.
_NO_PLACE = re.compile(r"\b(remote|zdaln\w*|praca|home|office|fully|full|only|first|friendly|possible|work|from|in|"
                       r"the|of|and|or|world|any|time ?zones?)\b|[\W\d_]+", re.I)


@lru_cache(maxsize=8)
def _region(s: Settings) -> re.Pattern:
    """Location words for remote work open beyond the country: worldwide wording and the region_names. Long names
    match as word prefixes ("europe" also covers "European")."""
    long_ = [n for n in s.region_names if len(n) > 3]
    short = [n for n in s.region_names if len(n) <= 3]
    parts = [rf"\b(?:{WORLD})\b"]
    if long_:
        parts.append(rf"\b(?:{alternation(long_)})")
    if short:
        parts.append(rf"\b(?:{alternation(short)})\b")
    return re.compile("|".join(parts), re.I)


@lru_cache(maxsize=8)
def _residency_ok(s: Settings) -> re.Pattern:
    """Words saying a residency rule includes the user: their country, their region, or the whole world."""
    names = alternation(s.country_names + s.region_names)
    return re.compile(rf"\b({names + '|' if names else ''}anywhere in the world|worldwide)\b", re.I)


def remote_residency_reason(job: Job) -> str | None:
    """Remote jobs open beyond the country can still be limited to another country's residents in the text. Jobs
    located in the user's city or country are not affected."""
    s = settings.get()
    loc = (job.location or "").lower()
    if s.in_city(loc) or s.in_country(loc) or other_city(loc, s):
        return None
    text = job.description or ""
    m = RESIDENCY.search(text)
    if not m:
        return None
    if _residency_ok(s).search(text[max(0, m.start() - 150): m.end() + 150]):
        return None  # "remote within the US or the EU"
    return f"remote limited to another country ('{m.group(0)}')"


FULL_REMOTE = (r"fully remote|100\s*%\s*remote|remote[- ]first|work from anywhere|praca (w pełni )?zdalna|"
               r"100\s*%\s*zdaln|role is remote|position is remote|this is a remote|can be (done|executed) remotely|"
               r"remote or hybrid from|work remotely or")
ONSITE = re.compile(
    r"(\bhybrid\b|hybrydow|on[- ]?site|stacjonarn|in the office|from (our|the) office|office[- ]based|"
    r"days? (a|per) (week|month) (in|at|from) (the|our) office|\d+ days? (in|at) the office|relocat\w*)", re.I)


@lru_cache(maxsize=8)
def _full_remote(s: Settings) -> re.Pattern:
    """Fully-remote wording, including "remote within <the country or region>"."""
    places = alternation(s.country_names + s.region_names)
    extra = (rf"|from anywhere in ({places})|remote (within|in|from) (the )?({places})|remote work in ({places})"
             if places else "")
    return re.compile(f"({FULL_REMOTE}{extra})", re.I)


@lru_cache(maxsize=8)
def _city_mention(s: Settings) -> re.Pattern:
    """The user's city named in text, inflected forms too (a final vowel may change)."""
    stems = {n[:-1] if len(n) > 4 and n[-1] in "aeiouy" else n for n in s.city_names}
    return re.compile(rf"\b({alternation(stems)})\w*", re.I) if stems else NEVER


def work_mode_reason(job: Job) -> str | None:
    """A job in another city of the country that is really hybrid/on-site there: "remote" searches and remote flags
    also return such jobs."""
    s = settings.get()
    loc = (job.location or "").lower()
    text = job.description or ""
    if s.relocate or s.in_city(loc) or not text or _full_remote(s).search(text):
        return None
    if _city_mention(s).search(text):
        return None  # the office it talks about may be the one in the user's city
    city = other_city(loc, s)
    m = ONSITE.search(text)
    if m:
        city = city or s.other_city(text.lower())
        if city:
            return f"hybrid/on-site outside {s.city} ({city}: '{m.group(0)}')"
    # LinkedIn's remote filter is unreliable: a job pinned to another city needs explicit remote wording.
    if job.source == "linkedin" and city:
        return f"location {job.location} (no explicit remote in description)"
    return None


def other_city(loc: str, s: Settings | None = None) -> str | None:
    """Another city of the user's country named in a location: one of settings.other_cities or, when none are
    listed, the city part of "<City>, <Country>"."""
    s = s or settings.get()
    low = (loc or "").lower()
    if s.other_cities:
        return s.other_city(low)
    parts = [p.strip() for p in low.split(",")]
    if len(parts) < 2 or s.in_city(low) or not any(s.in_country(p) for p in parts):
        return None
    region = _region(s)
    return next((p for p in parts if p and not (s.in_country(p) or WORK_MODE_PART.match(p) or region.search(p))),
                None)


def country_boards(s: Settings) -> set[str]:
    """Boards whose remote flag means "remote within the user's country": the country's own job boards, plus LinkedIn
    and Indeed, which are searched for that country."""
    if not s.country_code:
        return set()
    return {*COUNTRY_SOURCES.get(s.country_code.upper(), ()), "linkedin", "indeed"}


def _names_other_place(loc: str, s: Settings) -> bool:
    rest = _region(s).sub(" ", loc)
    for name in s.country_names + s.city_names:
        rest = rest.replace(name, " ")
    return bool(_NO_PLACE.sub(" ", rest).strip())


def _remote_ok(loc: str, remote: bool, s: Settings) -> bool:
    """Remote work beyond the country, as far as settings.remote allows. A remote job pinned to one other country
    ("Canada (Remote)", "Remote - US", "Spain") usually needs residence there and stays out."""
    if s.remote == "country":
        return False
    if _region(s).search(loc):
        return True  # "Worldwide", "EMEA (Remote)", "European Union"
    return s.remote == "worldwide" and remote and not _names_other_place(loc, s)


def location_ok(job: Job) -> bool:
    s = settings.get()
    loc = (job.location or "").lower()
    if s.in_city(loc):
        return True
    remote = job.remote or bool(REMOTE_WORD.search(loc))
    # "<City>, <Country>" is on-site in that city: other cities of the country count only when the job is remote.
    if other_city(loc, s):
        return s.relocate or remote
    if s.in_country(loc) or _remote_ok(loc, remote, s):
        return True
    if job.source == "linkedin":
        # LinkedIn also returns remote jobs pinned to another country ("Canada (Remote)"): a bare "Remote" is fine,
        # a foreign country is not.
        return BARE_REMOTE.match(loc) is not None
    if job.source in country_boards(s):
        return job.remote or BARE_REMOTE.match(loc) is not None
    return False


# ---------- languages ----------

def _language_of(entry: str) -> str:
    """'German:C1' -> 'german', 'English - B2' -> 'english', 'Język angielski' -> 'english'."""
    name = language_name(entry)
    if not name or name in _KNOWN_LANGUAGES:
        return name
    words = (language_name(w) for w in re.findall(r"[^\W\d_]+", name))
    return next((w for w in words if w in _KNOWN_LANGUAGES), name)


def _asked_languages(job: Job, s: Settings) -> list[str]:
    """Languages in the listing's languages field that the user doesn't speak, pack languages first."""
    asked = [lang for lang in map(_language_of, job.languages or []) if lang and not s.speaks(lang)]
    return [lang for lang in PACKS if lang in asked] + [lang for lang in asked if lang not in PACKS]


def listing_language_reason(job: Job, s: Settings | None = None) -> str | None:
    """A language the listing asks for that the user doesn't speak: in its languages field, or a title hint."""
    s = s or settings.get()
    asked = _asked_languages(job, s)
    if asked:
        return f"{asked[0].capitalize()} required (listing)" if asked[0] in PACKS else "other language required"
    title = job.title or ""
    for lang, _ in _packs(s):
        if lang in LANGUAGE_WORDS and title_hint_rx((lang,)).search(title):
            return f"{lang.capitalize()} required (title)"
    if title_hint_rx(_unspoken(s, OTHER_LANGUAGES)).search(title):
        return "other language required"
    return None


def is_mostly(text: str, lang: str) -> bool:
    """The text is written in this language: 8+ of its common words, more than of any other pack's."""
    count = len(PACKS[lang].stopwords.findall(text))
    others = [len(p.stopwords.findall(text)) for other, p in PACKS.items() if other != lang and p.stopwords]
    return count >= 8 and count > max(others, default=0)


def is_mostly_polish(text: str) -> bool:
    return is_mostly(text, "polish")


def _language_findings(text: str, s: Settings):
    """(kind, language) for each language blocker in a description, in check order: written in a language the user
    doesn't speak, then asking for one. Language None = one of OTHER_LANGUAGES."""
    if not text:
        return
    packs = _packs(s)
    for lang, pack in packs:
        if pack.stopwords and is_mostly(text, lang):
            yield "written", lang
    for lang, pack in packs:
        if _firm(pack.required or required_rx((lang,)), text):
            yield "required", lang
    if _firm(required_rx(_unspoken(s, OTHER_LANGUAGES)), text):
        yield "required", None


def description_language_reason(text: str, s: Settings | None = None) -> str | None:
    kind, lang = next(_language_findings(text, s or settings.get()), (None, None))
    if kind is None:
        return None
    if lang is None:
        return "other language required"
    return f"description in {lang.capitalize()}" if kind == "written" else f"{lang.capitalize()} required (description)"


def language_flags(text: str) -> list[str]:
    """Language blockers in an employer's posting for the active settings, e.g. ["posting in Polish",
    "Polish required", "other language required"]."""
    labels = {"written": "posting in {}", "required": "{} required"}
    return [labels[kind].format(lang.capitalize()) if lang else "other language required"
            for kind, lang in _language_findings(text, settings.get())]


def required_years(text: str) -> int | None:
    """Largest 'N years of experience' requirement mentioned, ignoring nice-to-haves.

    Ads list several requirements ("3+ years support, 1+ year Linux"); the strictest one decides.
    For ranges ("1-2 years") the lower bound counts.
    """
    found = []
    for m in [*YEARS.finditer(text), *YEARS_AFTER.finditer(text)]:
        window = text[max(0, m.start() - 60): m.end() + 40]
        if NICE_TO_HAVE.search(window):
            continue
        found.append(int(m.group(1)))
    for m in YEARS_WORK.finditer(text):
        window = text[max(0, m.start() - 60): m.end() + 40]
        after = text[m.end(): m.end() + 50]
        before = text[max(0, m.start() - 40): m.start()]
        if NICE_TO_HAVE.search(window) or COMPANY_AGE.search(before) or COMPANY_AGE.search(after[:25]):
            continue
        if m.group(2) or WORK_WORDS.search(after) or WORK_WORDS.search(m.group(0)):
            found.append(int(m.group(1)))
    found = [n for n in found if 0 < n < 20]
    return max(found) if found else None
