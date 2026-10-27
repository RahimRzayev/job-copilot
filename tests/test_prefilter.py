from dataclasses import replace
from datetime import datetime, timedelta, timezone

from jobhunt import settings

from jobhunt.normalize import Job, dedupe_key
from jobhunt.prefilter import MAX_YEARS, check_details, check_listing, language_flags, required_years, work_mode_reason

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def job(**kw) -> Job:
    base = dict(source="justjoin", source_id="1", title="Junior Python Developer", company="Acme",
                url="https://x", location="Warszawa", posted_at=(NOW - timedelta(hours=3)).isoformat())
    base.update(kw)
    return Job(**base)


def test_listing_passes_junior_warsaw():
    assert check_listing(job(), 24, NOW) is None


def test_listing_rejections():
    assert check_listing(job(title="Senior Backend Engineer"), 24, NOW) == "senior title"
    assert check_listing(job(title="Junior Rekruter IT"), 24, NOW) == "non-tech role"
    assert check_listing(job(location="Berlin"), 24, NOW).startswith("location")
    assert check_listing(job(posted_at=(NOW - timedelta(hours=30)).isoformat()), 24, NOW) == "older than 24h"
    assert check_listing(job(languages=["Polish:C1"]), 24, NOW) == "Polish required (listing)"
    assert check_listing(job(seniority="mid"), 24, NOW) == "seniority mid"


def test_programmes_get_longer_window():
    old = (NOW - timedelta(days=3)).isoformat()
    assert check_listing(job(title="Software Engineering Intern, 2030", posted_at=old), 24, NOW) is None
    assert check_listing(job(title="Junior Developer", posted_at=old), 24, NOW) == "older than 24h"
    very_old = (NOW - timedelta(days=20)).isoformat()
    assert check_listing(job(title="Graduate Programme", posted_at=very_old), 24, NOW) == "older than 336h"


def test_listing_keeps_remote_and_junior_lead_words():
    assert check_listing(job(location="Remote", remote=True), 24, NOW) is None
    assert check_listing(job(title="Graduate Associate Engineer"), 24, NOW) is None
    assert check_listing(job(title="Junior Salesforce Developer"), 24, NOW) is None
    assert check_listing(job(title="Junior Frontend Developer - Recruitment Technologies"), 24, NOW) is None
    assert check_listing(job(title="Sales Specialist"), 24, NOW) == "non-tech role"
    assert check_listing(job(title="Junior Salesforce Marketing Cloud Developer"), 24, NOW) is None
    assert check_listing(job(title="Junior IT Recruiter"), 24, NOW) == "non-tech role"
    assert check_listing(job(title="Junior IT Support Specialist (HR systems)"), 24, NOW) is None


def test_location_for_foreign_boards():
    gh = dict(source="greenhouse")
    assert check_listing(job(location="Remote, India", **gh), 24, NOW).startswith("location")
    assert check_listing(job(location="Spain", remote=True, **gh), 24, NOW).startswith("location")
    assert check_listing(job(location="Remote - Poland", **gh), 24, NOW) is None
    assert check_listing(job(location="Remote", **gh), 24, NOW).startswith("location")
    assert check_listing(job(location="Gdańsk", remote=True, source="justjoin"), 24, NOW) is None
    assert check_listing(job(location="Gdańsk", remote=False, source="justjoin"), 24, NOW).startswith("location")


def test_more_rejections():
    assert check_listing(job(title="Intermediate Backend Engineer"), 24, NOW) == "senior title"
    assert check_listing(job(title="Software Engineer III"), 24, NOW) == "senior title"
    assert check_listing(job(title="Account Executive, Commercial"), 24, NOW) == "non-tech role"
    assert check_listing(job(title="Software Engineer II, Cloud Platform"), 24, NOW) is None


