"""Application history from Gmail: what the user already applied to, and what is pending.

Rows come from Gmail's search-results list (browser/gmail_rows.js) — sender, subject, preview
snippet, date. /mail-check rows (browser/gmail_rows_bodies.js) also carry `body`, the newest message's text from
the print view; when present it is stored and classified instead of the ~150-char snippet. Each row is classified (applied / test / interview /
rejection / offer / other) and matched to jobs by company, so the ranked list can warn
"already applied" or "test pending" before the user picks.
"""

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from functools import lru_cache

from . import settings
from .db import now

# Gmail search used to (re)build the history. Restrict with newer_than:14d for incremental runs.
GMAIL_QUERY = (
    "(subject:(application OR applying OR applied OR candidate OR candidacy OR interview OR assessment OR "
    "\"skills test\" OR \"thank you for your interest\" OR aplikacja OR aplikację OR kandydat OR rekrutacja OR rozmowa) OR "
    "from:(smartrecruiters OR myworkday OR myworkdayjobs OR greenhouse OR lever OR ashby OR darwinbox OR "
    "teamtailor OR pracuj OR justjoin OR nofluffjobs OR erecruiter OR workable OR recruitee OR peopleforce OR "
    "testedrecruits OR icims OR jobvite OR traffit OR successfactors)) "
    "newer_than:2y"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_history (
    id           INTEGER PRIMARY KEY,
    thread_id    TEXT,
    msg_date     TEXT,              -- ISO date
    sender_name  TEXT,
    sender_email TEXT,
    subject      TEXT,
    snippet      TEXT,
    kind         TEXT,              -- applied | test | interview | rejection | offer | status | other
    body         TEXT,              -- newest message text (print view), when the importer had it
    imported_at  TEXT,
    UNIQUE (thread_id, msg_date, subject)
);
"""

KINDS = [  # first match wins, most significant first
    # Job-board alerts ("78% match: Data Analyst job for you") are not applications at all, nor are LinkedIn alert
    # digests ('“internship”: Acme - Security engineer intern posted on 10/4/26'), which would otherwise look like a
    # same-role application.
    ("alert", r"(\d+% match|jobs? for you|new jobs? (matching|for)|job alert|recommended jobs|oferty dla ciebie|"
              r"nowe oferty|proponowane oferty|you may be interested|^[“\"][^”\"]{1,60}[”\"]:\s|"
              r"posted on \d{1,2}/\d{1,2}/\d{2})"),
    ("offer", r"(offer letter|pleased to offer you|(would|'d) like to (make you an |extend an )?offer|"
              r"congratulations[^.]{0,60}offer|oferta zatrudnienia|propozycja zatrudnienia)"),
    ("interview", r"(interview|rozmow[aęy] (rekrutacyjn|kwalifikacyjn)|invite you to (a |an )?(call|meeting|chat)|"
                  r"schedule (a|an) (call|chat|meeting)|zaproszenie na rozmow)"),
    ("test", r"(placement test|technical test|skills test|english test|assessment|codility|hackerrank|testgorilla|"
             r"coding (task|challenge)|zadanie rekrutacyjne|online test|test link)"),
    ("rejection", r"(unfortunately|niestety|not (be )?moving forward|other candidates|not been successful|"
                  r"regret to inform|decided to (proceed|move forward) with|unsuccessful|won'?t be progressing|"
                  r"not selected|position has been filled|(isn.t|is not|not) progressing|won.t be moving|not to (move|proceed) forward|"
                  r"(isn.t|is not|not) (the |a )?right (match|fit)|"
                  # A rejection subject "<Company> | Feedback on your application - <role>": read mails come without
                  # a body, and the snippet's "Thank you for your interest" would make it "applied". Not "feedback on
                  # your application process" (a survey).
                  r"feedback on your application\s*[-–—:]|"
                  r"nie został|nie przeszedł|odrzucon)"),
    ("status", r"(changed its status|status (update|change)|application status|update on your (job )?application|"
               r"is being (assessed|reviewed)|informacja o twojej aplikacji|pracodawca udziela)"),
    ("applied", r"(thank(s| you) (for|in) (applying|your application|your interest|applying at)|received your application|"
                r"application (has been |was )?(received|sent|submitted)|you('ve| have)? (just )?applied|"
                r"your application (to|for|as|at|with)\b|^application (for|to)\b|applied for|"
                r"dziękujemy za (aplikacj|przesłanie|zainteresowanie)|twoja aplikacja|otrzymano zgłoszenie|"
                r"aplikacja na stanowisko|zgłoszenie (na|do) |złożenie aplikacji|otrzymania aplikacji|otrzymaliśmy twoje (cv|aplikacj)|"
                r"application is now with us)"),
    # Registrations: an account exists on that company's ATS (useful when applying again).
    ("account", r"(verify your (candidate )?account|candidate account|account confirmation|confirm your (email|account)|"
                r"one-time password|potwierdź (konto|adres)|verify your email|signing up)"),
]

GENERIC_SENDERS = re.compile(
    r"^(no-?reply|noreply|notification|notifications|jobs|careers|talent|recruit\w*|hr|team|info|hello|support)$", re.I)
PLATFORM_DOMAINS = ("smartrecruiters", "myworkday", "greenhouse", "lever", "ashby", "darwinbox", "teamtailor",
                    "pracuj", "justjoin", "nofluffjobs", "linkedin", "erecruiter", "workable", "recruitee",
                    "peopleforce", "testedrecruits", "icims", "jobvite", "traffit", "successfactors", "gmail", "google",
                    "outlook", "bulldogjob", "indeed", "elementapp")
LEGAL_NOISE = {"sp", "z", "o", "oo", "s", "a", "sa", "spolka", "ograniczona", "odpowiedzialnoscia", "ltd", "llc",
               "inc", "gmbh", "group", "grupa", "the", "co", "company", "corporation",
               "technologies", "technology", "solutions", "services", "systems", "international", "global",
               "kapitalowa", "spk", "k", "sk", "of"}


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").replace("​", "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("ł", "l")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# Words that say nothing about which company it is. A core made only of these ("Management Solutions" -> "management")
# would match every email mentioning "Talent Management" or "Capacity Management": keep the full name then.
GENERIC_CORE = {"management", "consulting", "consultants", "digital", "data", "software", "it", "tech", "business",
                "capital", "partners", "energy", "health", "media", "design", "engineering", "finance", "financial",
                "bank", "network", "networks", "cloud", "labs", "lab", "studio", "talent", "talents", "people", "career",
                "careers", "recruitment", "hr", "analytics", "ai", "development", "security", "logistics", "trading",
                "innovation", "innovations", "insurance", "holding", "industries", "products", "research", "travel"}
LEGAL_FORMS = {"sp", "z", "o", "oo", "s", "a", "sa", "spolka", "ograniczona", "odpowiedzialnoscia", "ltd", "llc", "inc",
               "gmbh", "spk", "k", "sk", "kapitalowa", "co", "plc", "ag", "bv", "nv", "srl", "sas"}


@lru_cache(maxsize=4)
def _places_for(s: settings.Settings) -> tuple[frozenset, frozenset, tuple]:
    """(city words, one-word country names, other-city prefixes) of the search settings, folded."""
    cities = frozenset(w for n in (s.city, *s.city_names) for w in fold(n).split())
    countries = frozenset(fold(n) for n in (s.country, *s.country_names) if n and " " not in fold(n))
    return cities, countries, tuple(fold(c) for c in s.other_cities if fold(c))


def _places() -> tuple[frozenset, frozenset, tuple]:
    return _places_for(settings.get())


def company_core(company: str) -> str:
    """'Grupa Globex Sp. z o.o.' -> 'globex'; 'UMBRELLA POLAND sp. z o.o.' -> 'umbrella' (the search country is no
    part of a name); 'Management Solutions' -> 'management solutions' (a generic word alone is not a name)."""
    noise = LEGAL_NOISE | _places()[1]
    words = [w for w in fold(company).split() if w not in noise]
    if words and all(w in GENERIC_CORE for w in words[:3]):
        words = [w for w in fold(company).split() if w not in LEGAL_FORMS]
    return " ".join(words[:3])


# First words that are too common to stand for a company on their own ("Bank X" -> not "bank"; "Johnson X" vs
# "Johnson & Johnson"; "Operator Medyczny ..."). The search city counts too (see brand_word).
BRAND_STOP = GENERIC_CORE | {"and", "johnson", "operator", "general", "first", "new", "united", "american", "european",
                             "euro", "smart", "open", "best", "top", "star", "blue", "green", "red", "white", "black",
                             "polish", "national", "central", "north", "south", "east", "west", "city", "home", "one",
                             "my", "your", "next", "future", "global", "world", "line", "art", "service", "solution",
                             "center", "centre", "institute", "university", "academy", "foundation", "agency", "office",
                             "hub", "hubs", "team", "medical", "pharma", "bio", "auto", "motor", "power", "air", "sea",
                             # ordinary English/Polish words seen as first words of company names
                             "advanced", "advisor", "agencja", "all", "analytic", "assessment", "association", "banking",
                             "bankowy", "base", "benefit", "big", "biuro", "british", "care", "cargo", "centrala",
                             "centralny", "centrum", "change", "check", "client", "code", "cold", "commit", "computer",
                             "confidential", "core", "credit", "customs", "cyber", "danish", "delivery", "diverse",
                             "drivers", "eco", "edge", "emerging", "enter", "enterprise", "exact", "exclusive",
                             "expansion", "extreme", "fast", "few", "fitness", "fleet", "free", "french", "from", "fun",
                             "fundacja", "get", "happy", "hard", "hardware", "healthy", "hotel", "human", "hunter",
                             "idea", "indian", "insider", "instant", "instytut", "inter", "job", "jobs", "just",
                             "justice", "kancelaria", "kids", "klient", "law", "leap", "legacy", "level", "light", "link",
                             "loop", "loyalty", "macro", "man", "match", "method", "metro", "museum", "native",
                             "natural", "nest", "online", "page", "partner", "play", "plus", "polski", "polskie", "port",
                             "portal", "premium", "prime", "pro", "progress", "rekrutacja", "rekruter", "respect",
                             "right", "royal", "sales", "school", "schools", "scope", "search", "sharp", "silent",
                             "simple", "social", "solid", "solve", "square", "standard", "state", "strategic", "strong",
                             "super", "sure", "swiss", "the", "too", "total", "transition", "understanding",
                             "universal", "urban", "vision", "welcome", "whatever", "work", "worldwide",
                             "ministerstwo", "narodowy", "urzad", "zaklad", "zarzad", "spoldzielnia", "stowarzyszenie",
                             "osrodek", "samodzielny", "przychodnia", "laboratorium", "laboratoire", "medyczna",
                             "apteka", "dom", "nowa", "audio", "cosmetic"}


def brand_word(company: str) -> str:
    """The distinctive first word of a multi-word company name, e.g. 'QXR - Global Transport and Logistics' -> 'qxr'
    (its core 'qxr transport and' never appears in the company's mails, so an application there would be missed).
    '' when the first word is generic, the search city, or too short."""
    words = company_core(company).split()
    if len(words) < 2 or len(words[0]) < 3 or words[0] in BRAND_STOP or words[0] in _places()[0]:
        return ""
    return words[0]


# System word lists (macOS, most Linux). Proper nouns there are capitalised, so made-up brand names are absent.
DICT_PATHS = ("/usr/share/dict/words", "/usr/dict/words", "/usr/share/dict/web2", "/usr/share/dict/american-english",
              "/usr/share/dict/british-english")
_UNLOADED = object()
_WORDS: object = _UNLOADED


def _dictionary() -> set[str] | None:
    """Lower-case English words from the first system word list found; None when there is none (Windows, minimal
    Linux): then every brand word is treated as an ordinary word and needs support (see _brand_hit_ok)."""
    global _WORDS
    if _WORDS is _UNLOADED:
        _WORDS = None
        for path in DICT_PATHS:
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    _WORDS = {w.strip() for w in f if w[:1].islower()}
                break
            except OSError:
                continue
    return _WORDS


def _brand_hit_ok(row: sqlite3.Row, company: str, brand: str, text: str) -> bool:
    """A match on the brand word alone, when that word is an ordinary English word, needs more support: the full
    name ('falcon ridge'), or a sender whose name/domain starts with it. Otherwise 'Falcon Ridge' matches a mail from
    'Acme Falcon', 'Ruby Widgets' a 'Ruby on Rails' job and 'Seeking Beta' a mail saying 'we are seeking'. Without a
    word list every brand word needs that support."""
    words_list = _dictionary()
    if words_list is not None and brand not in words_list:
        return True
    words = company_core(company).split()
    if len(words) >= 2 and f" {words[0]} {words[1]} " in text:
        return True
    sender = fold(row["sender_name"] or "")
    domain = (row["sender_email"] or "").split("@")[-1]
    return sender.split(" ")[:1] == [brand] or (not any(p in domain for p in PLATFORM_DOMAINS)
                                                 and fold(domain).startswith(brand))


def _sender_domain(row: sqlite3.Row) -> str:
    """The sender's own domain ('initechce.com' for careers@initechce.com); '' for ATS/mail platforms."""
    domain = (row["sender_email"] or "").split("@")[-1]
    return "" if any(p in domain for p in PLATFORM_DOMAINS) else domain


def _other_company_sender(row: sqlite3.Row, core: str) -> bool:
    """True when the mail comes from some other company's own domain: a consultancy's confirmation for its
    'Contoso Lab' programme names Contoso but is no Contoso application. The whole domain counts, TLD included:
    companies with a brand TLD (careers.hr.globexbank) would otherwise look foreign in their own mails."""
    domain = fold(_sender_domain(row))
    if not domain:
        return False
    flat = domain.replace(" ", "")
    words = [w for w in core.split() if len(w) >= 3]
    return not (core.replace(" ", "") in flat or any(w in flat for w in words))


# Unambiguous rejection sentences. In a full body they beat interview/test words: a rejection may ask for feedback on
# "your interview experience" and would otherwise count as an interview invite.
STRONG_REJECTION = re.compile(
    r"(decided to (proceed|move forward|continue|go ahead) with (an)?other|chosen to (proceed|move forward|continue) with (an)?other|"
    r"not to (move|proceed|take your application) (forward|further)|regret to inform|(have|has) not been successful|"
    r"(isn.t|is not) the right (match|fit)|won.?t be (progressing|moving forward)|will not be (progressing|moving forward)|"
    r"(isn.t|is not|not) progressing further|position has (now )?been filled|not (been )?selected (for|to)|"
    r"not to take your application further|will not be considering you|"
    r"(unable|not able|won.?t be able) to invite you to (the )?next (stage|phase)|"
    r"decided to move forward with candidates whose|nie możemy zaproponować|zdecydowaliśmy się (kontynuować|zaprosić)|"
    r"wybraliśmy (innych|inną|innego))", re.I)
# "If this particular role isn't the right fit, keep an eye on our careers page" (a confirmation) is not one.
CONDITIONAL = re.compile(r"\b(if|in case|should|unless|whether|jeśli|jeżeli|w przypadku)\b", re.I)
# With a full body the bare word "interview" is too weak: confirmations describe the process ("a recruiter will
# contact you to schedule an interview"). An invitation needs invitation wording.
INTERVIEW_INVITE = re.compile(
    r"(invite you (to|for) (an? |the |our )?(\w+ )?(interview|call|meeting|chat|conversation)|invitation (to|for) (an? )?"
    r"(\w+ )?interview|would (like|love) to (invite|schedule|meet|talk|speak|have a (call|chat))|"
    r"schedule (a|an|your) (\w+ )?(interview|call|chat|meeting)|book (a|your) (slot|time)|calendly|"
    r"interview (is |has been )?(scheduled|confirmed)|zaproszenie na rozmow|zapraszamy (cię |pana )?na rozmow|"
    r"choose (a|your) (slot|time))", re.I)


# "If you qualify, a recruiter will contact you to schedule an interview" describes the process, it is no invite.
FUTURE_OR_IF = re.compile(r"\b(if|in case|should|once|when|will|may|might|could)\b", re.I)


def interview_invite(text: str) -> bool:
    for m in INTERVIEW_INVITE.finditer(text):
        if not FUTURE_OR_IF.search(text[max(0, m.start() - 70):m.start()]):
            return True
    return False


def strong_rejection(text: str) -> bool:
    for m in STRONG_REJECTION.finditer(text):
        if not CONDITIONAL.search(text[max(0, m.start() - 45):m.start()]):
            return True
    return False


def classify(subject: str, snippet: str, body: str = "") -> str:
    """Kind of a job email. Without a body: first KINDS match on subject + snippet. With the newest message's body
    (mail-check rows): alerts and generic rejection words only count near the top (footers say "new offers",
    confirmations say "in case other candidates are a better match"), strong rejection sentences win, and an
    interview needs invitation wording or "interview" in the subject."""
    if not body:
        text = f"{subject} {snippet}".replace("​", "")
        for kind, pattern in KINDS:
            if re.search(pattern, text, re.I):
                return kind
        return "other"
    subject = subject.replace("​", "")
    body = body.replace("​", "")
    full, head = f"{subject} {body}", f"{subject} {body[:300]}"
    patterns = dict(KINDS)
    # Strong rejections beat the alert check: a rejection may invite you to "our email job alert service" in its
    # second sentence and must not be marked as noise.
    if strong_rejection(full) and not re.search(patterns["offer"], full, re.I):
        return "rejection"
    if re.search(patterns["alert"], head, re.I):
        return "alert"
    if re.search(patterns["offer"], full, re.I):
        return "offer"
    if interview_invite(full) or re.search(patterns["interview"], subject, re.I):
        return "interview"
    if re.search(patterns["test"], f"{subject} {body[:600]}", re.I):
        return "test"
    if re.search(patterns["rejection"], head, re.I):
        return "rejection"
    for kind in ("status", "applied", "account"):
        if re.search(patterns[kind], full, re.I):
            return kind
    return "other"


def parse_gmail_date(value: str) -> str:
    value = (value or "").replace(" ", " ").replace("\xa0", " ").strip()
    for fmt in ("%a, %b %d, %Y, %I:%M %p", "%a, %d %b %Y, %H:%M", "%b %d, %Y, %I:%M %p"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def connect_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if "body" not in {r[1] for r in conn.execute("PRAGMA table_info(app_history)")}:
        conn.execute("ALTER TABLE app_history ADD COLUMN body TEXT")


def reclassify(conn: sqlite3.Connection) -> int:
    """Re-run classify() on every stored row (after the rules change)."""
    connect_schema(conn)
    n = 0
    for row in conn.execute("SELECT id, subject, snippet, body, kind FROM app_history").fetchall():
        kind = classify(row["subject"], row["snippet"], row["body"] or "")
        if kind != row["kind"]:
            conn.execute("UPDATE app_history SET kind = ? WHERE id = ?", (kind, row["id"]))
            n += 1
    conn.commit()
    return n


def import_rows(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    connect_schema(conn)
    added = 0
    for r in rows:
        subject = (r.get("subject") or "").replace("​", "").strip()
        snippet = (r.get("snippet") or "").replace("​", "").strip()
        body = (r.get("body") or "").strip()
        key = (r.get("thread_id") or "", parse_gmail_date(r.get("date", "")), subject)
        cur = conn.execute(
            "INSERT OR IGNORE INTO app_history (thread_id, msg_date, sender_name, sender_email, subject, snippet, "
            "kind, body, imported_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (*key[:2], r.get("sender_name", ""), (r.get("sender_email") or "").lower(), subject, snippet,
             classify(subject, snippet, body), body or None, now()),
        )
        if not cur.rowcount and body:  # seen before without its body (list-only import): add it now
            conn.execute("UPDATE app_history SET body = ? WHERE thread_id = ? AND msg_date = ? AND subject = ? "
                         "AND body IS NULL", (body, *key))
        added += cur.rowcount
    conn.commit()
    reclassify(conn)
    return {"rows": len(rows), "added": added}


def _haystack(row: sqlite3.Row) -> str:
    domain = (row["sender_email"] or "").split("@")[-1]
    domain_part = "" if any(p in domain for p in PLATFORM_DOMAINS) else domain.split(".")[0]
    name = row["sender_name"] or ""
    name = "" if GENERIC_SENDERS.match(fold(name)) or "@" in name else name
    return f" {fold(name)} {fold(row['subject'])} {fold(row['snippet'])} {fold(domain_part)} "


ROLE_NOISE_WORDS = (r"m|k|f|x|d|w|n|mfd|fmx|mfx|she|he|they|her|him|entry level|fqa|remote|hybrid|full time|"
                    r"part time|b2b|uop")


@lru_cache(maxsize=4)
def _role_noise_for(s: settings.Settings) -> re.Pattern:
    cities, countries, prefixes = _places_for(s)
    alts = [re.escape(w) for w in sorted(cities | countries, key=len, reverse=True)]
    alts += [rf"{re.escape(p)}\w*" for p in prefixes]
    return re.compile(rf"\b({ROLE_NOISE_WORDS}{''.join('|' + a for a in alts)})\b")


def _role_noise() -> re.Pattern:
    """Gender tags, work modes and the search's places ('Tester (f/m), <city> / hybrid') are no part of a role."""
    return _role_noise_for(settings.get())
ROLE_STOP = {"and", "or", "the", "a", "an", "of", "for", "in", "with", "to", "at", "on", "team", "role", "position",
             "job", "pl", "eng"}


# The email talks about a specific position ("for the position X", "the X role", "applying to X at Y").
ROLE_MENTION = re.compile(r"(for the (position|role) (of )?\w|\b(position|role|stanowisko)\s*[:\-]|"
                          r"apply(ing)? (for|to) (the )?[A-Z]\w+|\b\w+ (role|position) (at|with)\b)")
SENIORITY = {"junior", "senior", "mid", "regular", "lead", "principal", "staff", "intern", "trainee", "graduate",
             "associate", "head", "chief", "expert"}


def _role_tokens(text: str) -> list[str]:
    t = _role_noise().sub(" ", fold(text))
    return [w for w in t.split() if w not in ROLE_STOP and len(w) > 1]


def _same_role(job_title: str, text: str, fuzzy: bool = True) -> bool:
    """The email names this job title. Both sides are normalized the same way (a subject repeating the exact title
    'Associate Technical Consultant - Entry-Level Consulting' must match even though "entry level" is noise).
    A match = the title's words appear as one run, or >= 80% of them (min. 2) appear."""
    title = _role_tokens(job_title)
    if not title:
        return False
    hay = _role_tokens(text)
    run, joined = " ".join(title), " ".join(hay)
    if run in joined:
        # "Junior Operations Engineer" is another job than "Operations Engineer".
        before = joined[:joined.index(run)].split()[-1:]
        return not (before and before[0] in SENIORITY and before[0] not in title)
    if not fuzzy:
        return False
    have = set(hay)
    hits = sum(1 for w in title if w in have)
    return len(title) >= 2 and hits / len(title) >= 0.8


def title_core(title: str) -> str:
    """The role name without brackets and trailing qualifiers: 'AI Engineer (LLM automation, agentic coding),
    part-time, remote' -> 'AI Engineer'. Rejections often name only that."""
    t = re.sub(r"\([^)]*\)", " ", title)
    return re.split(r"\s*[,|]\s*|\s+[-–—]\s+", t.strip())[0].strip()


def core_role(job_title: str, text: str) -> bool:
    """The email names the title's core (title_core) as whole words, and the core has 2+ words: "AI Engineer" must not
    match inside "GenAI Engineer", nor "Junior AI Engineer"."""
    core = _role_tokens(title_core(job_title))
    if len(core) < 2 or core == _role_tokens(job_title):
        return False
    run, joined = f" {' '.join(core)} ", f" {' '.join(_role_tokens(text))} "
    if run not in joined:
        return False
    before = joined[:joined.index(run)].split()[-1:]
    return not (before and before[0] in SENIORITY and before[0] not in core)


def for_company(conn: sqlite3.Connection, company: str, title: str = "") -> list[dict]:
    connect_schema(conn)
    core = company_core(company)
    if len(core) < 3:
        return []
    brand = brand_word(company)
    names = [re.escape(core)] + ([re.escape(brand)] if brand else [])
    pattern = re.compile(rf"\b({'|'.join(names)})\b")
    core_pattern = re.compile(rf"\b{re.escape(core)}\b")
    # Companies named like an ATS (Workday, Greenhouse, Lever...) appear in every email sent through that ATS;
    # for them only the sender's own domain (e.g. @workday.com) counts.
    platform_named = any(core.replace(" ", "") in p for p in PLATFORM_DOMAINS + ("myworkdayjobs", "workday"))
    hits = []
    for row in conn.execute("SELECT * FROM app_history WHERE kind != 'alert' ORDER BY msg_date DESC"):
        if platform_named:
            domain = (row["sender_email"] or "").split("@")[-1]
            if not re.fullmatch(rf"([a-z0-9-]+\.)*{re.escape(core.replace(' ', ''))}\.[a-z.]+", domain) or "myworkday" in domain:
                continue
            if not pattern.search(f" {fold(row['subject'])} "):
                continue  # e.g. "Verify your candidate account" sent by workday.com for some other employer
            text = _haystack(row)
        else:
            text = _haystack(row)
            if pattern.search(text) and _other_company_sender(row, core):
                continue
            if brand and not core_pattern.search(text) and pattern.search(text) and not _brand_hit_ok(row, company, brand, text):
                continue
        if platform_named or pattern.search(text):
            role_text = f"{row['subject']} {row['snippet']} {(row['body'] or '')[:600]}"
            same = bool(title) and _same_role(title, role_text)
            hits.append({"date": row["msg_date"], "kind": row["kind"], "subject": row["subject"], "same_role": same,
                         "names_other_role": not same and bool(ROLE_MENTION.search(role_text)),
                         "core_role": not same and bool(title) and core_role(title, role_text),
                         "role_text": role_text})
    return hits


def summarize(hits: list[dict]) -> tuple[str, bool]:
    """Short note + whether it should block/warn strongly (same role applied, or test/interview pending)."""
    if not hits:
        return "", False
    parts, strong = [], False
    for h in hits[:4]:
        role = " (same role)" if h["same_role"] else ""
        parts.append(f"{h['kind']} {h['date']}: {h['subject'][:60]}{role}")
        if h["same_role"] or h["kind"] in ("test", "interview", "offer"):
            strong = True
    more = f" (+{len(hits) - 4} more)" if len(hits) > 4 else ""
    return " | ".join(parts) + more, strong


# An application confirmation this many days older than the posting belongs to an earlier opening of the same title.
REPOST_DAYS = 60


def _days_before(day: str, days: int) -> str:
    return (datetime.fromisoformat(day) - timedelta(days=days)).date().isoformat()


def sync_applied(conn: sqlite3.Connection, statuses: tuple[str, ...] = ("new", "scored", "shortlisted")) -> dict:
    """A job still listed as new/scored/shortlisted whose Gmail history names the same role as an application
    confirmation was applied to (e.g. applied by hand, outside /apply). The job becomes applied, with an application
    row so later rejections sync. Confirmations that name no role are only listed in `review`: with several open jobs
    at one company we cannot tell which one was applied to."""
    connect_schema(conn)
    rows = conn.execute(f"SELECT id, company, title, posted_at, scraped_at FROM jobs "
                        f"WHERE status IN ({','.join('?' * len(statuses))})", statuses).fetchall()
    synced, review = [], []
    for r in rows:
        hits = [h for h in for_company(conn, r["company"], r["title"]) if h["kind"] == "applied"]
        # Exact title only: a fuzzy match would count a "Mid Backend Engineer" posting as the "Junior Backend
        # Engineer" application at the same company.
        same = [h for h in hits if h["same_role"] and _same_role(r["title"], h["role_text"], fuzzy=False)]
        # A confirmation from long before the posting appeared was for an earlier opening of the same title; marking
        # the new posting "applied" would hide it from scoring. Those go to review.
        seen_at = (r["posted_at"] or r["scraped_at"] or "")[:10]
        if same and seen_at and max(h["date"] for h in same) < _days_before(seen_at, REPOST_DAYS):
            review.append({"id": r["id"], "company": r["company"], "title": r["title"],
                           "mails": [f"{h['date']}: {h['subject'][:80]} (older application, re-posting?)"
                                     for h in same[:3]]})
            continue
        if same:
            first = min(same, key=lambda h: h["date"])
            conn.execute("UPDATE jobs SET status = 'applied' WHERE id = ?", (r["id"],))
            conn.execute("INSERT INTO applications (job_id, ats, applied_at, answers, cover_letter, notes) "
                         "VALUES (?, 'gmail-history', ?, '{}', '', ?)",
                         (r["id"], first["date"], f"synced from Gmail history: {first['subject'][:120]}"))
            synced.append({"id": r["id"], "company": r["company"], "title": r["title"], "date": first["date"]})
        elif hits:
            review.append({"id": r["id"], "company": r["company"], "title": r["title"],
                           "mails": [f"{h['date']}: {h['subject'][:80]}" for h in hits[:3]]})
    conn.commit()
    return {"synced": synced, "review": review}


def annotate(conn: sqlite3.Connection, statuses: tuple[str, ...] = ("new", "scored", "shortlisted")) -> dict:
    connect_schema(conn)
    applied = sync_applied(conn, statuses)
    rows = conn.execute(f"SELECT id, company, title FROM jobs WHERE status IN ({','.join('?' * len(statuses))})",
                        statuses).fetchall()
    flagged = []
    for r in rows:
        note, strong = summarize(for_company(conn, r["company"], r["title"]))
        conn.execute("UPDATE jobs SET history_note = ?, history_warn = ? WHERE id = ?", (note or None, int(strong), r["id"]))
        if note:
            flagged.append({"id": r["id"], "company": r["company"], "title": r["title"], "warn": strong, "note": note})
    conn.commit()
    return {"checked": len(rows), "with_history": len(flagged), "flagged": flagged,
            "applied_synced": applied["synced"], "applied_review": applied["review"]}


def counts(conn: sqlite3.Connection) -> dict:
    connect_schema(conn)
    return {r[0]: r[1] for r in conn.execute("SELECT kind, COUNT(*) FROM app_history GROUP BY kind ORDER BY 2 DESC")}


def load_rows(path) -> list[dict]:
    data = json.loads(open(path).read())
    return data if isinstance(data, list) else data.get("rows", [])
