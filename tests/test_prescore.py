from dataclasses import replace

from jobhunt import settings
from jobhunt.prescore import prescore

SKILLS = [("python", 3), ("django", 3), ("typescript", 3), ("react", 3), ("postgresql", 3), ("docker", 2), ("linux", 2)]


def s(**job):
    return prescore(job, SKILLS)[0]


def test_ranking_order():
    junior_py = s(title="Junior Python Developer", skills=["Python", "Django", "PostgreSQL"],
                  description="We welcome students. 0-1 years of experience. Docker and Linux are a plus.")
    plain_swe = s(title="Software Engineer", skills=["Python", "React"],
                  description="2 years of experience with Python and TypeScript, React, Docker.")
    mid_java = s(title="Java Developer (Regular)", skills=["Java", "Spring"],
                 description="3 years of experience with Java and Spring Boot.")
    non_tech = s(title="Account Manager", skills=[], description="Sales experience.")
    # 2+ years is already a penalty in entry-level ranking, so the plain SWE with "2 years" sits below the junior.
    assert junior_py > plain_swe and junior_py > mid_java and junior_py > non_tech
    assert junior_py >= 70 and non_tech < 30


def test_near_site_and_senior_penalties():
    # A "near-site, within 2 hours of <city>" junior role must not rank first.
    near = s(title="Junior SharePoint Developer (Warsaw, near-site)", skills=["Python"],
             description="near-site (within 2 hours of Warsaw). At least 2 years of professional experience")
    plain = s(title="Junior Python Developer", skills=["Python"], description="Python. Warsaw office.")
    assert near < plain - 30
    # Mid/senior tags (LinkedIn seniority or title) rank low.
    mid = s(title="Full Stack Engineer", skills=["Python", "React"], description="React Python", seniority="mid-senior level")
    assert mid < 60


def test_graduate_title_boost_without_description():
    assert s(title="Graduate Programme - Technology", skills=[], description="") > s(title="Software Engineer", skills=[], description="")


def test_offsite_penalty_follows_the_settings():
    berlin = settings.Settings(country="Germany", country_code="DE", country_names=("germany",), city="Berlin",
                               city_names=("berlin",), other_cities=("munich", "münchen", "hamburg"))
    settings.use(berlin)
    on_site = s(title="Junior Python Developer", skills=["Python"], description="Hybrid, 3 days in our Munich office.")
    home = s(title="Junior Python Developer", skills=["Python"], description="Hybrid, 3 days in our Berlin office.")
    assert on_site < home - 20
    assert "on-site/near-site outside Berlin" in prescore({"title": "Junior Developer",
                                                          "description": "Near-site role."}, SKILLS)[1]
    settings.use(replace(berlin, relocate=True))
    assert s(title="Junior Python Developer", skills=["Python"], description="Hybrid, 3 days in our Munich office.") \
        == home


def test_stack_penalty_skips_stacks_the_user_has():
    java = dict(title="Junior Java Developer", skills=["Java"], description="Java and Spring.")
    assert s(**java) < prescore(java, SKILLS + [("java", 3)])[0]
    assert "stack: java" in prescore(java, SKILLS)[1]
    assert not any(w.startswith("stack") for w in prescore(java, SKILLS + [("java", 3)])[1])


def test_profile_skill_names_map_to_generic_aliases():
    from jobhunt.prescore import skill_patterns
    assert skill_patterns("LLM APIs (OpenAI)") == skill_patterns("llm")
    assert skill_patterns("Drizzle ORM") == skill_patterns("orm")
    assert skill_patterns("C++") == skill_patterns("c++") != skill_patterns("c#")
    assert skill_patterns("Swift") == ["swift"]  # unknown names are searched as written