def test_other_languages():
    assert check_listing(job(title="L1 Service Desk Agent with Czech or Slovak"), 24, NOW) == "other language required"
    assert check_listing(job(title="Junior Frontend Developer z j. niemieckim"), 24, NOW) == "German required (title)"
    assert check_listing(job(languages=["en:", "zh:C1"]), 24, NOW) == "other language required"
    assert check_listing(job(languages=["en:B2"]), 24, NOW) is None
    assert check_listing(job(languages=["Ukrainian:C1"]), 24, NOW) is None
    assert check_details(job(description="Ability to communicate in English and French fluently.")) \
        == "French required (description)"
    assert check_details(job(description="German is a plus. Python required.")) is None


def test_details_polish():
    assert check_details(job(description="Requirements: fluent Polish and English. Python.")) \
        == "Polish required (description)"
    assert check_details(job(description="English required. Polish is a plus. Python, SQL.")) is None
    assert check_details(job(description="Comfortable communicating in English and Polish (minimum B2 level).")) \
        == "Polish required (description)"
    assert check_details(job(description="Excellent communication skills in English and Polish (written and oral).")) \
        == "Polish required (description)"
    assert check_details(job(description="Communicative level of English and Polish. Python, SQL.")) \
        == "Polish required (description)"
    assert check_details(job(description="Very good command of both Polish and English.")) \
        == "Polish required (description)"
    pl = ("Wymagania: znajomość Python oraz doświadczenie w pracy w zespole. Oferujemy pracę dla "
          "studentów. Zakres obowiązków jest szeroki, będzie się działo, nasz zespół który")
    assert check_details(job(description=pl)) == "description in Polish"


def test_years():
    assert required_years("You have 3+ years of commercial experience with Java") == 3
    assert required_years("1-2 years of experience is welcome") == 1
    assert required_years("3+ years' experience providing L1/L2 support. 1+ years' experience with Linux") == 3
    assert required_years("Experience with Docker. 5 years of experience is nice to have") is None
    assert check_details(job(description="Minimum 4 years of experience in Python.")) == "4+ years required"
    assert check_details(job(description="At least 2 years of experience in Python.")) is None


def test_dedupe_across_boards():
    a = dedupe_key("Grupa Initech Sp. z o. o.", "Junior Software Engineer (m/k)", "Warszawa")
    b = dedupe_key("Initech", "Junior Software Engineer", "Warsaw, Masovian, Poland")
    assert a == b


def test_full_mode_keeps_mid_and_drops_senior():
    assert check_listing(job(title="Python Developer (Regular)"), 24, NOW, mode="full") is None
    assert check_listing(job(title="Intermediate Backend Engineer"), 24, NOW, mode="full") is None
    assert check_listing(job(title="Software Engineer", seniority="mid"), 24, NOW, mode="full") is None
    assert check_listing(job(title="Senior Backend Engineer"), 24, NOW, mode="full") == "senior title"
    assert check_listing(job(title="Tech Lead"), 24, NOW, mode="full") == "senior title"
    assert check_listing(job(title="Software Engineer", seniority="senior"), 24, NOW, mode="full") == "seniority senior"
    # narrow mode unchanged
    assert check_listing(job(title="Python Developer (Regular)"), 24, NOW) == "senior title"


def test_full_mode_years_threshold():
    assert check_details(job(description="3+ years of commercial experience with Python."), max_years=3) is None
    assert check_details(job(description="4 years of commercial experience with Python."), max_years=3) == "4+ years required"


def test_junior_words_extended():
    for t in ("Graduate Software Engineer", "Młodszy Programista Python", "Early Career Data Analyst", "Software Apprentice"):
        assert check_listing(job(title=t + " Senior Team"), 24, NOW, mode="full") is None, t


def test_other_polish_city_needs_remote():
    gh = dict(source="workday")
    assert check_listing(job(location="Krakow, Poland", **gh), 24, NOW).startswith("location")
    assert check_listing(job(location="Krakow, Poland", remote=True, **gh), 24, NOW) is None
    assert check_listing(job(location="Poland, Warsaw", **gh), 24, NOW) is None
    assert check_listing(job(location="Poland", **gh), 24, NOW) is None


