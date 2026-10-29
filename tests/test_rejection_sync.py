"""Rejections that must reach the job status (brand-TLD sender domain, title qualifiers, a "Feedback on your
application" subject without body), and resolve not retrying the same jobs every run."""
import pytest

from jobhunt import history, jobs, mailcheck, resolve
from jobhunt.db import connect


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "t.db")


def applied_job(conn, title, company):
    cur = conn.execute(
        "INSERT INTO jobs (dedupe_key, source, title, company, url, scraped_at, status) "
        "VALUES (?, 'linkedin', ?, ?, 'https://x', '2026-10-01', 'scored')", (f"{title}|{company}", title, company))
    jobs.record_application(conn, cur.lastrowid, "x", {})
    conn.execute("UPDATE applications SET applied_at = '2026-10-05T10:00:00+00:00'")
    conn.commit()
    return cur.lastrowid


def mail(conn, sender_name, sender_email, subject, snippet, body=""):
    history.import_rows(conn, [{"sender_name": sender_name, "sender_email": sender_email, "subject": subject,
                                "date": "Fri, Oct 23, 2026, 9:00 AM", "snippet": snippet, "body": body,
                                "thread_id": subject[:20]}])


def status(conn, jid):
    return conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()[0]


def test_brand_tld_sender_is_the_company_itself(conn):
    jid = applied_job(conn, "Junior Software Developer", "Globex Bank")
    mail(conn, "Globex Bank Careers", "recruitment@careers.hr.globexbank",
         "Your application for the position of Junior Software Developer_Spr, 1234567890100123293",
         "Hello Alex, We would like to thank you for your interest in the position of Junior Software Developer_Spr.",
         "After careful consideration, we regret to inform you that we have decided to proceed with other candidates.")
    mailcheck.sync_job_statuses(conn)
    assert status(conn, jid) == "rejected"


def test_rejection_naming_title_core_syncs_single_application(conn):
    jid = applied_job(conn, "AI Engineer (LLM automation, agentic coding), part-time, remote", "Contoso Data")
    mail(conn, "Recruitment", "recruitment@contoso-data.eu", "Your application for AI Engineer at Contoso Data",
         "Hi, Thank you for applying for the AI Engineer role at Contoso Data and for the time you put into your application.",
         "We received many strong applications and decided to move forward with other candidates.")
    mailcheck.sync_job_statuses(conn)
    assert status(conn, jid) == "rejected"


def test_title_core_does_not_decide_between_two_applications(conn):
    a = applied_job(conn, "Software Engineer (Backend)", "Acme")
    b = applied_job(conn, "Software Engineer (Frontend)", "Acme")
    mail(conn, "Acme", "jobs@acme.com", "Your application for Software Engineer at Acme",
         "Thank you for applying for the Software Engineer role at Acme.",
         "Unfortunately we have decided to move forward with other candidates.")
    mailcheck.sync_job_statuses(conn)
    assert (status(conn, a), status(conn, b)) == ("applied", "applied")


def test_core_role_needs_whole_words_and_two_words():
    assert history.core_role("AI Engineer (LLM), remote", "the AI Engineer role at Contoso Data")
    assert not history.core_role("AI Engineer (LLM), remote", "the GenAI Engineer role")
    assert not history.core_role("AI Engineer (LLM), remote", "the Junior AI Engineer role")
    assert not history.core_role("Developer (Python)", "the Developer role")
    assert not history.core_role("AI Engineer", "the AI Engineer role")  # no qualifiers: the normal match decides


def test_feedback_subject_is_a_rejection_without_body():
    assert history.classify("Initech | Feedback on your application - AI Content Labs Intern | Poland",
                            "Dear Alex,. Thank you for your interest in the AI Content Labs Intern position at Initech "
                            "and for ...") == "rejection"
    assert history.classify("Alex, please provide feedback on your application process with Umbrella Electric.",
                            "you applied for a position with Umbrella Electric.") != "rejection"


