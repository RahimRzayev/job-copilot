"""Cheap pre-score for every job, so Claude only reads the most promising ones in detail.

Signals (0-100): overlap with the profile's skills (weighted by level), entry-level wording in title/text,
years of experience asked, whether the role family is one the user targets, and a penalty when the title
is built around a stack the user doesn't have. This is a ranking aid, never a final decision.
"""

import re
import sqlite3
from functools import lru_cache

import yaml

from . import settings
from .prefilter import JUNIOR_TITLE, alternation, required_years

PROFILE = settings.PROFILE

LEVEL_WEIGHT = {"strong": 3, "working": 2, "basic": 1}
# How profile skill names may appear in job text. A profile skill matches the first key it names as a whole word
# ("LLM APIs (OpenAI)" -> "llm"); anything else is searched as written.
ALIASES = {
    "python": [r"python"], "django rest framework": [r"\bdrf\b", r"django rest"], "django": [r"django"],
    "typescript": [r"typescript", r"\bts\b"], "javascript": [r"javascript", r"\bjs\b"],
    "react": [r"react"], "next.js": [r"next\.?js"], "node.js": [r"node\.?js", r"\bnode\b"],
    "go": [r"\bgo\b", r"golang"], "rust": [r"\brust\b"], "java": [r"\bjava\b"], "c#": [r"c#", r"\.net\b"],
    "c++": [r"c\+\+"], "postgresql": [r"postgres"], "sql": [r"\bsql\b"], "nosql": [r"nosql", r"mongo"],
    "rest": [r"\brest\b", r"\bapi"], "oauth": [r"\bjwt\b", r"oauth"], "docker": [r"docker", r"container"],
    "kubernetes": [r"kubernetes", r"\bk8s\b"], "linux": [r"linux", r"bash", r"unix"],
    "ci/cd": [r"ci/cd", r"\bci\b", r"github actions", r"gitlab ci", r"jenkins"],
    "aws": [r"\baws\b", r"amazon web services"], "azure": [r"azure"], "gcp": [r"\bgcp\b", r"google cloud"],
    "html": [r"\bhtml"], "css": [r"\bcss\b", r"tailwind"], "playwright": [r"playwright", r"selenium", r"cypress"],
    "git": [r"\bgit\b", r"github", r"gitlab"], "orm": [r"\borm\b", r"sqlalchemy", r"hibernate", r"prisma", r"drizzle"],
    "llm": [r"\bllm", r"genai", r"generative ai", r"openai", r"gemini", r"\brag\b", r"\bai\b"],
    "machine learning": [r"machine learning", r"\bml\b", r"scikit", r"pytorch", r"tensorflow"],
    "power bi": [r"power ?bi"], "excel": [r"excel"], "tableau": [r"tableau"], "pandas": [r"pandas"],
    "it support": [r"helpdesk", r"help desk", r"service desk", r"it support", r"troubleshoot"],
    "networking": [r"network", r"\bvpn\b", r"tcp/ip", r"\blan\b"],
}


def skill_patterns(name: str) -> list[str]:
    """Regexes for a profile skill: its own alias list, the first alias key it names, or the name itself."""
    name = name.lower()
    if name in ALIASES:
        return ALIASES[name]
    for key, pats in ALIASES.items():
        if re.search(rf"(?<![\w+#.]){re.escape(key)}(?![\w+#])", name):
            return pats
    return [re.escape(name)]


ROLE_FAMILY = re.compile(
    r"(python|backend|back-end|full[- ]?stack|frontend|front-end|web|software|developer|engineer|programist|"
    r"devops|cloud|platform|sre|site reliability|data|analyst|analytics|ml|machine learning|\bai\b|"
    r"qa|test|tester|automation|support|helpdesk|service desk|security|soc|cyber|it\b|technolog\w*|tech\b|digital|"
    r"informatyk\w*|programist\w*|administrator|sysadmin)", re.I)
# Stacks a title can be built around: one the user's skills don't cover ranks the job lower.
STACK_TITLE = re.compile(
    r"\b(java|kotlin|\.net|c#|php|salesforce|sap|abap|android|ios|objective-c|scala|ruby|"
    r"embedded c|c\+\+|unity|unreal|dynamics|servicenow|mainframe|cobol|delphi|oracle apex)\b", re.I)