def test_remote_dedupe_ignores_city():
    a = Job(source="linkedin", source_id="1", title="Forward Deployed AI Engineer (f/m/x)", company="Globex Poland", url="", location="Szczecin", remote=True)
    b = Job(source="linkedin", source_id="2", title="Forward Deployed AI Engineer (f/m/x)", company="Globex Poland", url="", location="Warsaw", remote=True)
    assert a.dedupe_key == b.dedupe_key


def test_associate_does_not_cancel_senior_words():
    assert check_listing(job(title="Senior Associate, Engineering"), 24, NOW) == "senior title"
    assert check_listing(job(title="Associate Director, Platform"), 24, NOW) == "senior title"
    assert check_listing(job(title="Supervising Associate, Data"), 24, NOW) == "senior title"
    assert check_listing(job(title="Graduate Associate Engineer"), 24, NOW) is None
    assert check_listing(job(title="Junior Team Lead Trainee"), 24, NOW) is None


def test_finance_legal_pharma_titles_are_non_tech():
    for title in ("KYC Junior Analyst", "Sanctions Junior Analyst", "Senior Pharmacovigilance Associate",
                  "Clinical Research Associate II", "Payroll Associate", "Customer Care Representative-Associate",
                  "Associate, Credit Portfolio Management"):
        assert check_listing(job(title=title.replace("Senior ", "")), 24, NOW) in ("non-tech role",), title
    for title in ("Junior Data Analyst", "Junior Business Analyst", "IT Support Analyst", "Junior Software Engineer - Retail Banking",
                  "Junior Security Analyst", "Model Validation Analyst"):
        assert check_listing(job(title=title), 24, NOW) is None, title


def test_work_mode_outside_warsaw():
    from jobhunt.prefilter import work_mode_reason
    li = dict(source="linkedin", remote=True)
    assert work_mode_reason(job(location="Gdańsk", description="Location: Gdańsk (Hybrid). React and TypeScript.", **li))
    assert work_mode_reason(job(location="Cracow", description="Build backend services in Python and Go.", **li))
    assert work_mode_reason(job(location="Cracow", description="This role is fully remote. Python.", **li)) is None
    assert work_mode_reason(job(location="Poland", description="Hybrid: 2 days in our Warsaw office.", **li)) is None
    assert work_mode_reason(job(location="Warszawa", description="Hybrid, 3 days in the office.", **li)) is None
    # Polish boards report the work mode themselves: a remote JustJoin job in Gdańsk stays.
    assert work_mode_reason(job(location="Gdańsk", remote=True, description="Python developer.")) is None


def test_experience_first_numbered_levels_phd_and_manual_titles():
    # Experience named before the number.
    assert required_years("Proven experience of 5+ years in DevOps, Platform Engineering") == 5
    assert required_years("Experience: 3 years with Python") == 3
    assert required_years("Experience of 2+ years is nice to have") is None
    # Numbered senior levels and PhD roles.
    assert check_listing(job(title="Security Software Engineer 5"), 24, NOW, mode="full") == "senior title"
    assert check_listing(job(title="Software Engineer 2 - .NET"), 24, NOW, mode="full") is None
    assert check_listing(job(title="Software Engineer, Early Career, PhD"), 24, NOW, mode="full") == "PhD required"
    assert check_details(job(description="Masz stopień PhD w dziedzinie informatyki.")) == "PhD required"
    assert check_details(job(description="A PhD is a plus but not required.")) is None
    # Manual / service work.
    for title in ("PRACOWNIK BUDOWLANY (K/M)", "KURIER ( K, M)", "DIAGNOSTA SAMOCHODOWY / MECHANIK",
                  "OPERATOR/KA MINIKOPARKI, OPERATOR/KA ŁADOWARKI", "Room Attendant / Accommodation Attendant",
                  "Pielęgniarz / Pielęgniarka", "Spedytor"):
        assert check_listing(job(title=title), 24, NOW, mode="full") == "non-tech role", title
    assert check_listing(job(title="Computer Operator I - Poland"), 24, NOW, mode="full") is None


