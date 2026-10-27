"""Target roles come from the user's settings: a non-tech job seeker keeps non-tech jobs, IT-only boards and tech
search words switch on only with `tech_only`, and `exclude_titles` drops what the user never wants."""
from dataclasses import replace
from datetime import datetime, timezone

from jobhunt import pipeline, settings
from jobhunt.normalize import Job
from jobhunt.prefilter import check_listing
from jobhunt.prescore import prescore
from jobhunt.sources import linkedin

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def job(**kw) -> Job:
    base = dict(source="linkedin", source_id="1", title="Junior Developer", company="Acme", url="https://x",
                location="Warsaw", posted_at="2026-10-01T09:00:00+00:00")
    return Job(**{**base, **kw})


def marketing(example):
    return replace(example, tech_only=False, roles=("marketing assistant", "content marketing"),
                   keywords=("intern", "junior"), exclude_titles=("call center",))


def test_non_tech_titles_are_kept_unless_tech_only(example_settings):
    assert check_listing(job(title="Marketing Intern"), 24, NOW) == "non-tech role"
    settings.use(marketing(example_settings))
    assert check_listing(job(title="Marketing Intern"), 24, NOW) is None
    assert check_listing(job(title="Junior Sales Specialist"), 24, NOW) is None


def test_excluded_title_words_drop_the_job(example_settings):
    settings.use(marketing(example_settings))
    assert check_listing(job(title="Call Center Agent (English)"), 24, NOW) == "excluded title (call center)"
    assert check_listing(job(title="Junior Marketing Assistant"), 24, NOW) is None


def test_roles_decide_the_role_family_score(example_settings):
    settings.use(marketing(example_settings))
    fit, _ = prescore({"title": "Junior Marketing Assistant", "description": ""}, [])
    off, why = prescore({"title": "Junior Software Engineer", "description": ""}, [])
    assert fit - off == 32 and "role family unclear" in why


def test_no_roles_and_not_tech_only_is_neutral(example_settings):
    settings.use(replace(example_settings, tech_only=False, roles=()))
    a, why = prescore({"title": "Junior Marketing Assistant", "description": ""}, [])
    b, _ = prescore({"title": "Junior Software Engineer", "description": ""}, [])
    assert a == b and "role family unclear" not in why


def test_search_words_follow_the_roles(example_settings):
    settings.use(marketing(example_settings))
    assert linkedin.slice_keywords() == ["intern", "junior", "marketing assistant", "content marketing"]
    assert linkedin.narrow_keywords() == ["marketing assistant", "content marketing"]
    words = pipeline.pracuj_keywords()
    assert words[:2] == ["marketing assistant", "content marketing"] and "helpdesk" not in words


def test_it_only_boards_need_tech_only(example_settings):
    assert example_settings.enabled("justjoin") and example_settings.enabled("pracuj")
    s = marketing(example_settings)
    assert not s.enabled("justjoin") and not s.enabled("nofluffjobs") and not s.enabled("bulldogjob")
    assert s.enabled("pracuj") and s.enabled("englishjobs") and s.enabled("linkedin")
    assert replace(s, sources=("justjoin",)).enabled("justjoin")  # an explicit source list wins


def test_settings_read_roles_from_yaml():
    s = settings.from_dict({"country": "Poland", "city": "Warsaw", "roles": ["Data Analyst"], "tech_only": True,
                            "exclude_titles": "Sales"})
    assert s.roles == ("data analyst",) and s.tech_only and s.exclude_titles == ("sales",)


def test_student_bonus_and_student_searches_follow_levels(example_settings):
    from jobhunt.sources import englishjobs
    student = {"title": "Working Student Data", "description": ""}
    assert "student role" in prescore(student, [])[1]
    assert any(s for _, _, s in englishjobs.searches("warszawa"))
    settings.use(replace(example_settings, levels=("graduate", "junior")))
    assert "student role" not in prescore(student, [])[1]
    assert not any(s for _, _, s in englishjobs.searches("warszawa"))
    assert "praca dla studenta" not in pipeline.pracuj_keywords()


def test_mid_level_users_are_not_penalised_for_mid_roles(example_settings):
    mid = {"title": "Mid Data Analyst", "description": "We ask for 4 years of experience in analytics.",
           "seniority": "mid-senior level"}
    settings.use(replace(example_settings, levels=("junior", "mid"), max_years=5))
    score, why = prescore(mid, [])
    assert "senior/mid level" not in why
    settings.use(replace(example_settings, levels=("junior",), max_years=5))
    assert prescore(mid, [])[0] < score


def test_region_remote_needs_region_names():
    s = settings.Settings()
    assert s.remote == "country" and s.region_names == ()
