"""Engine tests without Chrome, Keychain or network: fake cookie store, fake page opener, fake responses.

    cd template/core && uv run --with pytest --with pytest-asyncio pytest -q
"""

import asyncio
import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import webapp_mcp.session as session_module
from webapp_mcp import (
    CHROME_AI_DEFAULT_DIR,
    ApiError,
    BrowserSession,
    ChromeCookies,
    Cookie,
    LoginRequired,
    WebApp,
    WebClient,
    bearer_auth,
    resolve_user_data_dir,
)
from webapp_mcp.chrome import CHROME_DIR
from webapp_mcp.session import hide_chrome_ai, open_in_chrome, open_in_chrome_ai

NOW = datetime.now(timezone.utc)
LOGIN_URL = "https://example.com/login"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Tests must not depend on the developer's own Chrome settings."""
    for name in ("WEBAPP_CHROME", "CHROME_AI_PROFILE", "CHROME_AI_PORT", "CHROME_PROFILE", "EX_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    # Safety net: a test must never launch a real browser or launcher.
    def forbidden(args, timeout=15):
        raise AssertionError(f"a test tried to run a real command: {args}")

    monkeypatch.setattr(session_module, "_run", forbidden)


def jwt(expires: datetime) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part({'exp': int(expires.timestamp())})}.signature"


class FakeChrome:
    """Cookie store that can change during the test (to simulate a sign-in)."""

    def __init__(self, cookies: list[Cookie] | None = None):
        self.cookies = cookies or []

    def read(self, domain, names=None, profile=None):
        return [c for c in self.cookies if (not names or c.name in names) and (not profile or profile == c.profile)]

    def profiles(self):
        return {"Profile 5": "Work"}


def cookie(value: str, *, profile="Profile 5", expires=NOW + timedelta(days=30), last_access=NOW) -> Cookie:
    return Cookie("Authorization", value, ".example.com", "/", expires, last_access, profile, "Work")


def session(chrome: FakeChrome, opened: list | None = None, **kwargs) -> BrowserSession:
    if opened is not None:
        kwargs["opener"] = lambda url, profile: opened.append((url, profile))
        # Records "hide" (back to the background) next to the opened pages, instead of running chrome-ai.
        kwargs.setdefault("hider", lambda: opened.append("hidden"))
    return BrowserSession(
        app_name="example",
        domain="example.com",
        cookies=["Authorization"],
        login_url=LOGIN_URL,
        chrome=chrome,
        **kwargs,
    )


class Commands:
    """Fake command runner: records the commands instead of running them."""

    def __init__(self, ok: bool = True):
        self.calls: list[list[str]] = []
        self.ok = ok

    def __call__(self, args, timeout=15):
        self.calls.append(list(args))
        return self.ok


# --- credentials -------------------------------------------------------------------------------------------


async def test_cookie_read_from_chrome_ai_by_default():
    s = session(FakeChrome([cookie("Bearer" + jwt(NOW + timedelta(days=1)))]), opened := [])
    creds = await s.credentials()
    assert creds.source == 'Chrome AI, profile "Work"' and creds.profile == "Profile 5"
    assert opened == []


async def test_cookie_read_from_regular_chrome_when_asked():
    s = session(FakeChrome([cookie("ok")]), [], chrome_source="chrome")
    assert (await s.credentials()).source == 'Chrome, profile "Work"'


async def test_most_recent_profile_wins():
    old = cookie("old", profile="Default", last_access=NOW - timedelta(days=3))
    s = session(FakeChrome([old, cookie("recent")]), [])
    assert (await s.credentials()).values == {"Authorization": "recent"}


async def test_expired_cookie_or_jwt_ignored():
    expired_jwt = cookie("Bearer" + jwt(NOW - timedelta(minutes=1)))
    expired_cookie = cookie("x", expires=NOW - timedelta(minutes=1))
    s = session(FakeChrome([expired_jwt, expired_cookie]), [], login_wait=0)
    with pytest.raises(LoginRequired):
        await s.credentials()


async def test_manual_token_as_fallback(monkeypatch):
    monkeypatch.setenv("EX_TOKEN", "fallback-token")
    s = session(FakeChrome(), opened := [], manual_token_env="EX_TOKEN")
    assert (await s.credentials()).source == "manual token"
    assert opened == []


