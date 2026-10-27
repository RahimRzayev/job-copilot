"""Per-job cover letter as a PDF (a .txt attachment looks unprofessional).

Claude writes the letter body for each job (structure in the /apply skill: opening for this role, 2-3 matching
projects/skills, honest gaps, availability), saves it as text, then `jobhunt cover <id> --text-file f` renders
data/inbox/covers/<id>/<First>_<Last>_Cover_Letter.pdf with a header from profile.yaml. Fill scripts pick the PDF
up automatically (formfill DATA.cover_letter_path); the same text goes into "message to the hiring team" boxes.
"""

import os
import re
import unicodedata
from datetime import date
from pathlib import Path

import yaml
from fpdf import FPDF

from . import formfill, settings
from .db import ROOT
from .jobs import get

OUT = ROOT / "data" / "inbox" / "covers"
_WINDIR = os.environ.get("WINDIR", "C:/Windows")
# (regular, bold) TrueType pairs, first found wins: a TTF covers letters outside Latin-1 (ő, ș, ž); fpdf's built-in
# core fonts are Latin-1 only and are the last resort.
FONTS = [
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    (f"{_WINDIR}/Fonts/arial.ttf", f"{_WINDIR}/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
     "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf"),
]
_LATIN1 = str.maketrans({"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", "•": "-"})


# Letters that don't decompose into a base letter + accent.
_TRANSLIT = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe",
                           "Œ": "OE", "đ": "d", "Đ": "D", "ð": "d", "þ": "th", "ı": "i", "ə": "e", "Ə": "E"})


def _ascii(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(_TRANSLIT))
    return "".join(c for c in text if not unicodedata.combining(c))


def filename(profile: dict | None = None) -> str:
    """'<First>_<Last>_Cover_Letter.pdf' from the profile name: the file name recruiters see in the ATS."""
    try:
        name = ((profile if profile is not None else formfill.load_profile()).get("personal") or {}).get("full_name")
    except SystemExit:
        name = ""
    stem = re.sub(r"[^A-Za-z0-9]+", "_", _ascii(name or "")).strip("_")
    return f"{stem}_Cover_Letter.pdf" if stem else "Cover_Letter.pdf"


def path_for(job_id: int) -> Path:
    return OUT / str(job_id) / filename()


def font_pair() -> tuple[str, str] | None:
    return next(((r, b) for r, b in FONTS if Path(r).exists() and Path(b).exists()), None)


def _latin1(text: str) -> str:
    """For the core fonts (Latin-1 only): other letters lose their accents (ł → l) instead of turning into '?'."""
    text = "".join(c if ord(c) < 256 else _ascii(c) for c in text.translate(_LATIN1))
    return text.encode("latin-1", "replace").decode("latin-1")


def header_lines(person: dict) -> list[str]:
    """Contact lines under the name, with the same email and country the application forms get: where the user
    lives, the job inbox and phone, then the profile links."""
    s = settings.get()
    place = ", ".join(x for x in (person.get("city") or (person.get("address") or {}).get("city"),
                                  person.get("country") or s.country) if x)
    links = [(person.get(k) or "").removeprefix("https://") for k in ("linkedin", "github", "portfolio")]
    return ["  |  ".join(c for c in line if c)
            for line in ([place, s.job_email or person.get("email", ""), person.get("phone", "")], links)]


