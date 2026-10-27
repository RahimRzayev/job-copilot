import json
from datetime import date
from pathlib import Path

from jobhunt import mailcheck, settings
from jobhunt.mailcheck import classify, plan

TODAY = date(2026, 10, 3)


def row(subject, snippet="", sender="no-reply@example.com", name="", when="Sat, Oct 3, 2026, 10:00 AM"):
    return {"subject": subject, "snippet": snippet, "sender_email": sender, "sender_name": name, "date": when,
            "thread_id": subject[:10]}


def b(r):
    return classify(r, TODAY)[0]


def test_noise():
    assert b(row("Alex: your job alert for Software Engineer in Springfield has been created",
                 sender="jobalerts-noreply@linkedin.com")) == "noise"
    assert b(row("“python”: Globex - Python Developer and more", sender="jobalerts-noreply@linkedin.com")) == "noise"
    assert b(row("Your Umbrella Job Alert", sender="UmbrellaJobs@noreply12.jobs2web.com")) == "noise"
    assert b(row("New openings this week", sender="careers@noreply12.jobs2web.com")) == "noise"
    assert b(row("Your order has shipped", sender="orders@shop.com")) == "noise"


def test_job_info():
    assert b(row("Thank you for applying to Northwind", sender="no-reply@northwind.com")) == "job_info"
    assert b(row("Podziękowanie za zainteresowanie współpracą z Contoso (aplikacja w odpowiedzi na ogłoszenie)",
                 sender="notification@smartrecruiters.com")) == "job_info"
    assert b(row("We’re sorry your Initech application isn’t progressing further")) == "job_info"
    assert b(row("Thank You for Creating an Account!", sender="system@successfactors.eu")) == "job_info"
    assert b(row("One Time Passcode for Acme Lottery Account creation", when="Thu, Oct 1, 2026, 9:00 AM")) == "job_info"


def test_keep():
    assert b(row("Invitation to interview - Junior Developer", sender="jane@company.com", name="Jane Roe")) == "keep"
    assert b(row("Alex Example - Next Steps for your Consultant Development Program")) == "keep"
    assert b(row("Your HackerRank assessment for Acme")) == "keep"
    assert b(row("Please complete your application", "We are missing your documents")) == "keep"
    assert b(row("Security alert", "New sign-in to your Google Account", sender="no-reply@accounts.google.com")) == "keep"
    assert b(row("One Time Passcode for Acme Lottery Account creation")) == "keep"  # same day: may still be needed


def test_plan_dedupes_threads():
    p = plan([row("Thank you for applying to Northwind"), row("Thank you for applying to Northwind")], TODAY)
    assert len(p["job_info"]) == 1


def test_recruiter_question_and_account_notices():
    assert b(row("Northwind – Intern Developer", "Could you possibly specify your level of English? "
                 "Looking forward to your reply!", sender="hr@northwind.com")) == "keep"
    assert b(row("You shared some Google Account data with Darwinbox", sender="noreply-accounts@google.com")) == "noise"
    assert b(row("Help us match you with the right jobs", "We currently have your details on our database",
                 sender="data.admin@recruitco.example")) == "noise"


# Rejections whose first ~150 characters read like confirmations; only the body tells.
REJECT_HOLISTIC = ("Dear Alex\n Thank you again for the time and energy you invested in your application for the "
                   "Associate Consultant role.\n After carefully reviewing your application holistically, we have "
                   "decided to move forward with other applicants at this time.\n We value your feedback: ... share "
                   "your thoughts on your interview experience via this brief survey")
REJECT_NOT_RIGHT_MATCH = ("Dear Alex,\nThank you for taking the time to apply for the Technical Support Agent role with "
                          "Contoso Talents.\nWhile this particular role isn’t the right match at the moment, we greatly "
                          "appreciate your background")
REJECT_NOT_MOVE_FORWARD = ("Dear Alex,\n Thank you again for your interest in Umbrella House and for taking the time to "
                           "apply for the Traffic Technical Support Engineer position.\n After careful review, we have "
                           "made the difficult decision not to move forward with your application at this time.")