async def test_unfilled_mcpb_template_ignored(monkeypatch):
    monkeypatch.setenv("EX_TOKEN", "${user_config.token}")
    s = session(FakeChrome(), [], manual_token_env="EX_TOKEN", login_wait=0)
    with pytest.raises(LoginRequired):
        await s.credentials()


async def test_waits_for_login_then_finds_it():
    chrome = FakeChrome()
    s = session(chrome, opened := [], login_wait=5)
    s.last_seen_profile = "Profile 5"

    async def user_signs_in():
        await asyncio.sleep(1)
        chrome.cookies.append(cookie("new-session"))

    creds, _ = await asyncio.gather(s.credentials(), user_signs_in())
    assert creds.values == {"Authorization": "new-session"}
    # The login window was shown, the user signed in, and Chrome AI went back to the background.
    assert opened == [(LOGIN_URL, "Profile 5"), "hidden"]


async def test_no_hide_when_the_session_was_already_there():
    s = session(FakeChrome([cookie("ok")]), opened := [])
    await s.credentials()
    assert opened == []


async def test_timeout_gives_a_clear_message_naming_chrome_ai():
    s = session(FakeChrome(), opened := [], login_wait=0)
    with pytest.raises(LoginRequired, match=f"I opened {LOGIN_URL} in Chrome AI: sign in there"):
        await s.credentials()
    with pytest.raises(LoginRequired):
        await s.credentials()
    assert len(opened) == 1  # no new tab on every call


async def test_timeout_message_names_the_regular_chrome_profile():
    s = session(FakeChrome(), [], login_wait=0, chrome_source="chrome")
    s.last_seen_profile = "Profile 5"
    with pytest.raises(LoginRequired, match=f'I opened {LOGIN_URL} in Chrome, profile "Work": sign in there'):
        await s.credentials()


async def test_rejected_credentials_never_reused():
    chrome = FakeChrome([cookie("expired-server-side")])
    s = session(chrome, [], login_wait=0)
    s.reject(await s.credentials())
    with pytest.raises(LoginRequired):
        await s.credentials()


def test_status_uses_english_keys_and_names_the_browser():
    s = session(FakeChrome([cookie("secret-value")]), [])
    status = s.status()
    assert status["chrome"] == f"Chrome AI ({CHROME_AI_DEFAULT_DIR})"
    assert status["domain"] == "example.com" and status["session"] == 'Chrome AI, profile "Work"'
    assert {"forced_profile", "manual_token_configured", "rejected_credentials", "expires"} <= status.keys()
    assert "secret-value" not in json.dumps(status)
    assert session(FakeChrome(), [], chrome_source="chrome").status()["chrome"] == "Chrome (regular profiles)"


# --- Chrome source and user-data-dir -----------------------------------------------------------------------


def test_user_data_dir_defaults_to_chrome_ai():
    assert resolve_user_data_dir() == Path.home() / ".chrome-ai-profile"
    assert resolve_user_data_dir("ai") == Path.home() / ".chrome-ai-profile"


def test_user_data_dir_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("CHROME_AI_PROFILE", str(tmp_path / "ai"))
    assert resolve_user_data_dir() == tmp_path / "ai"
    monkeypatch.setenv("WEBAPP_CHROME", "chrome")
    assert resolve_user_data_dir() == CHROME_DIR
    assert resolve_user_data_dir("ai") == tmp_path / "ai"  # an explicit source beats the env var


def test_user_data_dir_explicit_regular_chrome(monkeypatch):
    monkeypatch.setenv("WEBAPP_CHROME", "ai")
    assert resolve_user_data_dir("chrome") == Path.home() / "Library/Application Support/Google/Chrome"


def test_user_data_dir_invalid_source(monkeypatch):
    with pytest.raises(ValueError, match='use "ai" for Chrome AI or "chrome"'):
        resolve_user_data_dir("firefox")
    monkeypatch.setenv("WEBAPP_CHROME", "brave")
    with pytest.raises(ValueError, match="brave"):
        resolve_user_data_dir()


def test_chrome_cookies_resolves_its_dir_at_construction(monkeypatch, tmp_path):
    monkeypatch.setenv("CHROME_AI_PROFILE", str(tmp_path))
    assert ChromeCookies().user_data_dir == tmp_path


def test_missing_user_data_dir_yields_no_cookies(tmp_path):
    chrome = ChromeCookies(tmp_path / "never-started")
    assert chrome.profiles() == {} and chrome.read("example.com") == []