def test_worldwide_and_eu_remote_allowed_country_pinned_dropped():
    """remote="region": EU/worldwide remote is fine; remote pinned to another country is not."""
    li = dict(source="linkedin", remote=True)
    assert check_listing(job(location="Worldwide", **li), 24, NOW) is None
    assert check_listing(job(location="EMEA", **li), 24, NOW) is None
    assert check_listing(job(location="European Union", **li), 24, NOW) is None
    assert check_listing(job(location="Remote", **li), 24, NOW) is None
    assert check_listing(job(location="Canada", **li), 24, NOW).startswith("location")
    assert check_listing(job(location="United States", **li), 24, NOW).startswith("location")
    gh = dict(source="greenhouse")
    assert check_listing(job(location="Remote - Europe", **gh), 24, NOW) is None
    assert check_listing(job(location="Remote - Anywhere", **gh), 24, NOW) is None


def test_remote_residency_restriction():
    from jobhunt.prefilter import remote_residency_reason
    ww = dict(source="linkedin", remote=True, location="Worldwide")
    assert remote_residency_reason(job(description="Candidates must be based in the United States. Python.", **ww))
    assert remote_residency_reason(job(description="You must be legally authorized to work in the US.", **ww))
    assert remote_residency_reason(job(description="Fully remote, anywhere in the world. Python.", **ww)) is None
    assert remote_residency_reason(job(description="Remote within the US or the EU.", **ww)) is None
    # jobs in the user's city are never affected
    assert remote_residency_reason(job(location="Warszawa", description="Must be based in the US.")) is None


def test_polish_language_skills_level():
    from jobhunt.prefilter import POLISH_REQUIRED_TEXT
    assert POLISH_REQUIRED_TEXT.search("Professional English and Polish language skills (C1)")
    assert not POLISH_REQUIRED_TEXT.search("Polish language skills are a plus")


def test_polish_titles_and_more_non_tech():
    from jobhunt.prefilter import NON_TECH_TITLE, TECH_TITLE, title_written_in

    def polish_title(t):
        return title_written_in(t, "polish")
    for t in ["Recepcjonista", "Pomocnik Konserwatora Urządzeń Dźwigowych (k/m/nb)", "Elektromonter / Elektromonterka",
              "Młodszy Specjalista / Młodsza Specjalistka ds. zarządzania usługami cyberbezpieczeństwa",
              "Asystent Projektanta / Asystentka Projektantki w branży mostowej", "Pracownik/Pracownica Sklepu"]:
        assert polish_title(t), t
        assert check_listing(job(title=t), 24, NOW, mode="full") in ("Polish title", "non-tech role"), t
    assert check_listing(job(title="Młodszy Programista Python"), 24, NOW, mode="full") is None
    for t in ["Informatyk / IT Specialist I (m/k/x)", "Software Engineer - Kraków", "Data Analyst (Łódź)",
              "QA Engineer", "Junior Python Developer", "Data Analyst (Python / SQL / dbt)"]:
        assert not polish_title(t), t
    for t in ["Funds Reconciliation Analyst (Hybrid)", "Purchasing Intern", "Litigation (Junior) Associate (m/f/d)",
              "Transportation Procurement Analyst", "Office Assistant", "Complaints Specialist - Medical Device Industry"]:
        assert NON_TECH_TITLE.search(t) and not TECH_TITLE.search(t), t


