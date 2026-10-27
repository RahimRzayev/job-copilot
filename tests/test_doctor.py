"""`jobhunt init` and `jobhunt doctor` on a fresh folder."""
import json
import shutil
from pathlib import Path

import pytest

from jobhunt import cover, cvtailor, doctor, secret

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.yaml"


@pytest.fixture(autouse=True)
def no_keychain_exporter_or_font(monkeypatch):
    monkeypatch.setattr(secret, "get_password", lambda acct=None, svc=None: None)
    monkeypatch.setattr(cvtailor, "pdf_exporter", lambda: None)
    monkeypatch.setattr(cover, "FONTS", [])


def test_doctor_on_an_empty_folder(tmp_path):
    text, ok = doctor.run(tmp_path)
    lines = text.strip().splitlines()
    assert not ok and len(lines) == 10
    assert all(line.startswith("❌") for line in lines)
    assert "profile: profile/profile.yaml missing → fix: run `uv run jobhunt init`" in lines[0]
    assert any("Playwright MCP: .mcp.json missing" in line and "claude mcp add" in line for line in lines)
    assert any(line.startswith("❌ default password (optional)") for line in lines)


def test_init_then_doctor(tmp_path):
    first = doctor.init(tmp_path)
    assert first["profile_created"] and Path(first["db"]).exists()
    assert (tmp_path / "companies.yaml").read_text() == doctor.COMPANIES_EXAMPLE.read_text()
    assert doctor.init(tmp_path)["profile_created"] is False  # idempotent
    shutil.copyfile(FIXTURE_PROFILE, tmp_path / "profile" / "profile.yaml")
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "cv.pdf").write_bytes(b"%PDF")
    (tmp_path / "tests" / "fixtures" / "cv-data.pdf").write_bytes(b"%PDF")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"playwright": {"command": "npx"}}}))
    (tmp_path / "companies.yaml").write_text("companies:\n  - {name: Acme, ats: greenhouse, slug: acme}\n")
    text, ok = doctor.run(tmp_path)
    assert ok, text
    for name in ("profile", "search settings", "CV file", "Playwright MCP", "(0 jobs)",
                 "companies.yaml: 1 companies"):
        assert any(line.startswith("✅") and name in line for line in text.splitlines()), name
    assert "❌ default password (optional)" in text  # optional: does not fail the run


def test_doctor_names_unknown_ats(tmp_path):
    (tmp_path / "companies.yaml").write_text("companies:\n  - {name: Acme, ats: nosuch, slug: acme}\n")
    assert "unknown ats for: Acme" in doctor.run(tmp_path)[0]


def test_doctor_names_the_missing_word_cvs(tmp_path):
    doctor.init(tmp_path)
    shutil.copyfile(FIXTURE_PROFILE, tmp_path / "profile" / "profile.yaml")
    (tmp_path / "profile" / "cv-source" / "cv.docx").write_bytes(b"PK")
    line = next(x for x in doctor.run(tmp_path)[0].splitlines() if "Word CV" in x)
    assert line.startswith("❌") and "profile/cv-source/cv-data.docx" in line and "cv-source/cv.docx" not in line
    (tmp_path / "profile" / "cv-source" / "data-analyst").mkdir()
    (tmp_path / "profile" / "cv-source" / "data-analyst" / "cv-data.docx").write_bytes(b"PK")
    line = next(x for x in doctor.run(tmp_path)[0].splitlines() if "Word CV" in x)
    assert line.startswith("✅") and "data-analyst/cv-data.docx" in line


def test_doctor_names_a_missing_cover_letter_font(tmp_path, monkeypatch):
    assert "❌ cover letter font (optional)" in doctor.run(tmp_path)[0]
    font = tmp_path / "Font.ttf"
    font.write_bytes(b"")
    monkeypatch.setattr(cover, "FONTS", [(str(font), str(font))])
    assert "✅ cover letter font (optional): Font.ttf" in doctor.run(tmp_path)[0]


def test_export_test_reports_a_failing_exporter(tmp_path, monkeypatch):
    monkeypatch.setattr(cvtailor, "pdf_exporter", lambda: "word")
    assert "--export-test" in doctor.run(tmp_path)[0]

    def broken():
        raise RuntimeError("Word export failed: -1708")
    monkeypatch.setattr(cvtailor, "test_export", broken)
    text = doctor.run(tmp_path, export_test=True)[0]
    assert "❌ PDF exporter for tailored CVs (optional): word found, but the test export failed: Word export failed" in text
    monkeypatch.setattr(cvtailor, "test_export", lambda: "word")
    assert "✅ PDF exporter for tailored CVs (optional): word (test export worked)" in doctor.run(tmp_path, export_test=True)[0]
