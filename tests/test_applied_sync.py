"""Applied sync from Gmail, duplicate closing on apply, tier from score, cover claims against confirmed CV facts."""
from pathlib import Path

import pytest

from jobhunt import cover, formfill, history, jobs
from jobhunt.db import connect

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.yaml"


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "t.db")


def add_job(conn, title, company, status="scored", source="linkedin", source_id=None):
    cur = conn.execute(
        "INSERT INTO jobs (dedupe_key, source, source_id, title, company, url, scraped_at, status) "
        "VALUES (?, ?, ?, ?, ?, 'https://x', '2026-10-01', ?)",
        (f"{source}|{title}|{company}|{source_id}", source, source_id, title, company, status))
    conn.commit()
    return cur.lastrowid


def test_tier_follows_score():
    assert [jobs.tier_for(x) for x in (85, 79, 55, 54)] == ["high", "medium", "medium", "low"]


def test_applied_mail_with_same_role_syncs_status(conn):
    jid = add_job(conn, "Engineer, Software Engineering", "Globex & Company")
    history.import_rows(conn, [{"sender_name": "Globex", "sender_email": "careers@globex.com",
                                "date": "Tue, Oct 7, 2026, 9:00 AM", "subject": "Thank you for applying to Engineer, Software Engineering at Globex & Company",
                                "snippet": "We received your application", "thread_id": "globex1"}])
    result = history.annotate(conn)
    assert [r["id"] for r in result["applied_synced"]] == [jid]
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()[0] == "applied"
    assert conn.execute("SELECT COUNT(*) FROM applications WHERE job_id = ?", (jid,)).fetchone()[0] == 1


def test_company_only_applied_mail_goes_to_review_not_status(conn):
    jid = add_job(conn, "Data Analyst", "Acme")
    history.import_rows(conn, [{"sender_name": "Acme", "sender_email": "jobs@acme.com",
                                "date": "Tue, Oct 7, 2026, 9:00 AM", "subject": "Thank you for applying to Acme",
                                "snippet": "We received your application", "thread_id": "acme1"}])
    result = history.annotate(conn)
    assert result["applied_synced"] == []
    assert [r["id"] for r in result["applied_review"]] == [jid]
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()[0] == "scored"


def test_recording_application_closes_other_copies(conn):
    applied = add_job(conn, "Associate Full-Stack Software Engineer (Node.js, React)", "Initech", source="linkedin", source_id="1")
    copy = add_job(conn, "Associate Full-Stack Software Engineer (Node.js, React)", "Initech Inc", source="greenhouse", source_id="2", status="new")
    other = add_job(conn, "Unrelated Role", "Initech", status="scored")
    jobs.record_application(conn, applied, "greenhouse", {"x": "y"})
    row = conn.execute("SELECT status, filter_reason FROM jobs WHERE id = ?", (copy,)).fetchone()
    assert tuple(row) == ("skipped", f"duplicate of #{applied} (applied)")
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (other,)).fetchone()[0] == "scored"


def test_cover_claims_accept_confirmed_facts(monkeypatch):
    monkeypatch.setattr(formfill, "PROFILE", FIXTURE_PROFILE)
    letter = "I have used Docker and, at a basic level, Kubernetes and AWS."
    assert [x["term"] for x in cover.check_claims(letter, {})] == ["Kubernetes", "AWS"]
    assert cover.check_claims(letter, {}, confirmed={"kubernetes", "aws"}) == []


def test_old_confirmation_for_reposted_role_goes_to_review(conn):
    """A confirmation from long before the posting appeared belongs to an earlier opening of the same title."""
    jid = add_job(conn, "Junior Insight Analyst", "Umbrella")
    conn.execute("UPDATE jobs SET posted_at = '2026-10-16' WHERE id = ?", (jid,))
    history.import_rows(conn, [{"sender_name": "Umbrella", "sender_email": "careers@umbrella.com",
                                "date": "Sun, Aug 17, 2025, 9:00 AM",
                                "subject": "Thank you for applying to Junior Insight Analyst at Umbrella",
                                "snippet": "We received your application", "thread_id": "umb1"}])
    result = history.annotate(conn)
    assert result["applied_synced"] == []
    assert [r["id"] for r in result["applied_review"]] == [jid]
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()[0] == "scored"
