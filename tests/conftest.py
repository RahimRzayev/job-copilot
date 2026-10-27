"""Every test runs with the same example search settings (a Warsaw, Poland job seeker who works in English and
Ukrainian), so no test depends on whoever runs it having a profile/profile.yaml. A test that needs other settings calls
`settings.use(...)` itself; this fixture resets afterwards."""
import pytest

from jobhunt import settings

EXAMPLE = settings.Settings(
    country="Poland", country_code="PL", country_names=("poland", "polska"),
    city="Warsaw", city_names=("warsaw", "warszawa", "varsovie", "warschau"),
    other_cities=("krak", "cracow", "wrocław", "wroclaw", "gdańsk", "gdansk", "gdynia", "sopot", "poznań", "poznan",
                  "łódź", "lodz", "katowice", "gliwice", "szczecin", "bydgoszcz", "lublin", "białystok", "bialystok",
                  "rzeszów", "rzeszow", "toruń", "torun", "opole", "kielce", "olsztyn", "bielsko", "częstochowa",
                  "czestochowa", "radom"),
    remote="region", region_names=("europe", "emea", "european union", "eu", "cet", "cee"),
    languages=("english", "ukrainian"), max_years=3, tech_only=True, levels=("internship", "working student", "junior"),
    keywords=("intern", "junior", "graduate", "trainee", "working student"), currency="PLN", salary_monthly=6500,
    job_email="jobs.example@gmail.com",
)


@pytest.fixture(autouse=True)
def example_settings():
    settings.use(EXAMPLE)
    yield EXAMPLE
    settings.use(None)