def make_cookie_db(path: Path, rows: list[tuple]) -> None:
    """Minimal Chrome cookie database. encrypted_value is TEXT on purpose: some real databases do that."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('version', '24')")
    con.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value TEXT, path TEXT, "
        "expires_utc INTEGER, last_access_utc INTEGER)"
    )
    con.executemany("INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def test_reads_a_chrome_ai_profile_dir(tmp_path):
    # Plain-text values: no decryption, so the Keychain is never touched.
    (tmp_path / "Local State").write_text(json.dumps({"profile": {"info_cache": {"Default": {"name": "AI"}}}}))
    make_cookie_db(
        tmp_path / "Default/Cookies",
        [(".example.com", "sid", "abc", "", "/", 0, 0), (".other.com", "sid", "zzz", "", "/", 0, 0)],
    )
    chrome = ChromeCookies(tmp_path)
    assert chrome.profiles() == {"Default": "AI"}
    [found] = chrome.read("example.com", {"sid"})
    assert (found.value, found.profile, found.profile_name, found.expires) == ("abc", "Default", "AI", None)


# --- login page opener -------------------------------------------------------------------------------------


def test_regular_chrome_opener_uses_the_profile():
    run = Commands()
    open_in_chrome(LOGIN_URL, "Profile 5", run=run)
    open_in_chrome(LOGIN_URL, None, run=run)
    assert run.calls == [
        ["open", "-na", "Google Chrome", "--args", "--profile-directory=Profile 5", LOGIN_URL],
        ["open", "-a", "Google Chrome", LOGIN_URL],
    ]


def test_chrome_ai_opener_shows_a_visible_window_on_the_login_page(tmp_path):
    run = Commands()
    open_in_chrome_ai(LOGIN_URL, user_data_dir=tmp_path, run=run, which=lambda name: "/opt/bin/chrome-ai")
    assert run.calls == [["/opt/bin/chrome-ai", "show", LOGIN_URL]]


def test_chrome_ai_opener_finds_the_launcher_in_local_bin(tmp_path):
    launcher = tmp_path / "chrome-ai"
    launcher.write_text("#!/bin/sh\n")
    run = Commands()
    open_in_chrome_ai(LOGIN_URL, user_data_dir=tmp_path, run=run, which=lambda name: None, local_launcher=launcher)
    assert run.calls[0] == [str(launcher), "show", LOGIN_URL]


def test_chrome_ai_opener_without_launcher_starts_chrome_itself(monkeypatch, tmp_path):
    monkeypatch.setenv("CHROME_AI_PORT", "9444")
    run = Commands()
    open_in_chrome_ai(
        LOGIN_URL, user_data_dir=tmp_path, run=run, which=lambda name: None, local_launcher=tmp_path / "missing"
    )
    assert run.calls == [
        ["pkill", "-f", f"--headless=new.*--user-data-dir={tmp_path}"],
        [
            "open", "-na", "Google Chrome", "--args",
            "--remote-debugging-port=9444", "--remote-debugging-address=127.0.0.1",
            "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={tmp_path}", LOGIN_URL,
        ]
    ]  # fmt: skip


def test_chrome_ai_opener_falls_back_when_the_launcher_fails(tmp_path):
    run = Commands(ok=False)
    open_in_chrome_ai(LOGIN_URL, user_data_dir=tmp_path, run=run, which=lambda name: "/opt/bin/chrome-ai")
    assert run.calls[0] == ["/opt/bin/chrome-ai", "show", LOGIN_URL]
    assert run.calls[1][0] == "pkill"
    assert "--remote-debugging-port=9333" in run.calls[2] and run.calls[2][-1] == LOGIN_URL


def test_hide_chrome_ai_uses_the_launcher(tmp_path):
    run = Commands()
    hide_chrome_ai(run=run, which=lambda name: "/opt/bin/chrome-ai")
    hide_chrome_ai(run=run, which=lambda name: None, local_launcher=tmp_path / "missing")
    assert run.calls == [["/opt/bin/chrome-ai", "hide"]]


@pytest.fixture
def commands(monkeypatch, tmp_path):
    """Replaces the real command runner of the session module; no launcher installed."""
    run = Commands()
    monkeypatch.setattr(session_module, "_run", run)
    monkeypatch.setattr(session_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(session_module, "LOCAL_LAUNCHER", tmp_path / "no-launcher")
    return run


async def test_session_opens_login_page_in_chrome_ai_by_default(commands, monkeypatch, tmp_path):
    monkeypatch.setenv("CHROME_AI_PROFILE", str(tmp_path / "ai"))
    s = session(FakeChrome(), login_wait=0)
    with pytest.raises(LoginRequired, match="in Chrome AI"):
        await s.credentials()
    assert commands.calls[0][0] == "pkill"  # a background instance would swallow the page: quit it first
    command = commands.calls[-1]
    assert f"--user-data-dir={tmp_path / 'ai'}" in command and "--remote-debugging-port=9333" in command
    assert not any(arg.startswith("--profile-directory") for arg in command)


async def test_session_opens_login_page_in_regular_chrome_when_asked(commands, monkeypatch):
    monkeypatch.setenv("WEBAPP_CHROME", "chrome")
    s = session(FakeChrome(), login_wait=0)
    s.last_seen_profile = "Profile 5"
    with pytest.raises(LoginRequired, match='in Chrome, profile "Work"'):
        await s.credentials()
    assert commands.calls == [["open", "-na", "Google Chrome", "--args", "--profile-directory=Profile 5", LOGIN_URL]]


def test_webapp_passes_the_chrome_source_through(tmp_path):
    common = dict(domain="example.com", cookies=["sid"], login_url=LOGIN_URL, base_url="https://api.example.com", tool_prefix="ex")
    assert WebApp("example", **common).session.source == "ai"
    regular = WebApp("example", chrome="chrome", **common).session
    assert regular.source == "chrome" and regular.chrome.user_data_dir == CHROME_DIR
    with pytest.raises(ValueError):
        WebApp("example", chrome="safari", **common)


# --- HTTP client -------------------------------------------------------------------------------------------


def response(status: int, body: dict) -> SimpleNamespace:
    return SimpleNamespace(status_code=status, json=lambda: body)


async def test_client_renews_the_session_after_rejection():
    chrome = FakeChrome([cookie("old")])
    s = session(chrome, [], login_wait=5)
    client = WebClient(s, base_url="https://api.example.com", is_auth_error=lambda r: r.status_code == 401)
    sent = []

    async def fake_send(method, url, creds, *args):
        sent.append(creds.values["Authorization"])
        if creds.values["Authorization"] == "old":
            chrome.cookies = [cookie("new")]  # the user signs in again in Chrome AI
            return response(401, {})
        return response(200, {"ok": True})

    client._send = fake_send
    result = await client.request("GET", "/me")
    assert result.status_code == 200 and sent == ["old", "new"]


async def test_client_gives_up_if_the_new_session_is_rejected():
    chrome = FakeChrome([cookie("a")])
    s = session(chrome, [], login_wait=3)
    client = WebClient(s, base_url="https://api.example.com", is_auth_error=lambda r: True)

    async def fake_send(method, url, creds, *args):
        chrome.cookies = [cookie("b")]
        return response(401, {})

    client._send = fake_send
    with pytest.raises(ApiError, match="also rejects the new session .* in Chrome AI"):
        await client.request("GET", "/me")


async def test_foreign_host_rejected():
    client = WebClient(session(FakeChrome(), []), base_url="https://api.example.com")
    with pytest.raises(Exception, match="Host not allowed"):
        await client.request("GET", "https://evil.example.org/steal")


def test_bearer_auth_extracts_the_jwt_from_the_cookie():
    token = jwt(NOW + timedelta(days=1))
    assert bearer_auth("Authorization")({"Authorization": "Bearer" + token}) == {"headers": {"authorization": f"Bearer {token}"}}
    assert bearer_auth("Authorization")({"Authorization": "opaque"}) == {"headers": {"authorization": "Bearer opaque"}}


async def test_request_json_readable_errors():
    client = WebClient(session(FakeChrome([cookie("ok")]), []), base_url="https://api.example.com")

    def fake(status, body, text):
        async def send(*args):
            return SimpleNamespace(status_code=status, json=body, text=text)

        return send

    client._send = fake(200, lambda: {"a": 1}, '{"a":1}')
    assert await client.request_json("GET", "/x") == {"a": 1}
    client._send = fake(500, lambda: {}, "server down")
    with pytest.raises(ApiError, match="HTTP 500: server down"):
        await client.request_json("GET", "/x")

    def not_json():
        raise ValueError

    client._send = fake(200, not_json, "<html>")
    with pytest.raises(ApiError, match="non-JSON"):
        await client.request_json("GET", "/x")
