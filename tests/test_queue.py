"""Apply queue (`jobhunt queue`, /shortlist): batch grouping, notes, read-only access."""
import sqlite3

import pytest

from jobhunt import jobs, queue
from jobhunt.db import connect


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    connect(path).close()
    return path


def add_job(conn, title, company, score):
    cur = conn.execute(
        "INSERT INTO jobs (dedupe_key, source, title, company, url, scraped_at, status, score) "
        "VALUES (?, 'pracuj', ?, ?, 'https://x', '2026-10-19', 'scored', ?)",
        (f"{title}|{company}", title, company, score))
    conn.commit()
    return cur.lastrowid


def test_queue_groups_the_shortlist_by_progress(db):
    conn = connect(db)
    a, b, c, d, e = (add_job(conn, f"Intern {n}", f"Co{n}", 90 - n) for n in range(5))
    jobs.set_status(conn, [a, b, c, d, e], "shortlisted")
    jobs.record_application(conn, a, "workday", {})
    jobs.set_status(conn, [b], "applying", note="filling the form")
    jobs.set_status(conn, [c], "skipped", note="Polish C1 on company page")
    jobs.set_status(conn, [d], "needs_input", note="waiting for your Submit approval")

    data = queue.batch(queue.connect_ro(db))
    ids = {title: [j["id"] for j in group] for title, group in data["groups"].items()}
    assert ids == {"Applying now": [b], "Waiting for you": [d], "In queue": [e], "Applied": [a],
                   "You apply yourself": [], "Skipped": [c]}
    text = queue.render(data)
    assert "2/5 done" in text and "Polish C1 on company page" in text and "✅ Applied" in text
    assert "via workday" not in text  # applied rows carry no note


def test_markdown_table_has_numbered_icon_rows(db):
    conn = connect(db)
    a, b, c = add_job(conn, "Contoso Lab | Developer path", "Initech", 80), add_job(conn, "Intern", "Co", 70), \
        add_job(conn, "Tester", "Skip Co", 60)
    jobs.set_status(conn, [a, b, c], "shortlisted")
    jobs.record_application(conn, a, "workday", {})
    jobs.set_status(conn, [c], "skipped", note="needs Polish")

    md = queue.markdown(queue.batch(queue.connect_ro(db)))
    assert "| # | Status | Company | Role | Score | Note |" in md
    assert "| 1 | ⏳ In queue | Co | Intern | 70 |  |" in md
    assert "| 2 | ✅ Applied | Initech | Contoso Lab \\| Developer path | 80 |  |" in md
    assert "| 3 | ❌ Skipped | Skip Co | Tester | 60 | needs Polish |" in md
    assert "2/3 done" in md and "```" not in md


def test_queue_shows_only_the_latest_batch(db):
    conn = connect(db)
    old, new = add_job(conn, "Old intern", "Old", 80), add_job(conn, "New intern", "New", 70)
    jobs.set_status(conn, [old], "shortlisted")
    jobs.record_application(conn, old, "smartrecruiters", {})
    conn.execute("UPDATE jobs SET shortlisted_at = '2026-10-10T08:00:00+00:00' WHERE id = ?", (old,))
    conn.commit()
    jobs.set_status(conn, [new], "shortlisted")

    data = queue.batch(queue.connect_ro(db))
    assert data["total"] == 1 and data["groups"]["In queue"][0]["id"] == new


def test_queue_connection_cannot_write(db):
    with pytest.raises(sqlite3.OperationalError):
        queue.connect_ro(db).execute("UPDATE jobs SET status = 'new'")


def test_empty_queue_says_how_to_start():
    assert "No shortlist yet" in queue.render({"started": None, "groups": {}, "total": 0})


def test_empty_markdown_says_how_to_start():
    assert "No shortlist yet" in queue.markdown({"started": None, "groups": {}, "total": 0})