REJECT_ANOTHER_CANDIDATE = ("Dear Alex, \n Thank you for your interest in the  Contoso Lab | Business Analyst path | "
                            "Springfield & Shelbyville  position at Initech and for submitting your CV. We appreciated "
                            "the opportunity to review your background and experience. \n After careful consideration "
                            "of all applications, our team has decided to move forward with another candidate whose "
                            "profile more closely matches the specific requirements of this role.")


def test_body_reveals_rejection():
    for subject, body in [("Update regarding your application for Associate Technical Consultant", REJECT_HOLISTIC),
                          ("Thank you for your application through Contoso Talents", REJECT_NOT_RIGHT_MATCH),
                          ("Your application for Traffic Technical Support Engineer at Umbrella House",
                           REJECT_NOT_MOVE_FORWARD),
                          ("Initech | Feedback on your application - Contoso Lab | Business Analyst path",
                           REJECT_ANOTHER_CANDIDATE)]:
        r = {**row(subject, body[:150]), "body": body}
        assert classify(r, TODAY) == ("job_info", "rejection"), subject


def test_body_footer_does_not_force_keep():
    body = ("Thank you for applying to Acme. We have received your application and will review it.\n" + "x " * 300 +
            "Next steps: our team reviews every application. Please do not reply. Security notice: ...")
    assert b({**row("Thank you for applying to Acme", body[:150]), "body": body}) == "job_info"


def test_body_interview_invite_still_keep():
    body = "Hi Alex, we would like to invite you to an interview. Please choose a slot in Calendly."
    assert b({**row("Next step in your Acme application", body[:150]), "body": body}) == "keep"


def test_sample_inbox_buckets():
    """A synthetic inbox with bodies: invites, tests and questions stay unread; confirmations that merely describe the
    process ("we'll be in touch if we'd like to meet you for an interview", "if this role isn't the right fit") and
    every rejection are job info; alerts and promotions are noise."""
    rows = json.loads((Path(__file__).parent / "fixtures" / "gmail_sample.json").read_text())
    p = plan(rows, TODAY)
    ids = {bucket: {r["thread_id"]: r["reason"] for r in items} for bucket, items in p.items()}
    assert ids["keep"] == {"s05": "interview", "s06": "test", "s11": "asks for something / next steps"}
    assert ids["job_info"] == {"s01": "applied", "s02": "applied", "s03": "rejection", "s04": "rejection",
                               "s08": "account", "s09": "old verification code"}
    assert set(ids["noise"]) == {"s07", "s10"} and ids["unknown"] == {}
    assert all(r["has_body"] for items in p.values() for r in items)


def test_gmail_url_opens_the_job_inbox():
    assert mailcheck.gmail_url().startswith("https://mail.google.com/mail/u/jobs.example@gmail.com/#search/is%3Aunread")
    settings.use(settings.Settings(country="Portugal", city="Lisbon"))  # no job_email: the first signed-in account
    assert mailcheck.gmail_url() == "https://mail.google.com/mail/u/0/#search/is%3Aunread"


def test_unnamed_rejection_only_flips_the_single_open_application(tmp_path):
    from jobhunt import history
    from jobhunt.db import connect
    from jobhunt.mailcheck import sync_job_statuses
    conn = connect(tmp_path / "t.db")
    def job(i, company, title):
        conn.execute("INSERT INTO jobs (id, dedupe_key, source, title, company, url, scraped_at, status) "
                     "VALUES (?,?,?,?,?,?,?,?)", (i, f"k{i}", "x", title, company, "u", "2026-10-01", "applied"))
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (?, ?)", (i, "2026-10-01T10:00:00"))
    job(1, "Globex", "Graduate software engineer")
    job(2, "Umbrella", "Operations Engineer (f/m)")
    job(3, "Umbrella", "Junior Cloud DevOps Engineer (Azure) (f/m)")
    history.import_rows(conn, [
        {"thread_id": "a", "date": "Tue, Oct 6, 2026, 3:25 PM", "sender_name": "Globex",
         "sender_email": "careers@globex.com", "subject": "Alex Example & Globex—Regarding your application",
         "snippet": "", "body": "Hi Alex, we are sorry to inform you that we won’t be moving forward with it this time."},
        {"thread_id": "b", "date": "Tue, Oct 6, 2026, 11:49 AM", "sender_name": "Umbrella Hiring T.",
         "sender_email": "notifications@smartrecruiters.com", "subject": "Thank you for your application to Umbrella",
         "snippet": "", "body": "Thank you for your application for the position Junior Operations Engineer (f/m) at "
                                "Umbrella. We regret to inform you that we have chosen to proceed with other applicants"},
    ])
    changed = {c["id"]: c["to"] for c in sync_job_statuses(conn)}
    assert changed == {1: "rejected"}  # Umbrella: rejection is for a third role, two open applications -> no flip