# When one of the user's programming languages is among the top skill hits, the job is usually open to it even if the
# title names another stack ("Java or Python developer").
PROGRAMMING = {"python", "typescript", "javascript", "go", "golang", "rust", "swift", "java", "kotlin", "c#", ".net",
               "php", "scala", "ruby", "c++", "c"}
JUNIOR_TEXT = re.compile(
    r"(no (prior |commercial )?experience (is )?(required|needed)|for (students|graduates)|student status|"
    r"internship|graduate program|trainee|0[-–]1 years?|0[-–]2 years?|up to (1|2) years?|first job|"
    r"entry[- ]level|status studenta|dla studentów|dla studenta|bez doświadczenia|working student|student job|"
    r"part[- ]time.{0,30}students?|students? .{0,30}part[- ]time)", re.I)
# Student and working-student roles get an extra push when they are among settings.levels.
STUDENT_ROLE = re.compile(r"\b(student\w*|working student|werkstudent|dla studenta)\b", re.I)
MID_TEXT = re.compile(r"\b(mid|regular|intermediate|experienced)\b", re.I)
# Titles above entry level: without this, "Middle" and "Senior" roles ranked near the top.
SENIOR_TITLE = re.compile(r"\b(senior|sr|lead|principal|staff|mid[- ]?senior|middle|mid|regular|expert|architect|"
                          r"manager|head of|director)\b", re.I)
SENIOR_LEVELS = {"mid", "mid-senior level", "senior", "director", "executive", "manager"}
# With "mid" in settings.levels, mid-level titles and LinkedIn's "Mid-Senior level" are targets, not penalties.
SENIOR_ONLY_TITLE = re.compile(r"\b(senior|sr|lead|principal|staff|expert|architect|manager|head of|director)\b", re.I)
SENIOR_ONLY_LEVELS = {"senior", "director", "executive", "manager"}
LEVEL_WORDS = {"intern", "internship", "junior", "graduate", "trainee", "student", "working", "entry", "level", "mid",
               "senior", "and", "or", "of", "the"}


@lru_cache(maxsize=8)
def _offsite(s: settings.Settings) -> re.Pattern:
    """Wording that puts a job outside the user's city and remote reach: near-site, "within N hours of <city>",
    relocation, or hybrid/on-site in another city of the country. Without it, a near-site role ranked first."""
    parts = [r"near[- ]?site"]
    if s.city_names:
        parts.append(rf"within \d+ ?(hours?|h) (of|from) ({alternation(s.city_names)})")
    if not s.relocate:
        parts.append(r"relocation|relokacj")
        if s.other_cities:
            city = rf"\b(?:{alternation(s.other_cities)})\w*"
            parts.append(rf"hybrid\b.{{0,60}}{city}|{city}.{{0,40}}\b(on[- ]?site|office)\b")
    return re.compile("(" + "|".join(parts) + ")", re.I)


@lru_cache(maxsize=8)
def _roles(s: settings.Settings) -> re.Pattern | None:
    """Any word of the user's target roles ("data analyst" -> data, analyst) in a title; None without roles. Level
    words don't count: "junior" alone says nothing about the role."""
    words = {w for role in s.roles for w in re.split(r"[\s/,-]+", role) if len(w) > 1} - LEVEL_WORDS
    return re.compile(rf"\b({alternation(sorted(words))})", re.I) if words else None


def years_points(years: int, s: settings.Settings) -> int:
    """0-1 years asked is a plus. Entry-level users: 2 years already costs points, more costs more. With "mid" in
    settings.levels, anything up to max_years is a small plus. Above max_years always costs most."""
    if years <= 1:
        return 8 if years == 0 else 6
    if years > s.max_years:
        return -25
    return 4 if s.mid else -10 if years == 2 else -18


def _profile_skills() -> list[tuple[str, int]]:
    """Skills from profile.yaml with their weights; none before the profile exists."""
    if not PROFILE.exists():
        return []
    skills = (yaml.safe_load(PROFILE.read_text()) or {}).get("skills") or []
    return [(s["name"].lower(), LEVEL_WEIGHT.get(s.get("level"), 1)) for s in skills]


def _words(name: str) -> set[str]:
    return set(re.split(r"[\s/(),]+", name.lower())) - {""}


