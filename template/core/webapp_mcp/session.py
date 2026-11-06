"""Web session: credentials read from Chrome AI (or Chrome), login page opened when needed, manual token as fallback."""

from __future__ import annotations

import asyncio
import functools
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from mcp.server.mcpserver.exceptions import ToolError

from .chrome import ChromeCookies, Cookie, KeychainError, chrome_ai_port, env, resolve_chrome_source, resolve_user_data_dir
from .jwt import jwt_expiry

log = logging.getLogger("webapp_mcp")

_NEVER = datetime.min.replace(tzinfo=timezone.utc)
REOPEN_AFTER = 120  # seconds before the login page is opened again
LOCAL_LAUNCHER = Path.home() / ".local/bin/chrome-ai"

# opener(login URL, Chrome profile folder or None): shows the login page to the user.
Opener = Callable[[str, str | None], None]


class LoginRequired(ToolError):
    """No valid session: the user must sign in to the site in Chrome AI (or Chrome)."""


@dataclass
class Credentials:
    values: dict[str, str]  # cookie name → value
    source: str  # 'Chrome AI, profile "Default"', 'Chrome, profile "Work"' or "manual token"
    profile: str | None = None  # Chrome profile folder they came from
    expires: datetime | None = None  # earliest expiry among cookies and JWTs

    @property
    def expired(self) -> bool:
        return self.expires is not None and self.expires <= datetime.now(timezone.utc)

    @property
    def key(self) -> tuple:
        return tuple(sorted(self.values.items()))


def _run(args: list[str], timeout: float = 15) -> bool:
    """Runs a helper command; True if it succeeded. Output is captured: an MCP server speaks JSON-RPC over
    stdout, so a child writing there (chrome-ai prints its URL) would corrupt the stream."""
    try:
        return subprocess.run(args, check=False, timeout=timeout, capture_output=True).returncode == 0
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("%s failed: %s", args[0], error)
        return False


def open_in_chrome(url: str, profile: str | None = None, *, run: Callable[..., bool] | None = None) -> None:
    """Opens the page in the user's regular Chrome, in the given profile folder when known."""
    run = _run if run is None else run
    if profile:
        run(["open", "-na", "Google Chrome", "--args", f"--profile-directory={profile}", url])
    else:
        run(["open", "-a", "Google Chrome", url])


def find_chrome_ai_launcher(which: Callable[[str], str | None] | None = None, local: Path | None = None) -> str | None:
    """Path of the `chrome-ai` launcher installed by the plugin: on PATH, or in ~/.local/bin (often missing
    from the PATH of apps started from the Dock, such as Claude Desktop)."""
    found = (shutil.which if which is None else which)("chrome-ai")
    if found:
        return found
    local = LOCAL_LAUNCHER if local is None else local
    return str(local) if local.is_file() else None


