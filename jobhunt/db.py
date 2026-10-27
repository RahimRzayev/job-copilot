"""SQLite storage for jobs, applications and the Q&A answer memory."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "jobhunt.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY,
    dedupe_key   TEXT UNIQUE NOT NULL,
    source       TEXT NOT NULL,
    source_id    TEXT,
    title        TEXT NOT NULL,
    company      TEXT NOT NULL,
    location     TEXT,
    remote       INTEGER DEFAULT 0,
    seniority    TEXT,
    url          TEXT NOT NULL,
    apply_url    TEXT,
    description  TEXT,
    skills       TEXT,              -- JSON list
    salary       TEXT,
    posted_at    TEXT,
    scraped_at   TEXT NOT NULL,
    score        INTEGER,           -- 0-100, written by Claude in /find-jobs
    tier         TEXT,              -- high | medium | low
    score_notes  TEXT,              -- JSON: matched, missing, red_flags, reason
    languages    TEXT,              -- JSON list of required spoken languages
    category     TEXT,
    filter_reason TEXT,             -- why prefilter dropped it (status = filtered)
    employer_url TEXT,              -- the employer's own ATS posting (behind aggregators)
    expires_at   TEXT,
    liveness     TEXT,              -- open | closed | unknown (jobhunt verify)
    liveness_detail TEXT,
    verified_at  TEXT,
    history_note TEXT,              -- prior applications to this company (from Gmail)
    history_warn INTEGER DEFAULT 0, -- 1 = same role applied before, or test/interview pending
    prescore     INTEGER,           -- cheap 0-100 ranking aid (jobhunt/prescore.py)
    prescore_why TEXT,
    scored_at    TEXT,              -- when Claude (or auto-low) scored it; the report's "today" uses this
    status       TEXT NOT NULL DEFAULT 'new'
        -- new | filtered | closed | scored | shortlisted | applying | skipped | needs_input | ready | applied
        -- | manual | rejected | interview | offer
);

CREATE TABLE IF NOT EXISTS applications (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER NOT NULL REFERENCES jobs(id),
    ats          TEXT,
    applied_at   TEXT,
    answers      TEXT,              -- JSON snapshot of every field we filled
    cover_letter TEXT,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS qa_memory (
    id                 INTEGER PRIMARY KEY,
    question_canonical TEXT UNIQUE NOT NULL,
    answer             TEXT NOT NULL,
    answer_type        TEXT NOT NULL DEFAULT 'text',  -- text | yesno | number | choice
    tags               TEXT DEFAULT '[]',              -- JSON list, e.g. ["skill", "work-auth"]
    variants           TEXT DEFAULT '[]',              -- JSON list of other phrasings seen
    company            TEXT,                           -- set for per-company overrides
    updated_at         TEXT NOT NULL
);

-- Skill facts for tailored CVs: yes/no per skill, asked once. 'no' is stored too so it is never asked or added again.
CREATE TABLE IF NOT EXISTS skill_facts (
    skill       TEXT PRIMARY KEY,           -- canonical lowercase, e.g. "java"
    display     TEXT NOT NULL,              -- how it is written on the CV, e.g. "Java"
    status      TEXT NOT NULL,              -- yes | no
    level       TEXT,                       -- basic | working | strong (yes only)
    section_key TEXT,                       -- which CV Skills bullet it goes into, e.g. "backend"
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id         INTEGER PRIMARY KEY,
    domain     TEXT UNIQUE NOT NULL,   -- ATS / career site host
    email      TEXT NOT NULL,
    keychain   TEXT NOT NULL,          -- OS keychain service name (keyring); password never stored here
    created_at TEXT NOT NULL
);

-- Small key/value state, e.g. when the LinkedIn alert emails were last read (alerts_last_read, unix seconds).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Columns added after the first release; created on older databases at connect time.
_MIGRATIONS = {"jobs": {"languages": "TEXT", "category": "TEXT", "filter_reason": "TEXT", "employer_url": "TEXT",
                        "expires_at": "TEXT", "liveness": "TEXT", "liveness_detail": "TEXT", "verified_at": "TEXT",
                        "history_note": "TEXT", "history_warn": "INTEGER DEFAULT 0",
                        "prescore": "INTEGER", "prescore_why": "TEXT", "scored_at": "TEXT",
                        "employer_note": "TEXT",
                        # apply queue (jobhunt queue): batch start, last status change, what is happening now
                        "shortlisted_at": "TEXT", "status_at": "TEXT", "queue_note": "TEXT",
                        # last `jobhunt resolve` attempt, so unresolved jobs are not re-searched on every scan
                        "resolve_tried_at": "TEXT"}}


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, cols in _MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, kind in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))
    conn.commit()


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("skills", "languages", "score_notes", "answers", "tags", "variants"):
        if isinstance(d.get(key), str):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                pass
    return d