def test_resolve_skips_jobs_tried_recently(conn, monkeypatch):
    conn.execute("INSERT INTO jobs (dedupe_key, source, title, company, url, scraped_at, status, score) "
                 "VALUES ('k', 'linkedin', 'Data Analyst', 'Nowhere Ltd', 'https://x', '2026-10-01', 'scored', 70)")
    conn.commit()
    calls = []
    monkeypatch.setattr(resolve, "_watchlist", lambda http, c, t: calls.append(c))
    monkeypatch.setattr(resolve, "_probe", lambda http, c, t: None)
    assert resolve.resolve(conn)["tried"] == 1
    assert resolve.resolve(conn)["tried"] == 0  # unresolved yesterday: not retried on every scan
    assert len(calls) == 1
    jid = conn.execute("SELECT id FROM jobs").fetchone()[0]
    assert resolve.resolve(conn, ids=[jid])["tried"] == 1  # explicit ids always run


def test_role_only_mail_from_an_unapplied_company_or_for_a_one_word_title_flips_nothing(conn):
    """An ATS rejection for "Intern" sent under another employer's name (one we never applied to through the tool)
    must not flip our own "Intern" or "Business Analyst" application at a different company."""
    intern = applied_job(conn, "Intern", "Initech")
    analyst = applied_job(conn, "Business Analyst", "Hooli")
    conn.execute("INSERT INTO jobs (dedupe_key, source, title, company, url, scraped_at, status) "
                 "VALUES ('pmo', 'linkedin', 'PMO Coordinator', 'Vandelay Industries', 'https://x', '2026-10-01', 'scored')")
    conn.commit()
    mail(conn, "Vandelay Industries", "vandelay@myworkday.com", "Your application", "",
         "Dear Alex, Many thanks for your application for the position of Business Analyst. After careful "
         "consideration, we are sorry to advise you that we will not be progressing with your application.")
    mail(conn, "System System", "system@successfactors.eu", "Your Application", "",
         "Dear Alex, Thank you for your interest in the Intern position. After careful consideration, we have decided "
         "to move forward with other candidates for this role.")
    assert mailcheck.sync_job_statuses(conn) == []
    assert status(conn, intern) == status(conn, analyst) == "applied"


def test_invite_for_a_longer_title_skips_the_generic_title_at_the_same_company(conn):
    generic = applied_job(conn, "Software Engineer", "Globex")
    specific = applied_job(conn, "Software Engineer Intern AI/ML (2027)", "Globex")
    mail(conn, "Globex Hiring Team", "no-reply@ashbyhq.com",
         "Globex | Assessment Instructions | Software Engineer Intern AI/ML (2027)", "",
         "Hi Alex, Thank you for applying for Software Engineer Intern AI/ML (2027)! As a next step, we would like to "
         "invite you to complete our online Technical Assessment.")
    changed = {c["id"]: c["to"] for c in mailcheck.sync_job_statuses(conn)}
    assert changed == {specific: "interview"} and status(conn, generic) == "applied"


def test_invite_with_two_words_before_interview():
    assert history.classify("Screening invitation", "",
                            "Hi, thank you for sending your CV and the task! Now, I'd like to invite you to a short "
                            "initial interview. The meeting will take 10-15 minutes.") == "interview"


def test_history_match_summary_lists_only_open_items(conn):
    for i, st in ((1, "new"), (2, "scored"), (3, "shortlisted")):
        conn.execute("INSERT INTO jobs (id, dedupe_key, source, title, company, url, scraped_at, status) "
                     "VALUES (?,?,?,?,?,?,?,?)", (i, f"k{i}", "x", "Shopify Developer", "Initech", "u", "2026-10-01", st))
    result = {"checked": 3, "with_history": 3, "applied_synced": [],
              "applied_review": [{"id": 2, "mails": ["x"]}, {"id": 3, "mails": ["y"]}], "flagged": [
                  {"id": 1, "warn": True, "note": "test pending"}, {"id": 2, "warn": True, "note": "test pending"},
                  {"id": 3, "warn": False, "note": "applied earlier"}]}
    out = history.match_summary(conn, result)
    assert out["warn_total"] == 2 and [f["id"] for f in out["warn_new_or_shortlisted"]] == [1]
    assert out["applied_review_total"] == 2 and [r["id"] for r in out["applied_review_new_or_shortlisted"]] == [3]
    assert "flagged" not in out