def render(conn, job_id: int, body: str) -> Path:
    job = get(conn, job_id)
    person = formfill.load_profile().get("personal") or {}
    pdf = FPDF(format="A4")
    pdf.set_margins(22, 20, 22)
    pdf.set_auto_page_break(True, 20)
    fonts = font_pair()
    if fonts:
        pdf.add_font("Body", "", fonts[0])
        pdf.add_font("Body", "B", fonts[1])
        family, clean = "Body", str
    else:
        family, clean = "Helvetica", _latin1
    pdf.add_page()

    pdf.set_font(family, "B", 16)
    pdf.cell(0, 8, clean(person.get("full_name", "")), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "", 9.5)
    pdf.set_text_color(90, 90, 90)
    for line in header_lines(person):
        pdf.cell(0, 5, clean(line), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(family, "", 10.5)
    pdf.cell(0, 6, date.today().strftime("%d %B %Y"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font(family, "B", 10.5)
    pdf.multi_cell(0, 6, clean(f"Application: {job['title']} – {job['company']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font(family, "", 10.5)
    for para in [p.strip() for p in body.strip().split("\n\n") if p.strip()]:
        pdf.multi_cell(0, 5.6, clean(para), align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    path = path_for(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    path.with_suffix(".txt").write_text(body.strip() + "\n")
    return path


# ---------- claim check ----------
# Drafts can claim things the profile does not say ("I work in VS Code", "deploy and monitor"). Before a letter is
# rendered, every tool/proper-noun-like term and every number outside "gap" sentences must appear in profile.yaml;
# the rest is listed as `unverified` for Claude to fix or confirm with the user.
GAP_SENTENCE = re.compile(
    r"(\bnot\b|n't|\bno\b (commercial|professional|experience)|\bnever\b|\byet\b|\bgap|transparent|learn|"
    r"would (be glad|like)|\bwithout\b)", re.I)
TERM = re.compile(r"(?<![\w.])(?:\.?[A-Z][\w#+.\-/]*[A-Za-z0-9#+]|[A-Za-z]+(?:\.js|#|\+\+)|\d+(?:[.,]\d+)?\+?)(?![\w])")
ALWAYS_OK = {"I", "Dear", "Kind", "Regards", "Hello", "Hi", "Thank", "Thanks", "My", "In", "As", "The",
             "To", "It", "This", "That", "Your", "We", "You", "Both", "Since", "With", "At", "For", "And", "Also",
             "During", "While", "Our", "If", "One", "Two", "AI",
             # generic formats/abbreviations that describe no tool claim
             "JSON", "XML", "CSV", "API", "APIs", "URL", "URLs", "CV", "CVs", "PDF", "UI", "UX", "IT"}
# The closing line; everything after it (name, contacts) is the signature, not a claim.
SIGN_OFF = re.compile(r"((kind|best|warm|warmest) )?regards,?|(yours )?(sincerely|faithfully|truly),?|"
                      r"best( wishes)?,?|thanks?,?|thank you,?", re.I)
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")


def _place_words() -> set[str]:
    """The search city and country as letters write them ('Lisbon', 'Portugal'): where the user lives is no claim."""
    s = settings.get()
    names = (s.city, s.country, *s.city_names, *s.country_names)
    return {w.title() for n in names for w in re.findall(r"[^\W\d_]+", n or "")}


def _profile_months(known: str) -> set[str]:
    """Month names behind the profile's ISO dates: 'expected_graduation: "2030-06"' allows 'June'."""
    return {MONTHS[int(m) - 1] for m in re.findall(r"\b20\d\d-(0[1-9]|1[0-2])\b", known)}


def _sentences(body: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", body) if s.strip()]


def check_claims(body: str, job: dict | None = None, profile_text: str | None = None,
                 confirmed: set[str] | None = None) -> list[dict]:
    """Terms in the letter that the profile does not back up (sentences about gaps are skipped). `confirmed` are skills
    the user answered yes to in the CV fact store (cv fact-save); they count as backed up even when profile.yaml does
    not list them."""
    if profile_text is None:
        profile_text = formfill.PROFILE.read_text() if formfill.PROFILE.exists() else ""
    known = profile_text.lower() + "\n" + "\n".join(sorted(confirmed or ()))
    job = job or {}
    context = f"{job.get('company', '')} {job.get('title', '')} {job.get('description', '')}".lower()
    ok_words = ALWAYS_OK | _place_words()
    out, seen = [], set()
    months = _profile_months(known)
    signed = False
    for sent in _sentences(body):
        if signed or SIGN_OFF.fullmatch(sent):
            signed = True
            continue
        if GAP_SENTENCE.search(sent) or sent.startswith(("Dear", "Kind regards")):
            continue
        for i, m in enumerate(TERM.finditer(sent)):
            whole = m.group(0).rstrip(".")
            if m.start() == 0 and not re.search(r"[A-Z].*[A-Z]|\d|[.#+]", whole[1:]):
                continue  # ordinary capitalized first word of the sentence
            for term in re.sub(r"-(based|driven|powered|related|first|only|backed|enabled|assisted)$", "", whole).split("/"):
                low = term.lower()
                if not term or term in ok_words or low in seen or low in months:
                    continue
                # plural of something the profile knows ("endpoints", "APIs" style)
                if low.endswith("s") and len(low) > 3 and (low[:-1] in known or term[:-1] in ok_words):
                    continue
                # Words from the posting (title, company, its tech) are what the letter answers to, not claims
                # about the user, unless the sentence says the user used them — those must be in the profile.
                if low in known or low.replace("-", " ") in known or low in str(job.get("title", "")).lower():
                    continue
                if low in context and not re.search(r"\b(I|my)\b", sent):
                    continue
                if low in context and re.search(rf"(position|role|team|project|program\w*)\W+[^.]*{re.escape(term)}|"
                                                rf"{re.escape(term)}\W+(position|role|team|project)", sent, re.I):
                    continue  # "applying for the Atlas Fullstack Developer position"
                seen.add(low)
                out.append({"term": term, "sentence": sent[:160]})
    return out + _overclaimed_languages(body, profile_text)


FLUENCY = r"(fluen\w*|native|bilingual|proficien\w*|mother tongue)"
# Profile levels that back up "fluent": C1/C2/native, or the wording LinkedIn-style profiles use.
FLUENT_LEVEL = re.compile(r"^\s*(c1|c2)|native|mother tongue|bilingual|fluent|full (professional|working)|"
                          r"professional working", re.I)


def _overclaimed_languages(body: str, profile_text: str) -> list[dict]:
    """"Fluent German" while the profile says german: B1. The term check above lets the word "German" through
    because the profile names the language; the level is what the letter overstates."""
    try:
        langs = (yaml.safe_load(profile_text) or {}).get("languages") or {}
    except (yaml.YAMLError, AttributeError):
        return []
    if not isinstance(langs, dict):
        return []
    weak = {str(k).lower(): str(v or "").strip() for k, v in langs.items() if not FLUENT_LEVEL.search(str(v or ""))}
    out = []
    for sent in _sentences(body):
        if GAP_SENTENCE.search(sent):
            continue
        for lang, level in weak.items():
            if re.search(rf"\b{FLUENCY}\b[^.]{{0,30}}\b{re.escape(lang)}\b|\b{re.escape(lang)}\b[^.]{{0,20}}\b{FLUENCY}",
                         sent, re.I):
                out.append({"term": f"{lang.capitalize()} ({level or 'no level'} in profile)", "sentence": sent[:160]})
    return out
