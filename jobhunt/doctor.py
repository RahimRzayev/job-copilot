"""`jobhunt init` (first-run files) and `jobhunt doctor` (one line per check, with the fix for each problem).

Both take the project root so they can be tried on an empty folder; the real commands use the repository root.
"""

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import settings
from .db import ROOT, connect

EXAMPLE = ROOT / "profile" / "profile.example.yaml"
PLAYWRIGHT_ADD = "claude mcp add --scope project playwright -- npx @playwright/mcp@latest"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True

    def line(self) -> str:
        optional = "" if self.required else " (optional)"
        text = f"{'✅' if self.ok else '❌'} {self.name}{optional}: {self.detail}"
        return text if self.ok or not self.fix else f"{text} → fix: {self.fix}"


def _profile_path(root: Path) -> Path:
    return settings.PROFILE if root.resolve() == ROOT else root / "profile" / "profile.yaml"


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def init(root: Path = ROOT) -> dict:
    """Profile from the example (only when missing), data folders and the database. Safe to re-run."""
    root = Path(root)
    profile = _profile_path(root)
    created = not profile.exists()
    if created:
        profile.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXAMPLE, profile)
    dirs = [root / "data" / "inbox", root / "reports", root / "profile" / "cv-source"]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    db = root / "data" / "jobhunt.db"
    connect(db).close()
    return {"profile": str(profile), "profile_created": created, "db": str(db), "dirs": [str(d) for d in dirs]}


def _load_profile(path: Path, root: Path) -> tuple[dict, Check]:
    if not path.exists():
        return {}, Check("profile", False, f"{_rel(path, root)} missing", "run `uv run jobhunt init`, then /setup")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        return {}, Check("profile", False, f"{_rel(path, root)} is not valid YAML ({getattr(e, 'problem', e)})",
                         "fix the line it names, or re-run /setup")
    name = (data.get("personal") or {}).get("full_name")
    if not name:
        return data, Check("profile", False, f"{_rel(path, root)} has no personal.full_name yet", "run /setup")
    return data, Check("profile", True, _rel(path, root))


def _cv_check(person: dict, root: Path) -> Check:
    rel = person.get("cv_file")
    fix = "put your CV as a PDF in profile/ and set personal.cv_file to it"
    if not rel:
        return Check("CV file", False, "personal.cv_file is not set", fix)
    paths = {"main": rel, **(person.get("cv_variants") or {})}
    missing = [f"{k}: {v}" for k, v in paths.items() if not (Path(v) if Path(v).is_absolute() else root / v).exists()]
    if missing:
        return Check("CV file", False, "not found: " + ", ".join(missing), fix)
    return Check("CV file", True, ", ".join(str(v) for v in paths.values()))


def _password_check(s: settings.Settings, person: dict) -> Check:
    from . import secret
    account = s.job_email or person.get("email") or ""
    if not account:
        return Check("default password", False, "no job email (search.job_email or personal.email)",
                     "run /setup, then `uv run jobhunt secret set` in your own terminal", required=False)
    try:
        password = secret.get_password(account, svc=s.password_service)
    except SystemExit as e:
        return Check("default password", False, str(e), "set up the OS keychain, then `uv run jobhunt secret set`",
                     required=False)
    if not password:
        return Check("default password", False, f"nothing stored under {s.password_service!r} for {account}",
                     "run `uv run jobhunt secret set` in your own terminal", required=False)
    return Check("default password", True, f"set ({len(password)} characters)", required=False)


def _playwright_check(root: Path) -> Check:
    path = root / ".mcp.json"
    if not path.exists():
        return Check("Playwright MCP", False, ".mcp.json missing", f"run `{PLAYWRIGHT_ADD}` in the project folder")
    try:
        servers = json.loads(path.read_text()).get("mcpServers") or {}
    except (json.JSONDecodeError, AttributeError) as e:
        return Check("Playwright MCP", False, f".mcp.json is not valid JSON ({e})", "fix or delete it, then "
                     f"run `{PLAYWRIGHT_ADD}`")
    if "playwright" not in servers:
        return Check("Playwright MCP", False, ".mcp.json has no `playwright` server", f"run `{PLAYWRIGHT_ADD}`")
    return Check("Playwright MCP", True, ".mcp.json → playwright")


def _pdf_check() -> Check:
    from . import cvtailor
    name = cvtailor.pdf_exporter()
    if not name:
        return Check("PDF exporter for tailored CVs", False, "none found", cvtailor.INSTALL_HINT, required=False)
    return Check("PDF exporter for tailored CVs", True, name, required=False)


def _db_check(root: Path) -> Check:
    path = root / "data" / "jobhunt.db"
    if not path.exists():
        return Check("database", False, f"{_rel(path, root)} missing", "run `uv run jobhunt init`")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            state = conn.execute("PRAGMA quick_check").fetchone()[0]
            jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return Check("database", False, f"{_rel(path, root)}: {e}", "run `uv run jobhunt init`; if it persists, "
                     "move the file away and run init again")
    if state != "ok":
        return Check("database", False, f"integrity check: {state}", "restore data/jobhunt.db from a backup")
    return Check("database", True, f"{_rel(path, root)} ({jobs} jobs)")


def _companies_check(root: Path) -> Check:
    path = root / "companies.yaml"
    if not path.exists():
        return Check("companies.yaml", False, "missing: company career boards are skipped",
                     "optional: /setup step 8 builds a watchlist", required=False)
    try:
        companies = (yaml.safe_load(path.read_text()) or {}).get("companies") or []
    except (yaml.YAMLError, AttributeError) as e:
        return Check("companies.yaml", False, f"does not load ({getattr(e, 'problem', e)})", "fix the line it names")
    if not isinstance(companies, list):
        return Check("companies.yaml", False, "`companies:` is not a list", "see the format at the top of the file")
    from .sources.ats_boards import BOARDS
    bad = [str(c.get("name")) for c in companies if not isinstance(c, dict) or c.get("ats") not in BOARDS]
    if bad:
        return Check("companies.yaml", False, f"{len(companies)} companies, unknown ats for: {', '.join(bad[:5])}",
                     f"use one of: {', '.join(sorted(BOARDS))}")
    if not companies:
        return Check("companies.yaml", False, "empty: no company career sites are searched",
                     "ask Claude: \"add <company> to my watchlist\"", required=False)
    return Check("companies.yaml", True, f"{len(companies)} companies")


def checks(root: Path = ROOT) -> list[Check]:
    root = Path(root)
    profile, profile_check = _load_profile(_profile_path(root), root)
    s = settings.from_dict(profile.get("search") or {})
    person = profile.get("personal") or {}
    search = Check("search settings", s.configured,
                   f"{s.city}, {s.country}; works in {', '.join(s.languages)}" if s.configured
                   else "search.country and search.city are not set", "run /setup (search settings step)")
    return [profile_check, search, _cv_check(person, root), _password_check(s, person), _playwright_check(root),
            _pdf_check(), _db_check(root), _companies_check(root)]


def run(root: Path = ROOT) -> tuple[str, bool]:
    """(report text, every required check passed)."""
    results = checks(root)
    return "\n".join(c.line() for c in results) + "\n", all(c.ok for c in results if c.required)