def prescore(job: dict, skills: list[tuple[str, int]] | None = None) -> tuple[int, list[str]]:
    skills = _profile_skills() if skills is None else skills
    s = settings.get()
    title = job.get("title") or ""
    job_skills = job.get("skills") or []
    if isinstance(job_skills, str):
        job_skills = [job_skills]
    text = f"{title}\n{' '.join(job_skills)}\n{(job.get('description') or '')[:4000]}".lower()
    why = []

    hits, weight = [], 0
    for name, w in skills:
        pats = skill_patterns(name)
        if any(re.search(p, text) for p in pats):
            hits.append(name)
            weight += w
    score = min(weight * 4, 45)
    if hits:
        why.append("skills: " + ", ".join(hits[:6]))

    if JUNIOR_TITLE.search(title):
        score += 22
        why.append("entry-level title")
    elif JUNIOR_TEXT.search(text):
        score += 15
        why.append("entry-level wording")
    elif MID_TEXT.search(title):
        score -= 4
    if s.students and (STUDENT_ROLE.search(title) or "Tags: student" in (job.get("description") or "")):
        score += 8
        why.append("student role")
    senior_title, senior_levels = (SENIOR_ONLY_TITLE, SENIOR_ONLY_LEVELS) if s.mid else (SENIOR_TITLE, SENIOR_LEVELS)
    if senior_title.search(title) or (job.get("seniority") or "").lower() in senior_levels:
        score -= 15
        why.append("senior/mid level")
    if _offsite(s).search(text):
        score -= 25
        why.append(f"on-site/near-site outside {s.city or 'your city'}")

    years = required_years(job.get("description") or "")
    if years is not None:
        score += years_points(years, s)
        why.append(f"{years}y asked")

    roles = _roles(s)
    if (s.tech_only and ROLE_FAMILY.search(title)) or (roles and roles.search(title)):
        score += 12
    elif s.tech_only or roles:
        score -= 20
        why.append("role family unclear")

    owned = set().union(*(_words(name) for name, _ in skills))
    foreign = [f for f in STACK_TITLE.findall(title) if f.lower() not in owned]
    if foreign and not any(_words(h) & PROGRAMMING for h in hits[:3]):
        score -= 18
        why.append("stack: " + ", ".join(sorted({f.lower() for f in foreign})))

    if not (job.get("description") or "").strip():
        why.append("no description yet")
    return max(0, min(100, score + 20)), why


def run(conn: sqlite3.Connection, statuses: tuple[str, ...] = ("new",)) -> dict:
    skills = _profile_skills()
    rows = conn.execute(f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(statuses))})", statuses).fetchall()
    import json
    for r in rows:
        job = dict(r)
        try:
            job["skills"] = json.loads(job.get("skills") or "[]")
        except ValueError:
            pass
        s, why = prescore(job, skills)
        conn.execute("UPDATE jobs SET prescore = ?, prescore_why = ? WHERE id = ?", (s, "; ".join(why), r["id"]))
    conn.commit()
    dist = {b: 0 for b in ("70+", "50-69", "30-49", "<30")}
    for (v,) in conn.execute(f"SELECT prescore FROM jobs WHERE status IN ({','.join('?' * len(statuses))})", statuses):
        dist["70+" if v >= 70 else "50-69" if v >= 50 else "30-49" if v >= 30 else "<30"] += 1
    return {"scored": len(rows), "distribution": dist}


def auto_low(conn: sqlite3.Connection, below: int) -> int:
    """Mark remaining unscored jobs under the cut as scored-low, so they leave the queue with a reason."""
    import json
    rows = conn.execute("SELECT id, prescore, prescore_why FROM jobs WHERE status = 'new' AND prescore < ?", (below,)).fetchall()
    for r in rows:
        notes = {"matched": [], "missing": [], "red_flags": [], "reason": f"Pre-score {r['prescore']} only (not read in detail): {r['prescore_why']}"}
        conn.execute("UPDATE jobs SET score = ?, tier = 'low', score_notes = ?, status = 'scored', "
                     "scored_at = datetime('now') WHERE id = ?",
                     (min(r["prescore"] // 2, 40), json.dumps(notes), r["id"]))
    conn.commit()
    return len(rows)
