"""Answer memory: ask the user once, reuse the answer for every rephrasing after.

Matching is two-stage. This module does cheap fuzzy retrieval and returns ranked
candidates with a similarity score; Claude then decides whether a candidate
really answers the form's question (and adapts it to the field type). Below the
threshold, nothing is returned and Claude must ask the user, then call save().
"""

import json
import re
import sqlite3
from functools import lru_cache

from rapidfuzz import fuzz

from . import settings
from .db import now, row_to_dict

MATCH_THRESHOLD = 70

_STOPWORDS = {
    "do", "you", "your", "have", "are", "is", "the", "a", "an", "of", "in", "with",
    "please", "what", "how", "many", "any", "to", "for", "and", "or", "if", "will",
    "be", "can", "when", "this", "position", "role", "describe", "provide", "indicate", "currently",
    # Generic location / filler words: "work in the job's location" = "work in <country>" (the search country and
    # city are added from the settings, see _stopwords).
    "job", "jobs", "location", "country", "e.g", "eg", "i.e", "ie", "legally", "able", "would",
    "could", "by", "at", "company", "offered", "hired",
}


_SUFFIXES = ("ations", "ation", "ate", "ions", "ion", "ing", "ies", "ed", "es", "s")

# Words that mean the same thing on application forms.
_SYNONYMS = {
    "begin": "start", "commence": "start",
    "wynagrodzenie": "salary", "pay": "salary", "compensation": "salary", "remuneration": "salary",
    "proficiency": "level", "fluency": "level",
    "authorised": "authoriz", "authorized": "authoriz", "permit": "authoriz", "visa": "sponsor",
    "eligibility": "authoriz", "eligible": "authoriz", "right": "authoriz",
    "desired": "expect", "expected": "expect", "expectation": "expect", "expectations": "expect",
    "requirement": "expect", "requirements": "expect",
    "employed": "work", "working": "work", "worked": "work", "soon": "earliest",
}


def _stem(word: str) -> str:
    word = _SYNONYMS.get(word, word)
    for suffix in _SUFFIXES:
        if len(word) - len(suffix) >= 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


@lru_cache(maxsize=4)
def _stopwords_for(s: settings.Settings) -> frozenset:
    places = {w for n in (s.country, s.city, *s.country_names, *s.city_names) for w in (n or "").lower().split()}
    return frozenset(_STOPWORDS | places)


def normalize(question: str) -> str:
    q = question.lower()
    q = re.sub(r"[^\w\s+#./-]", " ", q)
    q = q.replace("/", " ")
    stop = _stopwords_for(settings.get())
    return " ".join(_stem(w) for w in q.split() if w not in stop)


def _clauses(question: str) -> list[str]:
    """The question plus its parts: forms add asides ("... location? (e.g., do you require sponsorship ...)") that
    drown the core question in extra words."""
    parts = [p.strip() for p in re.split(r"[?()]", question) if len(p.split()) >= 3]
    return [question, *parts] if len(parts) > 1 else [question]


def _similarity(a: str, b: str) -> float:
    return max(_pair_similarity(part, b) for part in _clauses(a))


def _pair_similarity(a: str, b: str) -> float:
    """Keyword coverage of the shorter question, blended with fuzzy ratio for typos."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    wa, wb = set(na.split()), set(nb.split())
    coverage = len(wa & wb) / min(len(wa), len(wb)) * 100
    return 0.6 * coverage + 0.4 * fuzz.token_set_ratio(na, nb)


def lookup(conn: sqlite3.Connection, question: str, company: str | None = None, limit: int = 3) -> list[dict]:
    """Return up to `limit` stored answers whose question resembles `question`.

    A company-specific answer outranks a general one at equal similarity.
    """
    results = []
    for row in conn.execute("SELECT * FROM qa_memory"):
        item = row_to_dict(row)
        if item["company"] and (not company or item["company"].lower() != company.lower()):
            continue
        phrasings = [item["question_canonical"], *item["variants"]]
        score = max(_similarity(question, p) for p in phrasings)
        if score >= MATCH_THRESHOLD:
            item["similarity"] = round(score, 1)
            results.append(item)
    results.sort(key=lambda r: (r["similarity"], bool(r["company"])), reverse=True)
    return results[:limit]


def save(
    conn: sqlite3.Connection,
    question: str,
    answer: str,
    answer_type: str = "text",
    tags: list[str] | None = None,
    company: str | None = None,
) -> int:
    """Insert or update an answer. Re-saving the same question overwrites it."""
    cur = conn.execute(
        """
        INSERT INTO qa_memory (question_canonical, answer, answer_type, tags, company, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(question_canonical) DO UPDATE SET
            answer = excluded.answer,
            answer_type = excluded.answer_type,
            tags = excluded.tags,
            company = excluded.company,
            updated_at = excluded.updated_at
        RETURNING id
        """,
        (question.strip(), answer, answer_type, json.dumps(tags or []), company, now()),
    )
    qa_id = cur.fetchone()[0]
    conn.commit()
    return qa_id


def add_variant(conn: sqlite3.Connection, qa_id: int, phrasing: str) -> None:
    """Remember a new phrasing Claude matched to an existing answer, so the next match is direct."""
    row = conn.execute("SELECT variants FROM qa_memory WHERE id = ?", (qa_id,)).fetchone()
    if row is None:
        raise KeyError(f"no qa_memory entry {qa_id}")
    variants = json.loads(row["variants"])
    if phrasing not in variants:
        variants.append(phrasing)
        conn.execute(
            "UPDATE qa_memory SET variants = ?, updated_at = ? WHERE id = ?",
            (json.dumps(variants), now(), qa_id),
        )
        conn.commit()


def delete(conn: sqlite3.Connection, qa_id: int) -> None:
    conn.execute("DELETE FROM qa_memory WHERE id = ?", (qa_id,))
    conn.commit()


def list_all(conn: sqlite3.Connection, tag: str | None = None) -> list[dict]:
    items = [row_to_dict(r) for r in conn.execute("SELECT * FROM qa_memory ORDER BY updated_at DESC")]
    if tag:
        items = [i for i in items if tag in i["tags"]]
    return items
