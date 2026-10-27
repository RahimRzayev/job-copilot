"""Build one-shot Playwright fill scripts for common ATS forms.

`jobhunt formfill smartrecruiters 533 --message-file msg.txt` writes data/inbox/fill-smartrecruiters-533.js:
the template from browser/ats/<ats>.js with a DATA object prepended. Claude runs it with Playwright MCP's
browser_run_code_unsafe(filename=...). Templates fill everything they know, NEVER click the final submit,
and return what they did plus any questions left for Claude/the user.

Every value in DATA comes from profile/profile.yaml (schema: profile/profile.example.yaml); nothing about the user is
written here.
"""

import json
import re
from datetime import date
from pathlib import Path

import yaml

from . import settings
from .prefilter import COMPANY_BOARDS
from .db import ROOT
from .jobs import get

TEMPLATES = ROOT / "browser" / "ats"
OUT = ROOT / "data" / "inbox"
CVS = OUT / "cvs"  # per-job tailored CVs: CVS/<job id>/<base CV file name>
PROFILE = settings.PROFILE

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
CEFR = ("A1", "A2", "B1", "B2", "C1", "C2")
# "How did you hear about us" per job source; company career sites are the employer's own website.
SOURCE_LABELS = {"linkedin": "LinkedIn"}
# ITU country calling codes with two digits; 1 and 7 have one digit, every other code has three.
TWO_DIGIT_CODES = {"20", "27", "30", "31", "32", "33", "34", "36", "39", "40", "41", "43", "44", "45", "46", "47", "48",
                   "49", "51", "52", "53", "54", "55", "56", "57", "58", "60", "61", "62", "63", "64", "65", "66", "81",
                   "82", "84", "86", "90", "91", "92", "93", "94", "95", "98"}
# Names phone-code pickers show for common prefixes; any other prefix falls back to the profile's country.
PHONE_COUNTRIES = {
    "+1": "United States", "+20": "Egypt", "+30": "Greece", "+31": "Netherlands", "+32": "Belgium", "+33": "France",
    "+34": "Spain", "+36": "Hungary", "+39": "Italy", "+40": "Romania", "+41": "Switzerland", "+43": "Austria",
    "+44": "United Kingdom", "+45": "Denmark", "+46": "Sweden", "+47": "Norway", "+48": "Poland", "+49": "Germany",
    "+52": "Mexico", "+55": "Brazil", "+61": "Australia", "+62": "Indonesia", "+63": "Philippines", "+81": "Japan",
    "+82": "South Korea", "+84": "Vietnam", "+86": "China", "+90": "Turkey", "+91": "India", "+92": "Pakistan",
    "+98": "Iran", "+212": "Morocco", "+234": "Nigeria", "+351": "Portugal", "+352": "Luxembourg", "+353": "Ireland",
    "+354": "Iceland", "+356": "Malta", "+357": "Cyprus", "+358": "Finland", "+359": "Bulgaria", "+370": "Lithuania",
    "+371": "Latvia", "+372": "Estonia", "+373": "Moldova", "+374": "Armenia", "+375": "Belarus", "+380": "Ukraine",
    "+381": "Serbia", "+385": "Croatia", "+386": "Slovenia", "+387": "Bosnia and Herzegovina", "+420": "Czech Republic",
    "+421": "Slovakia", "+880": "Bangladesh", "+962": "Jordan", "+966": "Saudi Arabia", "+971": "United Arab Emirates",
    "+972": "Israel", "+992": "Tajikistan", "+993": "Turkmenistan", "+994": "Azerbaijan", "+995": "Georgia",
    "+996": "Kyrgyzstan", "+998": "Uzbekistan",
}


def load_profile(path: Path | None = None) -> dict:
    p = Path(path or PROFILE)
    if not p.exists():
        raise SystemExit(f"{p} is missing: run `uv run jobhunt init`, then /setup in Claude Code.")
    return yaml.safe_load(p.read_text()) or {}


def _abs(rel: str) -> Path:
    p = Path(rel).expanduser()
    return p if p.is_absolute() else ROOT / p


def cv_variants(profile: dict | None = None) -> list[str]:
    person = (profile if profile is not None else load_profile()).get("personal") or {}
    return ["main", *(person.get("cv_variants") or {})]


def cv_path(variant: str = "main", profile: dict | None = None) -> Path:
    """The PDF of a CV variant: personal.cv_file for "main", personal.cv_variants[<variant>] otherwise."""
    profile = profile if profile is not None else load_profile()
    person = profile.get("personal") or {}
    rel = person.get("cv_file") if variant == "main" else (person.get("cv_variants") or {}).get(variant)
    if not rel:
        raise SystemExit(f"no CV {variant!r} in the profile; known: {', '.join(cv_variants(profile))}")
    return _abs(rel)


CURRENT = ("present", "now", "current", "ongoing")


