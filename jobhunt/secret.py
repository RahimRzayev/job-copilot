"""The default password for job-site accounts, kept in the OS keychain (macOS Keychain, Windows Credential Manager,
Linux Secret Service) through `keyring`, under settings.password_service and the job email.

Claude never sees it: `jobhunt secret copy` puts it on the clipboard and prints only its length, Claude pastes it
into the form with Ctrl/Cmd+V, checks the field's length, then runs `jobhunt secret clear`. The secret is never
printed, logged, written to a file or passed as a command-line argument (the clipboard tool reads it from stdin).
"""

import getpass
import os
import platform
import shutil
import subprocess

from . import settings


def service() -> str:
    return settings.get().password_service or "jobhunt-default"


def account() -> str:
    """The job email: search.job_email, else personal.email."""
    s = settings.get()
    if s.job_email:
        return s.job_email
    from .formfill import load_profile
    try:
        return ((load_profile().get("personal") or {}).get("email") or "").strip()
    except SystemExit:
        return ""


def _require_account() -> str:
    acct = account()
    if not acct:
        raise SystemExit("no job email yet: set search.job_email (or personal.email) in profile/profile.yaml")
    return acct


def get_password(acct: str | None = None, svc: str | None = None) -> str | None:
    import keyring
    from keyring.errors import KeyringError
    try:
        return keyring.get_password(svc or service(), acct or _require_account())
    except KeyringError as e:
        raise SystemExit(f"OS keychain unavailable ({e.__class__.__name__}). Linux: install and unlock "
                         "gnome-keyring or KWallet.") from None


def set_password(password: str, acct: str | None = None) -> None:
    import keyring
    from keyring.errors import KeyringError
    try:
        keyring.set_password(service(), acct or _require_account(), password)
    except KeyringError as e:
        raise SystemExit(f"OS keychain unavailable ({e.__class__.__name__}). Linux: install and unlock "
                         "gnome-keyring or KWallet.") from None


def _clipboard_cmd(clear: bool = False) -> list[str]:
    """The platform's clipboard writer; it reads the text from stdin."""
    system = platform.system()
    if system == "Darwin":
        return ["pbcopy"]
    if system == "Windows":
        return ["clip"]
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-copy", "--clear"] if clear else ["wl-copy"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard"]
    if shutil.which("xsel"):
        return ["xsel", "-b", "-c"] if clear else ["xsel", "-b", "-i"]
    raise SystemExit("no clipboard tool found: install wl-clipboard (Wayland), xclip or xsel")


def _to_clipboard(text: str) -> None:
    cmd = _clipboard_cmd(clear=not text)
    # clip.exe reads UTF-16 with a byte-order mark; the others take UTF-8.
    data = text.encode("utf-16") if cmd[0] == "clip" and text else text.encode()
    # No captured output: xclip/wl-copy keep a child serving the clipboard, and it would hold our pipes open.
    res = subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    if res.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed (exit {res.returncode})")


def variant(password: str, max_len: int | None = None, suffix: str = "") -> str:
    """The password as one site needs it: `suffix` appended (a site that wants another symbol), then cut to `max_len`
    keeping the suffix (a site with a length cap). Use the same flags on every sign-in to that site."""
    if max_len is not None and max_len <= len(suffix):
        raise SystemExit("--max must be longer than --suffix")
    if max_len is not None:
        password = password[:max_len - len(suffix)]
    return password + suffix


def copy(max_len: int | None = None, suffix: str = "") -> int:
    """Put the password on the clipboard; returns only its length."""
    password = get_password()
    if not password:
        raise SystemExit(f"no password stored under {service()!r}: run `uv run jobhunt secret set` in your own terminal")
    text = variant(password, max_len, suffix)
    _to_clipboard(text)
    return len(text)


def clear() -> None:
    _to_clipboard("")


def check() -> dict:
    password = get_password()
    return {"set": bool(password), "length": len(password) if password else 0}


def prompt_and_set() -> int:
    """Ask twice without echo (run it in your own terminal, never through Claude); returns the stored length."""
    acct = _require_account()
    first = getpass.getpass(f"Default password for job-site accounts ({acct}): ")
    if not first:
        raise SystemExit("empty password, nothing stored")
    if getpass.getpass("Again: ") != first:
        raise SystemExit("the two entries differ, nothing stored")
    set_password(first, acct)
    return len(first)