def test_years_without_the_word_experience():
    """Requirements where 'experience' does not follow the years."""
    assert required_years("9+ years in a corporate IT environment in a similar positions") == 9
    assert required_years("Must have - 6+ years in DevOps, platform or SRE engineering.") == 6
    assert required_years("- 4+ years in commercial software development,") == 4
    assert required_years("We are seeking a skilled 10 years of Java Full Stack Developer") == 10
    assert required_years("Minimum 5 years in a similar role") == 5
    # not requirements
    assert required_years("A company with 15 years on the market") is None
    assert required_years("We have 12+ years of delivering projects for clients") is None
    assert required_years("Contract for 4 years in Warsaw") is None
    assert required_years("5+ years in SAP is a plus") is None


def test_senior_role_in_description():
    """Neutral titles hiding a senior role."""
    assert check_details(job(description="To strengthen our team, we are looking for a Senior Solution Engineer.")) \
        == "senior role (description)"
    assert check_details(job(description="Hands-on MLOps or ML engineering at senior level.")) == "senior role (description)"
    assert check_details(job(description="You will learn from senior engineers.")) is None
    assert check_details(job(description="Acme is looking for a Senior / Mid Virtualization Engineer.")) is None


def test_date_only_posting_counts_until_end_of_day():
    """LinkedIn cards carry only a date; a job dated yesterday must not be dropped as "older than 24h"."""
    from datetime import datetime, timezone
    now = datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc)
    job = Job(source="linkedin", source_id="1", title="Junior Python Developer", company="X", url="u",
              location="Warsaw", posted_at="2026-10-16")
    assert check_listing(job, 24, now=now, mode="full") is None
    job.posted_at = "2026-10-15"
    assert check_listing(job, 24, now=now, mode="full") == "older than 24h"
    job.posted_at = "2026-10-16T10:00:00Z"
    assert check_listing(job, 24, now=now, mode="full") == "older than 24h"


BERLIN = settings.Settings(country="Germany", country_code="DE", country_names=("germany", "deutschland"), city="Berlin",
                           city_names=("berlin",), languages=("english",), remote="country", max_years=2,
                           tech_only=True)


def gh(**kw) -> Job:
    return job(source="greenhouse", **kw)


def test_other_country_location_rules():
    settings.use(BERLIN)
    assert check_listing(gh(location="Munich, Germany"), 24, NOW) == "location Munich, Germany"
    assert check_listing(gh(location="Munich, Bavaria, Germany", remote=True), 24, NOW) is None
    assert check_listing(gh(location="Berlin, Germany"), 24, NOW) is None
    assert check_listing(gh(location="Remote, Germany"), 24, NOW) is None
    assert check_listing(gh(location="Germany"), 24, NOW) is None
    assert check_listing(gh(location="Warszawa"), 24, NOW) == "location Warszawa"
    assert check_listing(gh(location="Remote - EMEA"), 24, NOW) == "location Remote - EMEA"
    assert check_listing(gh(location="Worldwide", remote=True), 24, NOW) == "location Worldwide"
    hybrid = job(source="linkedin", location="Munich, Germany", remote=True, description="Hybrid, 2 days in the office.")
    assert work_mode_reason(hybrid) == "hybrid/on-site outside Berlin (munich: 'Hybrid')"


def test_remote_scope_and_relocation_settings():
    settings.use(replace(BERLIN, remote="region"))  # no region_names: only worldwide wording counts
    assert check_listing(gh(location="Remote - EMEA"), 24, NOW).startswith("location")
    settings.use(replace(BERLIN, remote="region", region_names=("europe", "emea", "eu")))
    assert check_listing(gh(location="Remote - EMEA"), 24, NOW) is None
    assert check_listing(gh(location="Remote, India"), 24, NOW).startswith("location")
    assert check_listing(gh(location="Remote"), 24, NOW).startswith("location")
    settings.use(replace(BERLIN, remote="worldwide"))
    assert check_listing(gh(location="Remote"), 24, NOW) is None
    assert check_listing(gh(location="Remote - Anywhere"), 24, NOW) is None
    assert check_listing(gh(location="Remote, India"), 24, NOW).startswith("location")
    assert check_listing(gh(location="Spain", remote=True), 24, NOW).startswith("location")
    settings.use(replace(BERLIN, relocate=True))
    assert check_listing(gh(location="Munich, Germany"), 24, NOW) is None
    assert work_mode_reason(job(location="Munich, Germany", description="Hybrid, 2 days in the office.")) is None


