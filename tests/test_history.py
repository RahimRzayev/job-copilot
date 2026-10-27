import pytest

from jobhunt import history
from jobhunt.db import connect


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "t.db")


@pytest.fixture
def words(monkeypatch):
    """A fixed word list, so brand-word rules do not depend on the machine's /usr/share/dict/words."""
    monkeypatch.setattr(history, "_WORDS", {"falcon", "ruby", "harbor", "seeking"})


ROWS = [
    {"sender_name": "NOVA-Globex", "sender_email": "nova@globex.com", "date": "Tue, Dec 2, 2025, 4:33 PM",
     "subject": "Action Required - Technical and English Test", "snippet": "Thank you for applying for the Video Game Tester role at Globex Games!", "thread_id": "a"},
    {"sender_name": "NOVA-Globex", "sender_email": "nova@globex.com", "date": "Tue, Dec 2, 2025, 4:31 PM",
     "subject": "Application Received for Video Game Tester at Globex Technologies LLC", "snippet": "Thank you for your interest", "thread_id": "b"},
    {"sender_name": "Initech", "sender_email": "notification@smartrecruiters.com", "date": "Fri, Oct 2, 2026, 11:44 AM",
     "subject": "Thank you for applying to ​Junior Modelling Analyst​ at ​Initech​", "snippet": "We just received your application", "thread_id": "c"},
    {"sender_name": "hr@northwind.com", "sender_email": "hr@northwind.com", "date": "Thu, Jun 18, 2026, 9:45 AM",
     "subject": "Northwind – ERP Functional Consultant Intern", "snippet": "would like to invite you to take part in the placement testing", "thread_id": "d"},
    {"sender_name": "Recruiting", "sender_email": "no-reply@commission.example", "date": "Mon, Jan 5, 2026, 9:00 AM",
     "subject": "Unfortunately we decided to move forward with other candidates", "snippet": "", "thread_id": "e"},
]


def test_classify_and_dates(conn):
    history.import_rows(conn, ROWS)
    kinds = dict(conn.execute("SELECT thread_id, kind FROM app_history").fetchall())
    assert kinds == {"a": "test", "b": "applied", "c": "applied", "d": "test", "e": "rejection"}
    dates = dict(conn.execute("SELECT thread_id, msg_date FROM app_history").fetchall())
    assert dates["a"] == "2025-12-02" and dates["c"] == "2026-10-02"


def test_alerts_and_offers():
    assert history.classify("Alex Example 78% match: Junior Data Scientist job for you", "") == "alert"
    assert history.classify("Your application for Trainee ERP @ Northwind", "Job offer: Trainee ERP") != "offer"
    assert history.classify("Next steps", "We are pleased to offer you the position") == "offer"
    # A LinkedIn alert digest is no application.
    assert history.classify("“internship”: Globex - Security engineer intern posted on 10/4/26",
                            "View jobs in Springfield Metropolitan Area") == "alert"
    assert history.classify("Thank you for applying at Acme!", "") == "applied"
    assert history.classify("Remote Front End Developer Skills Test for Contoso Technologies", "") == "test"


def test_import_is_idempotent(conn):
    history.import_rows(conn, ROWS)
    assert history.import_rows(conn, ROWS)["added"] == 0


def test_company_matching(conn):
    history.import_rows(conn, ROWS)
    hits = history.for_company(conn, "Globex Poland Sp. z o.o.", "Video Game Tester (FQA/Entry Level)")
    assert len(hits) == 2 and any(h["same_role"] for h in hits)
    assert history.for_company(conn, "Northwind", "Junior Data Engineer")[0]["same_role"] is False
    assert history.for_company(conn, "Iss Poland", "Junior IT Support") == []  # 'iss' must not match 'commission'
    note, strong = history.summarize(history.for_company(conn, "Globex", "Video Game Tester"))
    assert strong and "test" in note


