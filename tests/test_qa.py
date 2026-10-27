import pytest

from jobhunt import qa
from jobhunt.db import connect


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "test.db")


def test_rephrased_question_matches(conn):
    qa.save(conn, "How many years of Python experience do you have?", "2", "number", ["skill"])
    for phrasing in [
        "Years of experience with Python",
        "Python experience (years)?",
        "How many years have you worked with Python?",
    ]:
        hits = qa.lookup(conn, phrasing)
        assert hits, phrasing
        assert hits[0]["answer"] == "2"


def test_unrelated_question_does_not_match(conn):
    qa.save(conn, "How many years of Python experience do you have?", "2", "number")
    assert qa.lookup(conn, "Are you legally authorized to work in Poland?") == []


def test_company_override_wins(conn):
    qa.save(conn, "Why do you want to work here?", "General answer")
    qa.save(conn, "Why do you want to work at Globex?", "Globex answer", company="Globex")
    hits = qa.lookup(conn, "Why do you want to work at Globex?", company="Globex")
    assert hits[0]["answer"] == "Globex answer"
    other = qa.lookup(conn, "Why do you want to work here?", company="Initech")
    assert all(h["company"] is None for h in other)


def test_save_overwrites_and_variants(conn):
    qa_id = qa.save(conn, "Expected salary (PLN gross/month)?", "6000")
    assert qa.save(conn, "Expected salary (PLN gross/month)?", "7000") == qa_id
    qa.add_variant(conn, qa_id, "Wynagrodzenie oczekiwane")
    hits = qa.lookup(conn, "Wynagrodzenie oczekiwane")
    assert hits[0]["answer"] == "7000"


def test_stemming_and_no_cross_match(conn):
    qa.save(conn, "Expected salary for an internship (EUR gross per month)?", "1800")
    qa.save(conn, "What is your Polish proficiency level?", "Intermediate")
    qa.save(conn, "What is your highest level of education / current studies?", "BA Economics")
    qa.save(conn, "When do you expect to graduate?", "2029")
    qa.save(conn, "What is your earliest start date?", "Immediately")

    assert qa.lookup(conn, "Salary expectations for this internship")[0]["answer"] == "1800"
    assert qa.lookup(conn, "Polish language level")[0]["answer"] == "Intermediate"
    assert qa.lookup(conn, "Expected graduation date")[0]["answer"] == "2029"
    assert qa.lookup(conn, "When can you start?")[0]["answer"] == "Immediately"
    assert qa.lookup(conn, "Have you used Kubernetes?") == []


def test_long_form_phrasings():
    from jobhunt.qa import _similarity
    # Asides in brackets, synonyms (eligibility/authorized, desired/expected, employed/working, soon/earliest).
    assert _similarity("Are you authorized to work in the jobs location? (e.g., do you require sponsorship to work "
                       "in the job location?)", "Describe your work authorization status in Poland.") >= 70
    assert _similarity("If hired, are you able to provide proof of eligibility to legally work in the country you "
                       "applied for?", "Are you legally authorized to work in Poland?") >= 70
    assert _similarity("Do you have any relatives employed by Acme?",
                       "Do you have family members or relatives working at this company?") >= 70
    assert _similarity("How soon could you start if offered a position?", "What is your earliest start date?") >= 70
    assert _similarity("Do you know Docker?", "What is your notice period?") < 70


def test_search_country_is_a_filler_word():
    from jobhunt import settings
    from jobhunt.qa import _similarity
    settings.use(settings.Settings(country="Portugal", city="Lisbon", country_names=("portugal",)))
    assert _similarity("Are you legally authorized to work in Portugal?",
                       "Are you authorized to work in the job's location?") >= 70