def test_other_country_language_rules():
    settings.use(BERLIN)
    assert check_details(gh(location="Berlin", description="Fluent German required. Python.")) \
        == "German required (description)"
    assert check_details(gh(location="Berlin", description="Requirements: fluent Polish and English.")) \
        == "Polish required (description)"
    assert check_listing(gh(location="Berlin", languages=["German:C1"]), 24, NOW) == "German required (listing)"
    assert check_listing(gh(location="Berlin", languages=["Polish:B2"]), 24, NOW) == "Polish required (listing)"
    # "Germany" is not the German language
    assert check_details(gh(location="Berlin", description="Fluent English, based in Germany. Python.")) is None
    # the search country's own language first
    assert language_flags("Fluent German required. Fluent Polish required.") == ["German required",
                                                                                   "Polish required"]
    settings.use(replace(BERLIN, languages=("english", "german")))
    assert check_details(gh(location="Berlin", description="Fluent German required. Python.")) is None
    assert check_listing(gh(location="Berlin", languages=["Deutsch: C1"]), 24, NOW) is None
    assert check_listing(gh(location="Berlin", title="Service Desk Agent with German"), 24, NOW) is None


def test_english_is_just_another_language():
    settings.use(replace(BERLIN, languages=("german",)))
    assert check_listing(gh(location="Berlin", languages=["en:B2"]), 24, NOW) == "English required (listing)"
    assert check_details(gh(location="Berlin", description="Fluent English required.")) \
        == "English required (description)"
    english = "We offer a great team and you will work with our engineers to build the requirements of the product."
    assert check_details(gh(location="Berlin", description=english)) == "description in English"


def test_years_limit_follows_settings():
    settings.use(BERLIN)
    assert MAX_YEARS["full"] == 2 and MAX_YEARS["narrow"] == 2
    assert check_details(gh(location="Berlin", description="3+ years of commercial experience with Python.")) \
        == "3+ years required"
    settings.use(replace(BERLIN, max_years=5))
    assert MAX_YEARS["full"] == 5 and MAX_YEARS["narrow"] == 2


def test_a_polish_speaker_keeps_polish_jobs():
    settings.use(replace(BERLIN, country="Poland", country_code="PL", country_names=("poland", "polska"),
                         city="Warsaw", city_names=("warsaw", "warszawa"), languages=("english", "polish")))
    assert check_details(job(description="Requirements: fluent Polish and English. Python.")) is None
    assert check_listing(job(languages=["Polish:C1"]), 24, NOW) is None
    assert check_listing(job(title="Młodszy Specjalista ds. Systemów"), 24, NOW) is None
    assert check_listing(job(title="Kierowca"), 24, NOW) == "non-tech role"


def test_postings_written_in_other_big_languages_are_dropped_for_english_speakers():
    settings.use(BERLIN)
    german = ("Wir suchen dich für unser Team. Deine Aufgaben: Entwicklung und Betreuung unserer Systeme. "
              "Das bieten wir: flexible Arbeitszeiten, eine moderne Umgebung und Weiterbildung. Anforderungen: "
              "Kenntnisse in Python, Erfahrung mit Linux oder Docker sind von Vorteil, sowie gute Teamarbeit.")
    assert check_details(gh(location="Berlin", description=german)) == "description in German"
    assert check_listing(job(title="Mitarbeiter (m/w/d) Kundenservice", location="Berlin"), 24, NOW) == "German title"
    # A German tech title is left to the description check.
    assert check_listing(job(title="Softwareentwickler (m/w/d)", location="Berlin"), 24, NOW) is None
    french = ("Nous recherchons un développeur pour notre équipe. Vos missions : développer des services avec Python "
              "et les maintenir dans le cloud. Votre profil : une première expérience, des compétences en SQL. "
              "Nous offrons un poste stable dans une entreprise qui est en croissance, avec des avantages pour vous.")
    assert check_details(gh(location="Berlin", description=french)) == "description in French"
    settings.use(replace(BERLIN, languages=("english", "german")))
    assert check_details(gh(location="Berlin", description=german)) is None