def test_company_core():
    assert history.company_core("Grupa Globex Sp. z o.o.") == "globex"
    assert history.company_core("UMBRELLA POLAND sp. z o.o.") == "umbrella"
    assert history.company_core("Grupa Kapitałowa Inter Contoso") == "inter contoso"


def test_company_core_drops_the_search_country():
    from jobhunt import settings
    settings.use(settings.Settings(country="Germany", city="Berlin", country_names=("germany", "deutschland")))
    assert history.company_core("Globex Deutschland GmbH") == "globex"
    assert history.brand_word("Berlin Robotics Lab") == ""  # the search city is no brand


def test_role_noise_uses_the_search_places():
    from jobhunt import settings
    settings.use(settings.Settings(country="Portugal", city="Lisbon", city_names=("lisbon", "lisboa"),
                                   other_cities=("port",)))
    assert history._role_tokens("QA Intern (f/m) - Lisboa, Portugal / hybrid") == ["qa", "intern"]
    assert history._role_tokens("QA Intern - Porto") == ["qa", "intern"]


def test_platform_named_company(conn):
    history.import_rows(conn, [
        {"sender_name": "Acme Workday", "sender_email": "acme@myworkday.com", "date": "Thu, Jul 2, 2026, 9:00 AM",
         "subject": "Update on Your Job Application", "snippet": "", "thread_id": "w1"},
        {"sender_name": "Workday Careers", "sender_email": "careers@workday.com", "date": "Fri, Jul 3, 2026, 9:00 AM",
         "subject": "Thank you for applying to Workday", "snippet": "", "thread_id": "w2"},
    ])
    hits = history.for_company(conn, "Workday", "Customer Support Analyst")
    assert [h["subject"] for h in hits] == ["Thank you for applying to Workday"]


def test_generic_company_name_and_other_company_sender(conn):
    """A generic core ('Management Solutions') must not match 'Talent Management' mails, and a consultancy's mail
    about its 'Contoso Lab' programme is no Contoso application."""
    history.import_rows(conn, [
        {"thread_id": "a", "date": "Tue, May 26, 2026, 9:00 AM", "sender_name": "Talent", "sender_email": "jobs@acme.com",
         "subject": "Your Application for Internship in Talent Management & Learning is Received", "snippet": ""},
        {"thread_id": "b", "date": "Mon, Oct 5, 2026, 2:19 PM", "sender_name": "Initech CE Recruit.",
         "sender_email": "cerecruiting@initechce.com", "subject": "Initech | Your Application was Submitted",
         "snippet": "interested in joining Initech for Contoso Lab | Business Analyst path"},
    ])
    assert history.company_core("Management Solutions") == "management solutions"
    assert history.for_company(conn, "Management Solutions") == []
    assert history.for_company(conn, "Contoso", "Backend .NET Developer") == []
    assert len(history.for_company(conn, "Initech")) == 1


def test_same_role_uses_same_normalization():
    assert history._same_role("Associate Technical Consultant - Entry-Level Consulting",
                              "Update regarding your application for Associate Technical Consultant - Entry-Level Consulting")
    assert history._same_role("Junior Operations Engineer (f/m)",
                              "Thank you for your application for the position Junior Operations Engineer (f/m) at Umbrella")
    assert not history._same_role("Junior Operations Engineer (f/m)",
                                  "Thank you for your application for the position Junior Cloud DevOps Engineer (Azure)")


def test_same_role_seniority_differs():
    assert not history._same_role("Operations Engineer (f/m)",
                                  "Thank you for your application for the position Junior Operations Engineer (f/m)")