def _ym(value) -> tuple[int | None, int | None]:
    """'2024-06' -> (2024, 6); a bare year -> (2024, None): the month is never guessed. 'present' -> this month;
    empty -> (None, None)."""
    s = str(value or "").strip()
    if not s:
        return None, None
    if s.lower() in CURRENT:
        today = date.today()
        return today.year, today.month
    if "-" in s:
        y, m = s.split("-")[:2]
        return int(y), int(m)
    return int(s), None


def _month_name(month: int | None) -> str:
    return MONTHS[month - 1] if month else ""


def studying(edu: dict) -> bool | None:
    """Still studying: the profile's `status` when set, else whether expected_graduation is still ahead. None when
    neither says (forms then ask instead of a guess)."""
    status = str(edu.get("status") or "").strip().lower()
    if status:
        return status in ("in progress", "studying", "current")
    gy, gm = _ym(edu.get("expected_graduation"))
    if gy is None:
        return None
    today = date.today()
    return (gy, gm or 12) >= (today.year, today.month)


def _month_year(value) -> str:
    """'2030-06' -> 'June 2030'; a bare year stays as it is."""
    s = str(value or "")
    return f"{MONTHS[int(s[5:7]) - 1]} {s[:4]}" if re.fullmatch(r"\d{4}-\d\d(-\d\d)?", s) else s


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def degree_level(degree: str) -> str:
    """'Bachelor of Science' -> 'Bachelor': the word degree dropdowns list. The profile's `education.degree_level`
    wins when a degree doesn't map cleanly (an engineer's degree is a bachelor in some countries, a master in others)."""
    d = (degree or "").lower()
    for level, pattern in (("PhD", r"ph\.?d|doctor"),
                           ("Master", r"master|\bm\.?sc|\bm\.?a\b|magist|\bmba\b|diplom|dipl\.|ingénieur"),
                           ("Bachelor", r"bachelor|\bb\.?sc|\bb\.?a\b|\bb\.?eng|licenc|inżynier|engineer")):
        if re.search(pattern, d):
            return level
    return degree or ""


def _gpa(edu: dict) -> tuple[str, str]:
    return str(edu.get("gpa") or "").strip(), str(edu.get("gpa_scale") or "").strip()


def degree_text(edu: dict) -> str:
    """'Bachelor of Science (4th year, expected June 2026, GPA 4.5/5.0)' from the profile's education entry."""
    parts = []
    grad = _month_year(edu.get("expected_graduation"))
    if studying(edu):
        if edu.get("year_of_study"):
            parts.append(f"{_ordinal(int(edu['year_of_study']))} year")
        if grad:
            parts.append(f"expected {grad}")
    elif grad:
        parts.append(f"completed {grad}")
    gpa, scale = _gpa(edu)
    if gpa:
        parts.append(f"GPA {gpa}/{scale}" if scale else f"GPA {gpa}")
    degree = edu.get("degree") or ""
    return f"{degree} ({', '.join(parts)})" if parts else degree


def languages(profile: dict) -> list[dict]:
    """profile `languages` {english: C1, spanish: native} -> [{name, cefr, fluent, native}], strongest first.
    'native' counts as C2; C1 and above count as fluent."""
    out = []
    for name, level in (profile.get("languages") or {}).items():
        text = str(level or "").strip()
        native = bool(re.search(r"native|mother|bilingual", text, re.I))
        found = re.search(r"\b([ABC][12])\b", text.upper())
        cefr = "C2" if native else found.group(1) if found else ""
        if not cefr:
            continue  # no level given: the form asks instead of a guess
        out.append({"name": str(name).strip().title(), "cefr": cefr, "fluent": native or cefr in ("C1", "C2"),
                    "native": native})
    return sorted(out, key=lambda lang: (not lang["native"], -CEFR.index(lang["cefr"])))


def skills(profile: dict) -> list[str]:
    """Skill names for skill-tag fields: strong ones first, then working; basic ones are left out."""
    items = [s for s in profile.get("skills") or [] if isinstance(s, dict) and s.get("name")]
    return [s["name"] for level in ("strong", "working") for s in items if s.get("level") == level]


def phone_parts(phone: str) -> tuple[str, str, str]:
    """'+48 500 600 700' -> ('+48', '500600700', '+48500600700'). No international prefix -> ('', number, number)."""
    full = re.sub(r"[\s\-().]", "", phone or "")
    if full.startswith("00"):
        full = "+" + full[2:]
    if not full.startswith("+"):
        return "", full, full
    digits = full[1:]
    n = 1 if digits[:1] in ("1", "7") else 2 if digits[:2] in TWO_DIGIT_CODES else 3
    return "+" + digits[:n], digits[n:], full


def resume_for(job: dict, cv: str = "main", profile: dict | None = None) -> Path:
    """A per-job tailored copy (jobhunt cv build) wins over the base CV."""
    base = cv_path(cv, profile)
    tailored = CVS / str(job.get("id")) / base.name
    return tailored if job.get("id") and tailored.exists() else base