def test_rejection_mentioning_job_alerts_and_polish_questionnaire():
    today = date(2026, 10, 7)
    initech = {"sender_name": "HR System", "sender_email": "HRsystem@initech.com", "date": "Wed, Oct 7, 2026, 11:32 AM",
               "subject": "Initech Group - Application Status Update", "snippet": "",
               "body": "Dear Alex, Thank you for your application for the position of AWS AI Developer After careful "
                       "consideration, we regret to inform you that we will not be pursuing your application. However, "
                       "we do invite you to register to our email job alert service to receive notifications."}
    assert classify(initech, today) == ("job_info", "rejection")
    questionnaire = {"sender_name": "Assessment Partner", "sender_email": "no-reply@assessments.example",
                     "date": "Wed, Oct 7, 2026, 3:38 PM",
                     "subject": "Globex Polska – Wypełnij kwestionariusz, aby dokończyć swoją aplikację", "snippet": "",
                     "body": "Cześć Alex! Dziękujemy za Twoją aplikację na stanowisko DevOps. To krótki kwestionariusz "
                             "o Twoim stylu pracy."}
    assert classify(questionnaire, today)[0] == "keep"


def test_assessment_flips_only_the_named_role(tmp_path):
    from jobhunt import history
    from jobhunt.db import connect
    from jobhunt.mailcheck import sync_job_statuses
    conn = connect(tmp_path / "t.db")
    for i, title in ((1, "Contoso Lab | Developer path | Springfield"), (2, "Agentic AI Technical Consultant | Poland")):
        conn.execute("INSERT INTO jobs (id, dedupe_key, source, title, company, url, scraped_at, status) "
                     "VALUES (?,?,?,?,?,?,?,?)", (i, f"k{i}", "x", title, "Initech", "u", "2026-10-01", "applied"))
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (?, ?)", (i, "2026-10-01T10:00:00"))
    history.import_rows(conn, [
        {"thread_id": "a", "date": "Wed, Oct 7, 2026, 4:37 PM", "sender_name": "Roe, Jane",
         "sender_email": "jroe@initechce.com",
         "subject": "Initech | Next steps - information about your online assessment for the job Contoso Lab | "
                    "Developer path | Springfield",
         "snippet": "", "body": "You will be invited to complete the required assessment for the role Contoso Lab | "
                                "Developer path | Springfield. Please check your inbox for an email from TestGorilla."},
    ])
    changed = {c["id"]: c["to"] for c in sync_job_statuses(conn)}
    assert changed == {1: "interview"}


def test_platform_mail_naming_only_the_role(tmp_path):
    """An ATS mail (generic sender) that names only the role syncs the one open application with that role."""
    from jobhunt import history
    from jobhunt.db import connect
    from jobhunt.mailcheck import sync_job_statuses
    conn = connect(tmp_path / "t.db")
    for i, company, title in ((1, "Umbrella", "Engineer, Integrations"), (2, "Globex", "QA Engineer")):
        conn.execute("INSERT INTO jobs (id, dedupe_key, source, title, company, url, scraped_at, status) "
                     "VALUES (?,?,?,?,?,?,?,?)", (i, f"k{i}", "x", title, company, "u", "2026-10-01", "applied"))
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (?, ?)", (i, "2026-10-03T10:00:00"))
    history.import_rows(conn, [
        {"thread_id": "p", "date": "Fri, Oct 9, 2026, 9:00 AM", "sender_name": "System System",
         "sender_email": "system@successfactors.eu", "subject": "Your Application", "snippet": "",
         "body": "Dear Alex Example, Thank you for your interest in the Engineer, Integrations position. After careful "
                 "consideration of your application, we have decided to move forward with other candidates for this role."},
    ])
    changed = {c["id"]: c["to"] for c in sync_job_statuses(conn)}
    assert changed == {1: "rejected"}


