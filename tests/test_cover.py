from pathlib import Path

import pytest

from jobhunt import cover, formfill
from jobhunt.cover import check_claims
from jobhunt.db import connect

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.yaml"
PROFILE = """skills: [Python, Flask, Vue, PostgreSQL, Docker, Git]
projects: [{name: Pantry Planner, summary: "12 REST endpoints"}]"""


@pytest.fixture
def fixture_profile(monkeypatch):
    monkeypatch.setattr(formfill, "PROFILE", FIXTURE_PROFILE)


def test_flags_tools_not_in_profile():
    terms = [x["term"] for x in check_claims(
        "I use Git daily and I work in VS Code. I deploy with Kubernetes. Pantry Planner has 12 endpoints.",
        {"title": "Junior Developer", "company": "Acme"}, PROFILE)]
    assert "VS" in terms and "Kubernetes" in terms
    assert "Git" not in terms and "12" not in terms and "Pantry" not in terms


def test_gap_sentences_job_words_and_signature_are_fine():
    letter = ("Dear Acme team,\nI am applying for the Atlas Fullstack Developer position.\n"
              "To be transparent: I have not used GitLab CI or Kubernetes yet.\nKind regards,\nAlex Example\n"
              "+48 500 600 700")
    assert check_claims(letter, {"title": "Atlas Fullstack Developer (Regular)", "company": "Acme"}, PROFILE) == []


def test_no_false_flags_for_dates_plurals_formats():
    """Month names behind profile dates, plurals, file formats and '-backed' compounds are no claims."""
    prof = PROFILE + '\neducation: [{expected_graduation: "2030-05"}]\nskills2: [OpenAI API, recipe search]'
    letter = ("I am a final-year student graduating in May 2030. I build AI-backed Python services and REST APIs "
              "that exchange JSON. Pantry Planner reads PDFs and stores recipe URLs.")
    assert check_claims(letter, {"title": "Developer", "company": "Acme"}, prof) == []
    assert [x["term"] for x in check_claims("I graduate in March 2030.", {}, prof)] == ["March"]


def test_search_city_and_country_are_no_claims():
    from jobhunt import settings
    settings.use(settings.Settings(country="Portugal", city="Lisbon", city_names=("lisbon", "lisboa")))
    assert check_claims("I live in Lisbon (Lisboa), Portugal and use Docker.", {}, PROFILE) == []
    assert [x["term"] for x in check_claims("I live in Porto and use Docker.", {}, PROFILE)] == ["Porto"]


def test_claims_checked_against_the_profile_file(fixture_profile):
    letter = "At Example University of Technology I used Python, SQL and Docker. I also know Haskell."
    assert [x["term"] for x in check_claims(letter, {})] == ["Haskell"]


def test_file_name_comes_from_the_profile_name(fixture_profile):
    assert cover.path_for(12).name == "Alex_Example_Cover_Letter.pdf"
    assert cover.filename({"personal": {"full_name": "Łucja Żak-Example"}}) == "Lucja_Zak_Example_Cover_Letter.pdf"
    assert cover.filename({"personal": {}}) == "Cover_Letter.pdf"


@pytest.mark.parametrize("fonts", ["system", "none"])
def test_render_writes_pdf_with_or_without_a_unicode_font(fixture_profile, monkeypatch, tmp_path, fonts):
    if fonts == "none":
        monkeypatch.setattr(cover, "FONTS", [])  # core-font fallback: non-Latin-1 letters are replaced, not fatal
    monkeypatch.setattr(cover, "OUT", tmp_path / "covers")
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO jobs (id, dedupe_key, source, title, company, url, scraped_at) "
                 "VALUES (3, 'k', 'linkedin', 'Junior Developer', 'Acme', 'https://x', '2026-01-01')")
    conn.commit()
    path = cover.render(conn, 3, "Dear Acme team,\n\nI would like to join – zażółć gęślą jaźń.\n\nKind regards,\nAlex")
    assert path == tmp_path / "covers" / "3" / "Alex_Example_Cover_Letter.pdf"
    assert path.read_bytes().startswith(b"%PDF") and path.with_suffix(".txt").exists()


def test_language_level_and_degree_claims_need_the_profile():
    prof = "languages: {english: B2}\neducation: [{degree: BA, major: Economics}]"
    terms = [x["term"] for x in check_claims("I speak C1 English and hold a BSc in Physics.", {}, prof)]
    assert "C1" in terms and "BSc" in terms and "Physics" in terms and "English" not in terms


def test_file_names_keep_letters_readable():
    assert cover._ascii("Søren Weiß Łukasz Əliyev") == "Soren Weiss Lukasz Eliyev"
