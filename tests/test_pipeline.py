from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobhunt import pipeline, settings
from jobhunt.db import connect
from jobhunt.normalize import Job

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.yaml"


@pytest.fixture(autouse=True)
def fixture_profile(monkeypatch):
    """process() pre-scores with the profile's skills: use the fictional test profile, not profile/profile.yaml."""
    monkeypatch.setattr(pipeline.prescore, "PROFILE", FIXTURE_PROFILE)


def _job(**kw) -> Job:
    base = dict(source="justjoin", source_id="a1", title="Junior Python Developer", company="Acme",
                url="https://x", location="Warszawa", description="Python developer role, English.",
                posted_at=datetime.now(timezone.utc).isoformat())
    base.update(kw)
    return Job(**base)


def test_same_role_other_city_or_relisted_id_is_duplicate(tmp_path):
    conn = connect(tmp_path / "t.db")
    r = pipeline.Report()
    pipeline.process(conn, None, [_job()], 24, 3, r)
    assert r.added == 1
    r = pipeline.Report()
    # Same board id with a different dedupe key (old key format) and the same role in another city.
    pipeline.process(conn, None, [_job(location="Warsaw, Poland", remote=True),
                                  _job(source="linkedin", source_id="99", location="Poland", remote=True)], 24, 3, r)
    assert r.added == 0
    reasons = [x[0] for x in conn.execute("SELECT filter_reason FROM jobs WHERE status = 'filtered'")]
    assert reasons and all(x.startswith("duplicate") for x in reasons)


def test_refilter_window_only_for_programmes(tmp_path):
    conn = connect(tmp_path / "t.db")
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    for i, title in enumerate(("Junior Python Developer", "Software Engineering Intern")):
        pipeline.store(conn, _job(source_id=str(i), title=title, posted_at=old), "filtered", "older than 24h")
    conn.commit()
    out = pipeline.refilter(conn, hours=168)
    titles = [conn.execute("SELECT title FROM jobs WHERE id = ?", (i,)).fetchone()[0] for i in out["revived"]]
    assert titles == ["Software Engineering Intern"]
    assert len(pipeline.refilter(conn, hours=168, all_roles=True)["revived"]) == 1


def test_dedupe_existing_keeps_most_advanced(tmp_path):
    conn = connect(tmp_path / "t.db")
    pipeline.store(conn, _job(source_id="1"), "scored")
    pipeline.store(conn, _job(source_id="2", location="Kraków"), "applied")
    conn.commit()
    out = pipeline.dedupe_existing(conn)
    assert out["merged"] == [{"id": 1, "status": "scored", "duplicate_of": 2, "kept_status": "applied"}]