def open_in_chrome_ai(
    url: str,
    profile: str | None = None,
    *,
    user_data_dir: Path | None = None,
    port: int | None = None,
    run: Callable[..., bool] | None = None,
    which: Callable[[str], str | None] | None = None,
    local_launcher: Path | None = None,
) -> None:
    """Opens the login page in Chrome AI as a VISIBLE window, so the user can type their sign-in.

    Chrome AI normally runs in the background (headless: no window, no Dock icon). A login needs a human, so
    `chrome-ai show <url>` restarts it as a window when it was in the background (same profile, same cookies) and
    opens the page. `profile` is unused: Chrome AI has a single profile."""
    run = _run if run is None else run
    user_data_dir = Path(user_data_dir) if user_data_dir else resolve_user_data_dir("ai")
    launcher = find_chrome_ai_launcher(which, local_launcher)
    if launcher and run([launcher, "show", url], timeout=60):
        return
    # No launcher (or it failed): quit a background (headless) instance first, since Chrome would forward the page
    # to it and nobody would see it; then start Chrome AI as a window with the launcher's flags and the URL in one
    # command. If a visible Chrome AI already runs, Chrome forwards the command to it (the start-up flags are then
    # ignored). `open` returns at once and Chrome runs under launchd, detached from this server.
    run(["pkill", "-f", f"--headless=new.*--user-data-dir={user_data_dir}"])
    target = [f"--user-data-dir={user_data_dir}", url]
    flags = [
        f"--remote-debugging-port={port or chrome_ai_port()}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    run(["open", "-na", "Google Chrome", "--args", *flags, *target])


def hide_chrome_ai(
    *, run: Callable[..., bool] | None = None, which: Callable[[str], str | None] | None = None, local_launcher: Path | None = None
) -> None:
    """Sends Chrome AI back to the background after a sign-in (`chrome-ai hide`). Without the launcher, leave the
    window: the user can close it, the sign-in is saved either way."""
    run = _run if run is None else run
    launcher = find_chrome_ai_launcher(which, local_launcher)
    if launcher:
        run([launcher, "hide"], timeout=60)


def _earliest(dates: list[datetime | None]) -> datetime | None:
    known = [d for d in dates if d]
    return min(known) if known else None


def _display_path(path: Path) -> str:
    """Path with the home folder shortened to ~, for messages."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


class BrowserSession:
    """Provides the credentials of a web app, in this order:

    1. the session cookies read from Chrome AI, or from the regular Chrome when WEBAPP_CHROME=chrome
       (most recently used profile, or CHROME_PROFILE);
    2. the manual token (environment variable), as a fallback;
    3. otherwise, the login page opens in that browser and the engine waits for the cookies to appear.

    Credentials rejected by the site are never reused for the lifetime of the server.
    """

    def __init__(
        self,
        *,
        app_name: str,
        domain: str,
        cookies: list[str],
        login_url: str,
        manual_token_env: str | None = None,
        profile: str | None = None,
        login_wait: float = 45,
        chrome: ChromeCookies | None = None,
        opener: Opener | None = None,
        chrome_source: str | None = None,
        hider: Callable[[], None] | None = None,
    ):
        self.app_name = app_name
        self.domain = domain
        self.cookie_names = list(cookies)
        self.login_url = login_url
        self.manual_token_env = manual_token_env
        self.profile = profile or env("CHROME_PROFILE")
        self.login_wait = login_wait  # Chrome writes its cookies to disk every ~30 s
        self.source = resolve_chrome_source(chrome_source)  # "ai" or "chrome"; raises ValueError otherwise
        self.user_data_dir = getattr(chrome, "user_data_dir", None) or resolve_user_data_dir(self.source)
        self.chrome = chrome or ChromeCookies(self.user_data_dir)
        self.opener = opener or self._default_opener()
        # Chrome AI runs in the background; the login flow shows it as a window, and once a session is found the
        # window goes back to the background, so nothing stays on the user's screen.
        self.hider = hider or (hide_chrome_ai if self.source == "ai" else None)
        self._window_shown = False
        self.current: Credentials | None = None
        self.last_seen_profile: str | None = None  # last profile where a cookie, even expired, was seen
        self._rejected: set[tuple] = set()
        self._last_opened = -REOPEN_AFTER
        self._lock = asyncio.Lock()

    @property
    def browser(self) -> str:
        """Name of the browser the user signs in to, for messages."""
        return "Chrome AI" if self.source == "ai" else "Chrome"

    def _default_opener(self) -> Opener:
        if self.source == "ai":
            return functools.partial(open_in_chrome_ai, user_data_dir=self.user_data_dir)
        return open_in_chrome

    def from_chrome(self) -> Credentials | None:
        """Complete set of cookies, not expired and never rejected; the most recently used one wins."""
        by_profile: dict[str, dict[str, Cookie]] = {}
        for cookie in self.chrome.read(self.domain, set(self.cookie_names), self.profile):
            self.last_seen_profile = cookie.profile
            if cookie.expired:
                continue
            found = by_profile.setdefault(cookie.profile, {})
            # Same name on several hosts or paths: keep the most recent one.
            previous = found.get(cookie.name)
            if not previous or (cookie.last_access or _NEVER) > (previous.last_access or _NEVER):
                found[cookie.name] = cookie
        candidates = []
        for profile, found in by_profile.items():
            if len(found) < len(self.cookie_names):
                continue
            cookies = [found[name] for name in self.cookie_names]
            creds = Credentials(
                values={c.name: c.value for c in cookies},
                source=f'{self.browser}, profile "{cookies[0].profile_name}"',
                profile=profile,
                expires=_earliest([c.expires for c in cookies] + [jwt_expiry(c.value) for c in cookies]),
            )
            if not creds.expired and creds.key not in self._rejected:
                candidates.append((max(c.last_access or _NEVER for c in cookies), creds))
        return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None

    def from_env(self) -> Credentials | None:
        token = env(self.manual_token_env)
        if not token:
            return None
        creds = Credentials({self.cookie_names[0]: token}, "manual token", expires=jwt_expiry(token))
        return None if creds.expired or creds.key in self._rejected else creds

    def read_chrome(self) -> Credentials | None:
        try:
            return self.from_chrome()
        except KeychainError as error:
            raise ToolError(str(error)) from error

    async def credentials(self) -> Credentials:
        async with self._lock:
            if not self.current or self.current.expired:
                self.current = await asyncio.to_thread(self.read_chrome) or self.from_env() or await self._wait_for_login()
                log.info("Session %s: %s", self.app_name, self.current.source)
                if self._window_shown and self.hider:
                    # The user signed in through the window we showed: send Chrome AI back to the background.
                    self._window_shown = False
                    await asyncio.to_thread(self.hider)
            return self.current

    def reject(self, creds: Credentials) -> None:
        """The site rejected these credentials: the next call will look for a fresh session."""
        log.warning("Session %s rejected by the site (%s)", self.app_name, creds.source)
        self._rejected.add(creds.key)
        if self.current is creds:
            self.current = None

    async def _wait_for_login(self) -> Credentials:
        profile = self.profile or self.last_seen_profile
        if time.monotonic() - self._last_opened > REOPEN_AFTER:
            log.info("Opening login page %s in %s (profile %s)", self.login_url, self.browser, profile or "default")
            await asyncio.to_thread(self.opener, self.login_url, profile)
            self._last_opened = time.monotonic()
            self._window_shown = self.source == "ai"
        deadline = time.monotonic() + self.login_wait
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            if creds := await asyncio.to_thread(self.read_chrome):
                return creds
        raise LoginRequired(
            f"No valid {self.app_name} session. I opened {self.login_url} {self._where(profile)}: "
            "sign in there, then retry the request."
        )

    def _where(self, profile: str | None) -> str:
        """Where the login page was opened, as the user sees it."""
        if self.source == "ai":
            return "in Chrome AI"
        if profile:
            return f'in Chrome, profile "{self.chrome.profiles().get(profile, profile)}"'
        return "in Chrome"

    def status(self) -> dict:
        """Connection status, never exposing a cookie value nor opening a page."""
        chrome = self.read_chrome()
        used = self.current or chrome or self.from_env()
        return {
            "app": self.app_name,
            "domain": self.domain,
            "cookies": self.cookie_names,
            "chrome": f"Chrome AI ({_display_path(self.user_data_dir)})" if self.source == "ai" else "Chrome (regular profiles)",
            "session": used.source if used else "none: the login page will open on the next call",
            "expires": used.expires.isoformat(timespec="minutes") if used and used.expires else None,
            "forced_profile": self.profile,
            "manual_token_configured": bool(env(self.manual_token_env)),
            "rejected_credentials": len(self._rejected),
        }
