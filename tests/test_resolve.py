from pathlib import Path

from jobhunt.resolve import _best, slugs, title_match

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.yaml"


def test_slugs_cover_suffix_words():
    assert "northwind-travel-group" in slugs("Northwind Travel Group")
    assert slugs("Globex") == ["globex"]
    assert "initech" in slugs("Initech Polska sp. z o.o.")


def test_title_match_ignores_gender_tags():
    assert title_match("Operations Engineer (f/m)", "Operations Engineer") == 1.0
    assert title_match("Junior Data Analyst", "Senior Data Engineer") < 0.6


def test_best_prefers_home_country_on_tie():
    postings = [("Technical Support Agent", "Cyprus", "https://a"), ("Technical Support Agent", "Warsaw, Poland", "https://b")]
    assert _best("Technical Support Agent", postings)[0] == "https://b"
    assert _best("Technical Support Agent", [("Head of Sales", "Poland", "https://c")]) is None


def test_cover_pdf_renders(tmp_path, monkeypatch):
    from jobhunt import cover, formfill, pipeline, settings
    from jobhunt.db import connect
    from jobhunt.normalize import Job
    conn = connect(tmp_path / "t.db")
    pipeline.store(conn, Job(source="x", source_id="1", title="Junior Developer", company="Zażółć Bölgə",
                             url="https://x"), "scored")
    conn.commit()
    # The fictional test profile and a temp output dir, never profile/ or data/ of the checkout.
    for module in (cover, formfill, settings):
        monkeypatch.setattr(module, "PROFILE", FIXTURE_PROFILE, raising=False)
    monkeypatch.setattr(cover, "OUT", tmp_path / "covers")
    path = cover.render(conn, 1, "Dear team,\n\nPara with ł and ə.\n\nKind regards,\nAlex Example")
    assert path.name.endswith("Cover_Letter.pdf") and path.read_bytes()[:4] == b"%PDF"
    assert tmp_path in path.parents


def test_sf_rmk_board_parses_rows_and_dates():
    import httpx
    from jobhunt.sources.ats_boards import _sf_rmk
    from jobhunt.sources.base import Http

    row = ('<tr class="data-row"><td class="colTitle"><span class="jobTitle hidden-phone">'
           '<a href="/job/Warsaw-Junior-Data-Quality-Analyst/1421333633/" class="jobTitle-link">Junior Data Quality '
           'Analyst (M/F/D)</a></span></td><td class="colLocation hidden-phone"><span class="jobLocation">'
           ' Warsaw, Masovian, PL, 02-677 </span></td><td class="colDate hidden-phone"><span class="jobDate">'
           '11 Sept 2026 </span></td></tr>')

    def handler(request):
        start = int(request.url.params.get("startrow", 0))
        return httpx.Response(200, text=row if start == 0 else "<table></table>")
    http = Http(delay=0)
    http.client = httpx.Client(transport=httpx.MockTransport(handler))
    [j] = _sf_rmk(http, "Acme", "jobs.acme.example|Warsaw")
    assert (j.source_id, j.title, j.location) == ("1421333633", "Junior Data Quality Analyst (M/F/D)", "Warsaw")
    assert j.posted_at.startswith("2026-09-11")
    assert j.employer_url == "https://jobs.acme.example/job/Warsaw-Junior-Data-Quality-Analyst/1421333633/"


def test_workday_board_searches_the_city_too():
    """A posting whose location names only a site in the city ("WARSAW PLANT") is missing from the country search."""
    from jobhunt.sources.ats_boards import _workday

    class FakeHttp:
        def post(self, url, json):
            posts = {"Poland": [{"title": "Data Engineer", "externalPath": "/job/A_1", "locationsText": "Warsaw, Poland"}],
                     "Warsaw": [{"title": "Data Engineer", "externalPath": "/job/A_1", "locationsText": "Warsaw, Poland"},
                                {"title": "IT Development Program", "externalPath": "/job/B_2",
                                 "locationsText": "WARSAW PLANT"}]}[json["searchText"]]

            class R:
                def json(self):
                    return {"jobPostings": posts, "total": len(posts)}
            return R()

    jobs = _workday(FakeHttp(), "Globex", "globex/wd5/1000")
    assert [j.title for j in jobs] == ["Data Engineer", "IT Development Program"]