def test_ingest_uses_file_window(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(pipeline.prescore, "run", lambda conn: None)
    two_days_ago = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    item = {"source": "pracuj", "source_id": "p1", "title": "Junior Python Developer", "company": "Acme",
            "url": "https://x", "location": "Warszawa", "posted_at": two_days_ago,
            "description": "Python developer role, English."}
    f = tmp_path / "pracuj.json"
    # 72h browser scrape: the file's window applies, a 48h-old offer is kept.
    f.write_text(json.dumps({"window_hours": 72, "stats": [], "jobs": [item]}))
    assert pipeline.ingest(connect(tmp_path / "a.db"), f).added == 1
    # A plain list has no window: 24h default, the same offer is too old.
    f.write_text(json.dumps([item]))
    assert pipeline.ingest(connect(tmp_path / "b.db"), f).added == 0


def test_refilter_never_revives_duplicates(tmp_path):
    """`refilter --hours 168` must not revive copies already filtered as 'duplicate of #id'."""
    conn = connect(tmp_path / "t.db")
    pipeline.store(conn, _job(source_id="1", title="Graduate software engineer"), "scored")
    pipeline.store(conn, _job(source="linkedin", source_id="2", title="Graduate software engineer", location="Poland",
                              remote=True), "filtered", "duplicate of #1")
    conn.commit()
    out = pipeline.refilter(conn, hours=168)
    assert out["revived"] == []
    statuses = dict(conn.execute("SELECT source_id, status FROM jobs").fetchall())
    assert statuses == {"1": "scored", "2": "filtered"}


def test_company_variants_are_the_same_employer():
    from jobhunt.normalize import company_variant
    assert company_variant("Robert Globex Sp. z o.o.", "Globex")
    assert company_variant("Northwind Trading Company", "NTC")
    assert company_variant("Initech Polska", "Initech")
    assert not company_variant("Data Art", "Data")
    assert not company_variant("Globex", "Globexung")
    assert not company_variant("Snowdrift", "Snowplow")


GERMANY = settings.Settings(country="Germany", country_code="DE", country_names=("germany", "deutschland"),
                            city="Berlin", city_names=("berlin",), languages=("english", "german"))


def test_scrape_needs_settings(tmp_path):
    settings.use(settings.Settings())
    with pytest.raises(SystemExit):
        pipeline.scrape(connect(tmp_path / "t.db"))


def test_other_country_runs_only_global_sources(tmp_path, monkeypatch):
    settings.use(GERMANY)
    ran = []
    monkeypatch.setattr(pipeline, "fetch_all", lambda http, sources, *a, **k: ran.extend(sources) or [])
    monkeypatch.setattr(pipeline.prescore, "run", lambda conn: {"scored": 0})
    report = pipeline.scrape(connect(tmp_path / "t.db"))
    assert ran == ["linkedin", "company_boards"]
    assert report.disabled == ["justjoin", "nofluffjobs", "bulldogjob", "englishjobs"]


def test_other_country_linkedin_and_board_searches():
    from jobhunt.sources import ats_boards, linkedin
    settings.use(GERMANY)
    assert linkedin.location_searches() == [{"location": "Berlin, Germany"}, {"location": "Germany", "f_WT": "2"}]

    class Http:
        def __init__(self):
            self.params = []

        def get(self, url, params):
            self.params.append(params)

            class R:
                text = ""
            return R()

        def post(self, url, json):
            self.params.append(json)

            class R:
                def json(self):
                    return {"jobPostings": [], "total": 0}
            return R()

    http = Http()
    linkedin.fetch(http, hours=24)
    assert {p["location"] for p in http.params} == {"Berlin, Germany", "Germany"}
    http = Http()
    ats_boards._workday(http, "Acme", "acme/wd1/Careers")
    assert [p["searchText"] for p in http.params] == ["Germany", "Berlin"]


def test_language_reasons_share_a_bucket():
    assert pipeline._bucket("Polish required (listing)") == pipeline._bucket("description in Polish") == "Polish"
    assert pipeline._bucket("German title") == "German"
    assert pipeline._bucket("other language required") == "other language required"
    assert pipeline._bucket("non-tech role") == "non-tech role"


def test_new_watchlist_company_brings_all_its_open_jobs(tmp_path, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    board_job = _job(source="greenhouse", source_id="gh1", posted_at=old)
    conn = connect(tmp_path / "t.db")
    r = pipeline.Report()
    pipeline.process(conn, None, [board_job], 24, 3, r)
    assert r.added == 0 and "older than 168h" in r.filtered
    conn = connect(tmp_path / "t2.db")
    r = pipeline.Report()
    pipeline.process(conn, None, [board_job], 24, 3, r, new_companies=frozenset({"Acme"}))
    assert r.added == 1

    # a company counts as new until one of its career-site jobs is stored
    monkeypatch.setattr(pipeline, "_companies", lambda: [{"name": "Acme", "ats": "greenhouse", "slug": "acme"},
                                                         {"name": "Globex", "ats": "workday", "slug": "g/wd3/x"}])
    assert pipeline.new_companies(conn) == {"Globex"}