def build_data(job: dict, message: str = "", source_label: str = "", cv: str = "main") -> dict:
    """`cv` picks the CV: "main" (personal.cv_file) or a personal.cv_variants key. One CV per application."""
    from . import cover

    p = load_profile()
    person = p.get("personal") or {}
    addr = person.get("address") or {}
    edu = (p.get("education") or [{}])[0] or {}
    exp = (p.get("experience") or [None])[0]
    # Multi-word given names: personal.first_name / last_name win over splitting full_name at the first space.
    first, _, last = (person.get("full_name") or "").strip().partition(" ")
    first, last = person.get("first_name") or first, person.get("last_name") or last
    phone_country, phone_local, phone_full = phone_parts(person.get("phone", ""))
    city = person.get("city") or addr.get("city") or ""
    # Where the user lives (forms ask "country of residence"); the search country only when the profile has none.
    country = person.get("country") or settings.get().country or ""
    ey, em = _ym(edu.get("start"))
    gy, gm = _ym(edu.get("expected_graduation"))
    gpa, scale = _gpa(edu)
    cover_pdf = cover.path_for(job["id"])
    data = {
        "first_name": first, "last_name": last.strip(), "full_name": person.get("full_name", ""),
        "email": settings.get().job_email or person.get("email", ""),  # the inbox job replies go to
        "phone_country": phone_country, "phone_local": phone_local, "phone_full": phone_full,
        "phone_country_name": PHONE_COUNTRIES.get(phone_country, ""),
        # Location comboboxes: type city_search, pick the option naming city_option (and the country).
        "city_search": city, "city_option": city, "country": country,
        # Street-type prefixes ("ul.") are left out: forms label the field "Street" already.
        "address": {"street": re.sub(r"^(ul\.|ulica)\s*", "", addr.get("street") or "", flags=re.I),
                    "house_number": str(addr.get("house_number") or ""), "apartment": str(addr.get("apartment") or ""),
                    "postal_code": str(addr.get("postal_code") or ""), "city": addr.get("city") or city,
                    "region": addr.get("region") or "", "country": addr.get("country") or country},
        "linkedin": person.get("linkedin", ""), "website": person.get("portfolio") or person.get("github") or "",
        "resume_path": str(resume_for(job, cv, p)),
        "education": {
            "institution": edu.get("university", ""),
            "institution_aliases": [edu.get("university", ""), edu.get("former_name") or ""],
            "degree": edu.get("degree", ""), "major": edu.get("major", ""), "degree_text": degree_text(edu),
            "degree_level": edu.get("degree_level") or degree_level(edu.get("degree", "")),
            "gpa": f"{gpa} / {scale}" if gpa and scale else gpa, "gpa_scale": scale,
            "year_of_study": edu.get("year_of_study"),
            "start_year": ey, "start_month": em, "start_month_name": _month_name(em),
            "end_year": gy, "end_month": gm, "end_month_name": _month_name(gm),
            "current": studying(edu),
        },
        "experience": None,
        # Companies the user worked for: "have you worked for us before?" is No only when the job's company is not here.
        "past_employers": [x.get("company") for x in p.get("experience") or [] if x and x.get("company")],
        "languages": languages(p),
        "skills": skills(p),
        "message": message.replace(";", ","),  # some ATS message boxes reject ';'
        "source_label": source_label or SOURCE_LABELS.get(job.get("source"))
        or ("Company website" if job.get("source") in COMPANY_BOARDS else "Job board"),
        "job": {"id": job["id"], "title": job.get("title"), "company": job.get("company")},
        # Per-job PDF from `jobhunt cover <id>`; templates attach it where the form has a cover-letter upload.
        "cover_letter_path": str(cover_pdf) if cover_pdf.exists() else "",
    }
    if exp:
        current = str(exp.get("end") or "").strip().lower() in ("", *CURRENT)
        sy, sm = _ym(exp.get("start"))
        ty, tm = (None, None) if current else _ym(exp.get("end"))
        place = (exp.get("city") or "").split(",")[0].strip()
        data["experience"] = {
            "title": exp.get("title", "").split(" (")[0], "title_full": exp.get("title", ""),
            "company": exp.get("company", ""), "location_search": place, "location_option_prefix": place,
            "description": "\n".join(f"- {b}" for b in exp.get("bullets") or []).replace(";", ","),
            "start_year": sy, "start_month": sm, "start_month_name": _month_name(sm),
            "end_year": ty, "end_month": tm, "end_month_name": _month_name(tm),
            "current": current,
        }
    return data


def write_script(conn, ats: str, job_id: int, message: str = "", source_label: str = "", cv: str = "main") -> Path:
    template = TEMPLATES / f"{ats}.js"
    if not template.exists():
        raise SystemExit(f"no template for {ats}; available: {sorted(t.stem for t in TEMPLATES.glob('*.js'))}")
    data = build_data(get(conn, job_id), message, source_label, cv)
    body = template.read_text()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"fill-{ats}-{job_id}.js"
    path.write_text(body.replace("/*__DATA__*/", "const DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";"))
    return path