def test_brand_word_finds_short_names(conn, words):
    """A long company name whose core never appears in its mails is still found by its distinctive first word."""
    assert history.company_core("QXR - Global Transport and Logistics") == "qxr transport and"
    assert history.brand_word("QXR - Global Transport and Logistics") == "qxr"
    assert history.brand_word("ZQT online investing") == "zqt"
    assert history.brand_word("Bank Globex S.A.") == ""         # generic first word
    assert history.brand_word("Johnson Robotics") == ""         # would match Johnson & Johnson
    assert history.brand_word("Initech") == ""                  # single word: the core already is the brand
    assert history.brand_word("Banking Contoso") == ""          # would match a "Banking Champions" internship
    history.import_rows(conn, [
        {"thread_id": "d1", "date": "Thu, Mar 26, 2026, 9:00 AM", "sender_name": "no-r...@successfactors.eu",
         "sender_email": "no-reply@successfactors.eu", "subject": "Your application for Junior Client Reporting Analyst",
         "snippet": "received your application for Junior Client Reporting Analyst . Thank you kindly for your interest in QXR."},
        {"thread_id": "p1", "date": "Fri, Mar 27, 2026, 9:00 AM", "sender_name": "Bank Northwind",
         "sender_email": "hr@banknorthwind.pl", "subject": "Thank you for applying to Bank Northwind", "snippet": ""},
    ])
    hits = history.for_company(conn, "QXR - Global Transport and Logistics", "Junior Client Reporting Analyst (M/F/D)")
    assert len(hits) == 1 and hits[0]["same_role"]
    assert history.for_company(conn, "Bank Globex S.A.", "Analyst") == []


def test_dictionary_brand_word_needs_support(conn, words):
    """A brand word that is an ordinary word ('falcon', 'ruby') needs the full name or the sender behind it."""
    history.import_rows(conn, [
        {"thread_id": "dp", "date": "Wed, Mar 18, 2026, 9:00 AM", "sender_name": "Acme Falcon Careers",
         "sender_email": "careers@acmefalcon.com", "subject": "Acme Falcon Careers - Application Received - Summer Internship",
         "snippet": ""},
        {"thread_id": "rb", "date": "Wed, Oct 7, 2026, 9:00 AM", "sender_name": "JustJoin",
         "sender_email": "no-reply@justjoin.it", "subject": "You applied for Ruby on Rails Junior+/Middle Developer",
         "snippet": ""},
        {"thread_id": "wb", "date": "Fri, Aug 29, 2025, 9:00 AM", "sender_name": "Harbor Careers",
         "sender_email": "noreply@myworkday.com", "subject": "An update about your application for Payments Analyst", "snippet": "Harbor careers"},
    ])
    assert history.for_company(conn, "Falcon Ridge", "Project Developer") == []
    assert history.for_company(conn, "Ruby Widgets", "Backend Engineer") == []
    assert len(history.for_company(conn, "Harbor Bros. Studios", "Analyst")) == 1   # sender name starts with brand


def test_missing_word_list_makes_every_brand_word_need_support(conn, monkeypatch, tmp_path):
    """Windows / minimal Linux have no /usr/share/dict/words: a brand-word-only hit then needs support too."""
    monkeypatch.setattr(history, "DICT_PATHS", (str(tmp_path / "no-words"),))
    monkeypatch.setattr(history, "_WORDS", history._UNLOADED)
    assert history._dictionary() is None
    history.import_rows(conn, [
        {"thread_id": "d1", "date": "Thu, Mar 26, 2026, 9:00 AM", "sender_name": "System",
         "sender_email": "no-reply@successfactors.eu", "subject": "Your application for Junior Client Reporting Analyst",
         "snippet": "Thank you kindly for your interest in QXR."},
        {"thread_id": "d2", "date": "Fri, Mar 27, 2026, 9:00 AM", "sender_name": "QXR Careers",
         "sender_email": "no-reply@successfactors.eu", "subject": "Your application for Junior Data Analyst",
         "snippet": "Thank you for applying."},
    ])
    hits = history.for_company(conn, "QXR - Global Transport and Logistics", "Junior Data Analyst")
    assert [h["subject"] for h in hits] == ["Your application for Junior Data Analyst"]  # the sender backs it up
