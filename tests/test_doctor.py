"""`jobhunt init` and `jobhunt doctor` on a fresh folder."""
import json
import shutil
from pathlib import Path

import pytest

from jobhunt import cvtailor, doctor, secret

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.yaml"


@pytest.fixture(autouse=True)
def no_keychain_or_exporter(monkeypatch):
    monkeypatch.setattr(secret, "get_password", lambda acct=None, svc=None: None)
    monkeypatch.setattr(cvtailor, "pdf_exporter", lambda: None)


def test_doctor_on_an_empty_folder(tmp_path):
    text, ok = doctor.run(tmp_path)
    lines = text.strip().splitlines()
    assert not ok and len(lines) == 8
    assert all(line.startswith("❌") for line in lines)
    assert "profile: profile/profile.yaml missing → fix: run `uv run jobhunt init`" in lines[0]
    assert any("Playwright MCP: .mcp.json missing" in line and "claude mcp add" in line for line in lines)
    assert any(line.startswith("❌ default password (optional)") for line in lines)


def test_init_then_doctor(tmp_path):
    first = doctor.init(tmp_path)
    assert first["profile_created"] and Path(first["db"]).exists()
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
