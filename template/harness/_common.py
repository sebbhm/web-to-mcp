"""Helpers shared by the harness scripts. Pure functions only at import time: the engine (and curl_cffi) are
imported lazily, so the unit tests run with nothing but pytest.

Never print a cookie value: helpers here hand values to HTTP requests only.
"""

from __future__ import annotations

import html
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.dont_write_bytecode = True  # the template may live in a read-only plugin cache

CORE = Path(__file__).resolve().parent.parent / "core"
OUTPUT_DIR = Path(".web-to-mcp")  # relative to the current working directory
CHROME_CHOICES = ("ai", "chrome")
CHROME_HELP = (
    'cookie source: "ai" = Chrome AI (the dedicated instance, $CHROME_AI_PROFILE or ~/.chrome-ai-profile), '
    '"chrome" = the regular Chrome. Default: $WEBAPP_CHROME, else "ai"'
)
_NEVER = datetime.min.replace(tzinfo=timezone.utc)


def engine_path() -> None:
    """Make the template engine (core/webapp_mcp) importable."""
    if str(CORE) not in sys.path:
        sys.path.insert(0, str(CORE))


def chrome_cookies(source: str | None = None):
    """ChromeCookies reading the chosen Chrome instance (see CHROME_HELP)."""
    engine_path()
    from webapp_mcp.chrome import ChromeCookies, resolve_user_data_dir

    return ChromeCookies(user_data_dir=resolve_user_data_dir(source))


def parse_pairs(pairs: Iterable[str] | None, what: str) -> dict[str, str]:
    """["k=v", …] → {k: v}; exits with a clear message on a malformed pair."""
    result = {}
    for pair in pairs or []:
        if "=" not in pair:
            sys.exit(f"{what} expected as name=value: {pair}")
        key, value = pair.split("=", 1)
        result[key] = value
    return result


def with_params(url: str, params: dict[str, str]) -> str:
    """URL with extra query parameters appended (existing ones are kept)."""
    if not params:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True) + list(params.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


def best_profile(cookies: list, profile: str | None, names: dict[str, str]) -> str | None:
    """Requested profile (folder or display name), else the one whose cookie was used most recently."""
    if profile:
        return next((folder for folder, name in names.items() if profile in (folder, name)), profile)
    live = [c for c in cookies if not c.expired]
    return max(live, key=lambda c: c.last_access or _NEVER).profile if live else None


def cookie_applies(cookie_host: str, cookie_path: str, url_host: str, url_path: str) -> bool:
    """Would Chrome send a cookie stored for (cookie_host, cookie_path) to this URL?

    Host: the cookie host equals the URL host (host-only cookie), or it is a domain cookie (leading dot) for the
    URL host itself or one of its parent domains. Path: the cookie path is a prefix of the URL path."""
    cookie_host, url_host = cookie_host.lower(), url_host.lower()
    if cookie_host.startswith("."):
        host_ok = url_host == cookie_host[1:] or url_host.endswith(cookie_host)
    else:
        host_ok = url_host == cookie_host
    path = cookie_path or "/"
    url_path = url_path or "/"
    path_ok = url_path == path or url_path.startswith(path if path.endswith("/") else path + "/")
    return host_ok and path_ok


def applicable_cookies(cookies: list, url: str) -> list:
    """Cookies Chrome would send to this URL, most specific host first (as Chrome orders them)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    kept = [c for c in cookies if not c.expired and cookie_applies(c.host, c.path, host, parts.path)]
    return sorted(kept, key=lambda c: (-len(c.path or "/"), -len(c.host.lstrip(".")), c.name))


def cookie_header(cookies: list, names: Iterable[str] | None = None) -> str:
    """Cookie header value for these cookies (optionally only the given names). Duplicated names (same cookie
    on several hosts) are all sent, as Chrome does."""
    wanted = set(names) if names is not None else None
    return "; ".join(f"{c.name}={c.value}" for c in cookies if wanted is None or c.name in wanted)


def load_session(domain: str, url: str, source: str | None, profile: str | None) -> tuple[list, str | None, Any]:
    """(cookies Chrome would send to url from the chosen profile, profile folder, ChromeCookies)."""
    chrome = chrome_cookies(source)
    live = [c for c in chrome.read(domain) if not c.expired]
    folder = best_profile(live, profile, chrome.profiles())
    return applicable_cookies([c for c in live if c.profile == folder], url), folder, chrome


def output_path(out: str | Path | None, kind: str, host: str) -> Path:
    """--out, or ./.web-to-mcp/<kind>-<host>.json. The default folder gets a .gitignore: it may hold user data."""
    if out:
        path = Path(out)
    else:
        path = OUTPUT_DIR / f"{kind}-{host}.json"
        OUTPUT_DIR.mkdir(exist_ok=True)
        ignore = OUTPUT_DIR / ".gitignore"
        if not ignore.exists():
            ignore.write_text("# Discovery output of web-to-mcp: may contain account data, never commit it.\n*\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def page_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return " ".join(html.unescape(match.group(1)).split())[:80] if match else ""