def test_german_requirement_wordings():
    settings.use(BERLIN)
    for text in ("Fluency in English and German.", "German language skills are a must.", "Good German skills.",
                 "Strong German communication skills.", "Business-level German is essential.",
                 "Verhandlungssichere Deutschkenntnisse.", "Sehr gute Deutschkenntnisse.", "You are a German speaker."):
        assert check_details(gh(location="Berlin", description=f"Marketing role. {text}")) \
            == "German required (description)", text
    for text in ("Knowledge of the German market.", "German is not required.", "No German required.",
                 "Influence on the German market.", "German is a plus.", "Deutschkenntnisse sind von Vorteil.",
                 "Good German skills are a bonus.", "A company with strong German roots."):
        assert check_details(gh(location="Berlin", description=f"Marketing role. {text}")) is None, text


def test_other_cities_match_places_not_word_starts():
    s = replace(BERLIN, other_cities=("essen", "hamburg", "munich"))
    settings.use(s)
    remote = dict(location="Germany", remote=True)
    assert work_mode_reason(job(**remote, description="Essential: Canva. This is a hybrid role.")) is None
    assert work_mode_reason(job(**remote, description="Hybrid: 2 days a week in our Essen office.")) \
        .startswith("hybrid/on-site outside Berlin (essen")
    assert work_mode_reason(job(**remote, description="Location: Hamburg. Hybrid work model.")) \
        .startswith("hybrid/on-site outside Berlin (hamburg")
    assert s.other_city("hessen, germany") is None and s.other_city("essen, germany") == "essen"


def test_manager_titles_in_the_users_own_non_it_roles():
    marketing = replace(BERLIN, tech_only=False, roles=("social media", "content marketing"))
    settings.use(marketing)
    for title in ("Marketing Manager (m/w/d)", "Social Media Manager", "Content Manager (all genders)"):
        assert check_listing(gh(title=title, location="Berlin"), 24, NOW, mode="full") is None, title
    settings.use(replace(marketing, roles=("marketing assistant", "social media")))
    for title in ("Senior Marketing Manager", "Account Manager", "Head of Content Marketing", "Assistant Store Manager"):
        assert check_listing(gh(title=title, location="Berlin"), 24, NOW, mode="full") == "senior title", title
    settings.use(replace(marketing, tech_only=True, roles=("software engineer",)))
    assert check_listing(gh(title="Software Engineering Manager", location="Berlin"), 24, NOW, mode="full") \
        == "senior title"


def test_titles_shared_by_two_languages_are_labelled_with_the_local_one():
    settings.use(replace(BERLIN, tech_only=False))
    assert check_listing(gh(title="Referent Marketing (m/w/d)", location="Berlin"), 24, NOW) == "German title"


def test_language_speaking_titles():
    settings.use(replace(BERLIN, tech_only=False, roles=("performance marketing",)))
    assert check_listing(gh(title="German Speaking Performance Marketing Manager", location="Berlin"), 24, NOW) \
        == "German required (title)"


def test_a_desirable_heading_does_not_hide_required_years():
    from jobhunt.prefilter import required_years
    text = "Desirable:\n - Practical .NET experience\nExperience\n - Minimum 5 years of web development experience"
    assert required_years(text) == 5
