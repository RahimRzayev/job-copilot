"""`jobhunt secret`: the password goes keychain -> clipboard and is never printed."""
import keyring
import pytest

from jobhunt import cli, secret

PASSWORD = "Sample-Pass9!"


@pytest.fixture
def clipboard(monkeypatch):
    """Fake keychain and clipboard tool; returns what the clipboard received."""
    store = {("jobhunt-default", "jobs.example@gmail.com"): PASSWORD}
    received = []
    monkeypatch.setattr(keyring, "get_password", lambda svc, acct: store.get((svc, acct)))
    monkeypatch.setattr(keyring, "set_password", lambda svc, acct, pw: store.__setitem__((svc, acct), pw))
    monkeypatch.setattr(secret, "_clipboard_cmd", lambda clear=False: ["fake-clip"])

    def fake_run(cmd, input=b"", **kwargs):
        received.append(input.decode())
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr(secret.subprocess, "run", fake_run)
    return received


def test_copy_prints_only_the_length(clipboard, capsys):
    cli.main(["secret", "copy"])
    out = capsys.readouterr()
    assert out.out.strip() == f"copied {len(PASSWORD)} characters"
    assert PASSWORD not in out.out + out.err
    assert clipboard == [PASSWORD]


def test_copy_variant_for_sites_with_rules(clipboard, capsys):
    cli.main(["secret", "copy", "--max", "8", "--suffix", "#"])
    out = capsys.readouterr()
    assert out.out.strip() == "copied 8 characters" and PASSWORD[:7] not in out.out + out.err
    assert clipboard == [PASSWORD[:7] + "#"]


def test_check_and_clear(clipboard, capsys):
    cli.main(["secret", "check"])
    out = capsys.readouterr().out
    assert '"set": true' in out and f'"length": {len(PASSWORD)}' in out and PASSWORD not in out
    cli.main(["secret", "clear"])
    assert clipboard == [""]


def test_set_asks_twice(clipboard, monkeypatch, capsys):
    answers = iter(["New-Pass-123", "New-Pass-123"])
    monkeypatch.setattr(secret.getpass, "getpass", lambda prompt="": next(answers))
    cli.main(["secret", "set"])
    assert "New-Pass-123" not in capsys.readouterr().out
    assert secret.check() == {"set": True, "length": 12}


def test_set_refuses_a_mismatch(clipboard, monkeypatch):
    answers = iter(["one", "two"])
    monkeypatch.setattr(secret.getpass, "getpass", lambda prompt="": next(answers))
    with pytest.raises(SystemExit, match="differ"):
        cli.main(["secret", "set"])


def test_copy_without_a_stored_password_says_how_to_set_it(clipboard, monkeypatch):
    monkeypatch.setattr(keyring, "get_password", lambda svc, acct: None)
    with pytest.raises(SystemExit, match="secret set"):
        secret.copy()
    assert clipboard == []


def test_clipboard_tool_per_platform(monkeypatch):
    monkeypatch.setattr(secret.platform, "system", lambda: "Darwin")
    assert secret._clipboard_cmd() == ["pbcopy"]
    monkeypatch.setattr(secret.platform, "system", lambda: "Windows")
    assert secret._clipboard_cmd() == ["clip"]
    monkeypatch.setattr(secret.platform, "system", lambda: "Linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(secret.shutil, "which", lambda name: "/usr/bin/" + name if name == "xsel" else None)
    assert secret._clipboard_cmd() == ["xsel", "-b", "-i"] and secret._clipboard_cmd(clear=True) == ["xsel", "-b", "-c"]