def test_role_only_skips_mail_naming_another_applied_company(tmp_path):
    """A rejection naming a closed application's company must not flip a similar open role elsewhere."""
    from jobhunt import history
    from jobhunt.db import connect
    from jobhunt.mailcheck import sync_job_statuses
    conn = connect(tmp_path / "t.db")
    for i, company, title, status in ((1, "ZQT online investing", "Data Engineer", "applied"),
                                      (2, "Initech Poland", "Cybersecurity Analyst", "rejected")):
        conn.execute("INSERT INTO jobs (id, dedupe_key, source, title, company, url, scraped_at, status) "
                     "VALUES (?,?,?,?,?,?,?,?)", (i, f"k{i}", "x", title, company, "u", "2026-10-01", status))
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (?, ?)", (i, "2026-10-01T10:00:00"))
    history.import_rows(conn, [
        {"thread_id": "a", "date": "Sun, Oct 4, 2026, 9:00 AM", "sender_name": "Initech",
         "sender_email": "initech@myworkday.com",
         "subject": "We’re sorry your Initech application isn’t progressing further", "snippet": "",
         "body": "Hi Alex, Thank you so much for taking the time to apply for the role of Junior GenAI Engineer "
                 "(AI&Data) at Initech. After careful consideration of your application against the requirements "
                 "of the role, we’ve made the decision not to take your application further, on this occasion."},
    ])
    assert sync_job_statuses(conn) == []


def test_more_rejection_wordings():
    from jobhunt.history import classify
    assert classify("Thank you for your interest in Globex Sp. z o.o.", "",
                    "Thank you for your effort and dedicated time in the process for IT Intern at Globex. After careful "
                    "evaluation, we will not be considering you for this position.") == "rejection"
    assert classify("Information from the recruitment process - Umbrella IT Solutions Poland", "",
                    "Thank you for your interest in the recruitment process for the position of Data Analyst/Tester. "
                    "This time, we are unable to invite you to the next stages of the recruitment process.") == "rejection"
    assert classify("Thank you for your interest in ​Initech sp. z o.o.​", "",
                    "Thank you for your interest in the position of Junior Test Automation Engineer. After carefully "
                    "reviewing your application, we are sorry to inform you that this time we won’t be able to "
                    "invite you to the next phase of our selection process.") == "rejection"


def test_rejection_for_closed_role_does_not_hit_remaining_one(tmp_path):
    """Once one of two applications at a company is rejected, the other (now the only open one) must not take the
    same rejection."""
    from jobhunt import history
    from jobhunt.db import connect
    from jobhunt.mailcheck import sync_job_statuses
    conn = connect(tmp_path / "t.db")
    for i, title, status in ((1, "IT Internship (BackEnd, Java) (f/m)", "applied"),
                             (2, "IT Intern - Cybersecurity & AI (f/m)", "rejected")):
        conn.execute("INSERT INTO jobs (id, dedupe_key, source, title, company, url, scraped_at, status) "
                     "VALUES (?,?,?,?,?,?,?,?)", (i, f"k{i}", "x", title, "Globex", "u", "2026-10-01", status))
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (?, ?)", (i, "2026-10-01T10:00:00"))
    history.import_rows(conn, [
        {"thread_id": "b", "date": "Fri, Oct 16, 2026, 3:33 PM", "sender_name": "Jane Roe (HR)",
         "sender_email": "notifications@smartrecruiters.com",
         "subject": "Thank you for your interest in Globex Sp. z o.o.", "snippet": "",
         "body": "Your application – IT Intern - Cybersecurity & AI (f/m) - Main Street, Springfield, Poland Dear Alex, "
                 "After careful evaluation, we will not be considering you for this position."},
    ])
    assert sync_job_statuses(conn) == []
