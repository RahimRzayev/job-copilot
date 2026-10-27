"""formfill DATA (what the ATS fill scripts type) comes only from the profile and the search settings."""
from pathlib import Path

import pytest

from jobhunt import formfill, settings

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.yaml"
JOB = {"id": 987654321, "title": "Junior Developer", "company": "Acme", "source": "linkedin", "skills": []}


@pytest.fixture(autouse=True)
def fixture_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(formfill, "PROFILE", FIXTURE_PROFILE)
    monkeypatch.setattr(formfill, "CVS", tmp_path / "cvs")


def test_personal_fields():
    d = formfill.build_data(JOB)
    assert (d["first_name"], d["last_name"], d["email"]) == ("Alex", "Example", "jobs.example@gmail.com")
    assert (d["phone_country"], d["phone_local"], d["phone_full"]) == ("+48", "500600700", "+48500600700")
    assert d["phone_country_name"] == "Poland" and d["country"] == "Poland"
    assert (d["city_search"], d["city_option"]) == ("Warsaw", "Warsaw")
    assert d["address"] == {"street": "Przykładowa", "house_number": "12", "apartment": "3", "postal_code": "00-001",
                            "city": "Warszawa", "region": "Mazowieckie",
                            "country": "Poland"}
    assert d["linkedin"] == "https://linkedin.com/in/alex-example" and d["website"] == "https://github.com/alex-example"


def test_country_falls_back_to_the_search_settings(monkeypatch):
    prof = formfill.load_profile()
    prof["personal"] = {**prof["personal"], "country": ""}
    monkeypatch.setattr(formfill, "load_profile", lambda: prof)
    settings.use(settings.Settings(country="Germany", city="Berlin"))
    assert formfill.build_data(JOB)["country"] == "Germany"


def test_languages_and_skills():
    d = formfill.build_data(JOB)
    assert d["languages"] == [
        {"name": "Ukrainian", "cefr": "C2", "fluent": True, "native": True},
        {"name": "English", "cefr": "C1", "fluent": True, "native": False},
        {"name": "Polish", "cefr": "B1", "fluent": False, "native": False},
    ]
    assert d["skills"] == ["Python", "SQL", "Docker", "Linux", "Git"]  # strong first, then working; no basic


def test_education_and_experience():
    d = formfill.build_data(JOB)
    e = d["education"]
    assert e["institution"] == "Example University of Technology"
    assert e["institution_aliases"] == ["Example University of Technology", "Example College"]
    assert (e["degree"], e["degree_level"], e["major"]) == ("Bachelor of Science", "Bachelor", "Information Systems")
    assert e["degree_text"] == "Bachelor of Science (3rd year, expected June 2026, GPA 3.6/4.0)"
    assert (e["gpa"], e["gpa_scale"], e["year_of_study"]) == ("3.6 / 4.0", "4.0", 3)
    assert (e["start_year"], e["start_month"], e["end_year"], e["end_month"], e["current"]) == (2022, 10, 2026, 6, True)
    x = d["experience"]
    assert (x["title"], x["company"], x["location_search"]) == ("IT Support Volunteer", "Example Foundation", "Warsaw")
    assert x["description"] == "- Set up and maintained 20 office laptops\n- Answered helpdesk tickets"
    assert (x["start_year"], x["start_month"], x["end_year"], x["end_month"]) == (2024, 7, 2024, 9)
    assert x["current"] is False and d["past_employers"] == ["Example Foundation"]


def test_dates_and_study_status_are_never_guessed():
    assert formfill._ym("2023") == (2023, None) and formfill._ym("") == (None, None)
    assert formfill.studying({"status": "completed", "expected_graduation": "2099-06"}) is False
    assert formfill.studying({"expected_graduation": "2099-06"}) is True
    assert formfill.studying({"expected_graduation": "2001-06"}) is False
    assert formfill.studying({}) is None
    assert formfill.degree_text({"degree": "BA", "expected_graduation": "2001-06"}) == "BA (completed June 2001)"
    assert formfill.degree_level("Diplom-Ingenieur") == "Master" and formfill.degree_level("Inżynier") == "Bachelor"


def test_country_phone_and_source_follow_the_person(monkeypatch):
    prof = formfill.load_profile()
    prof["personal"] = {**prof["personal"], "country": "Ukraine", "phone": "+380 44 123 4567", "first_name": "Mary Ann",
                        "last_name": "Example"}
    prof["experience"] = [{**prof["experience"][0], "end": "present"}]
    monkeypatch.setattr(formfill, "load_profile", lambda: prof)
    d = formfill.build_data({**JOB, "source": "greenhouse"})
    assert d["country"] == "Ukraine" and d["address"]["country"] == "Ukraine"  # not the search country (Poland)
    assert d["phone_country_name"] in ("Ukraine", "") and (d["first_name"], d["last_name"]) == ("Mary Ann", "Example")
    assert d["source_label"] == "Company website"
    x = d["experience"]
    assert x["current"] is True and (x["end_year"], x["end_month"]) == (None, None)


def test_cv_variants(tmp_path):
    assert formfill.build_data(JOB)["resume_path"].endswith("tests/fixtures/cv.pdf")
    assert formfill.build_data(JOB, cv="data-analyst")["resume_path"].endswith("tests/fixtures/cv-data.pdf")
    with pytest.raises(SystemExit, match="data-analyst"):
        formfill.build_data(JOB, cv="designer")
    tailored = tmp_path / "cvs" / str(JOB["id"]) / "cv.pdf"
    tailored.parent.mkdir(parents=True)
    tailored.write_bytes(b"%PDF")
    assert formfill.build_data(JOB)["resume_path"] == str(tailored)


@pytest.mark.parametrize("phone, parts", [
    ("+1 415 555 0100", ("+1", "4155550100")), ("+44 20 7946 0000", ("+44", "2079460000")),
    ("+380 44 123 4567", ("+380", "441234567")), ("0048 500-600-700", ("+48", "500600700")),
    ("500 600 700", ("", "500600700")),
])
def test_phone_prefix(phone, parts):
    assert formfill.phone_parts(phone)[:2] == parts


def test_missing_profile_says_how_to_start(monkeypatch, tmp_path):
    monkeypatch.setattr(formfill, "PROFILE", tmp_path / "profile.yaml")
    with pytest.raises(SystemExit, match="jobhunt init"):
        formfill.build_data(JOB)


def test_fill_scripts_need_a_link_to_that_ats():
    linkedin_only = {"source": "linkedin", "url": "https://www.linkedin.com/jobs/view/1", "employer_url": None}
    assert not formfill.uses_ats(linkedin_only, "smartrecruiters")
    assert formfill.uses_ats({**linkedin_only, "employer_url": "https://jobs.smartrecruiters.com/Acme/123"},
                             "smartrecruiters")
    assert formfill.uses_ats({"source": "workday", "url": "https://acme.wd3.myworkdayjobs.com/x"}, "workday")
    assert formfill.uses_ats({"source": "sf_rmk", "url": "https://careers.acme.example/job/1/"}, "successfactors")
    assert not formfill.uses_ats({"source": "greenhouse", "url": "https://boards.greenhouse.io/acme/jobs/1"}, "workday")
