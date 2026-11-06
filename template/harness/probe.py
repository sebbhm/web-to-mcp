# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.3,<3", "cryptography>=45", "curl_cffi>=0.16"]
# ///
"""Query a site's internal API with the session read from Chrome, to validate an endpoint before writing the
MCP tool. Never prints a cookie value.

    # Which cookies does the site set? (names, profiles, expiry; never the values)
    uv run harness/probe.py cookies example.com

    # Replay an endpoint and compare auth modes (all cookies, chosen cookies, Bearer, no auth, no Chrome
    # impersonation): shows what works and what a rejected session looks like.
    uv run harness/probe.py call example.com POST https://api.example.com/v1/portfolio/query \\
        --json '{"currency": 2781}' --header platform=web --compare

    # A single request, response shown (shape + start of the content)
    uv run harness/probe.py call example.com POST <url> --bearer Authorization --header platform=web --json '{...}'

Cookies come from Chrome AI by default (the dedicated instance); --chrome chrome reads the regular Chrome.
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import CHROME_CHOICES, CHROME_HELP, best_profile, chrome_cookies, engine_path, parse_pairs  # noqa: E402

engine_path()
from curl_cffi import AsyncSession  # noqa: E402
from webapp_mcp import bearer_auth, find_jwt, jwt_expiry  # noqa: E402

LIKELY_SESSION = re.compile(r"sess|auth|token|jwt|sid|login|user|access|refresh|account", re.I)


def describe(value: object, depth: int = 0) -> object:
    """Shape of a JSON response: keys and types, list lengths, no values."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        items = list(value.items())
        shape = {k: describe(v, depth + 1) for k, v in items[:30]}
        if len(items) > 30:
            shape["…"] = f"{len(items) - 30} more keys"
        return shape
    if isinstance(value, list):
        return [f"{len(value)} items", describe(value[0], depth + 1)] if value else []
    return type(value).__name__ if value is not None else "null"


def error_hints(value: object, depth: int = 0) -> list[str]:
    """Fields that look like an error code or message: the signature of a rejected session."""
    hints = []
    if isinstance(value, dict) and depth < 3:
        for key, v in value.items():
            if re.search(r"code|error|message|msg|status", key, re.I) and isinstance(v, (str, int, float, bool)):
                hints.append(f"{key}={str(v)[:60]}")
            else:
                hints += [f"{key}.{h}" for h in error_hints(v, depth + 1)]
    return hints


def list_cookies(args: argparse.Namespace) -> None:
    chrome = chrome_cookies(args.chrome)
    cookies = chrome.read(args.domain)
    if not cookies:
        sys.exit(f"No cookie for {args.domain} in {chrome.user_data_dir}. Sign in to the site in that Chrome "
                 "(or pick the other one with --chrome), then retry.")
    names = chrome.profiles()
    print(f"Chrome: {chrome.user_data_dir}")
    for profile in sorted({c.profile for c in cookies}):
        print(f"\nProfile \"{names.get(profile, profile)}\" ({profile})")
        for c in sorted((c for c in cookies if c.profile == profile), key=lambda c: c.name.lower()):
            exp = jwt_expiry(c.value)
            flags = [
                "EXPIRED" if c.expired else "",
                f"JWT exp {exp:%Y-%m-%d}" if exp else "",
                "<- likely session" if LIKELY_SESSION.search(c.name) or find_jwt(c.value) else "",
            ]
            expires = f"{c.expires:%Y-%m-%d}" if c.expires else "session"
            print(f"  {c.name:<32} {c.host:<28} expires {expires:<10} {len(c.value):>5} chars  {' '.join(f for f in flags if f)}")


async def send(method, url, *, headers, cookies, params, body, impersonate=True):
    async with AsyncSession(impersonate="chrome" if impersonate else None) as http:
        return await http.request(method, url, headers=headers, cookies=cookies or None, params=params or None, json=body, timeout=30)


def summary_line(label: str, response) -> str:
    content_type = response.headers.get("content-type", "?").split(";")[0]
    hint = ""
    if "json" in content_type:
        try:
            data = response.json()
            hint = ", ".join(error_hints(data)[:4]) or f"keys: {', '.join(list(data)[:6]) if isinstance(data, dict) else type(data).__name__}"
        except ValueError:
            hint = "unreadable JSON"
    elif "html" in content_type:
        title = re.search(r"<title>(.*?)</title>", response.text, re.I | re.S)
        hint = f"HTML page{f': {title.group(1).strip()[:60]}' if title else ''}"
    return f"  {label:<34} HTTP {response.status_code:<4} {content_type:<24} {len(response.content):>8} B  {hint}"


async def call(args: argparse.Namespace) -> None:
    chrome = chrome_cookies(args.chrome)
    all_cookies = [c for c in chrome.read(args.domain) if not c.expired]
    profile = best_profile(all_cookies, args.profile, chrome.profiles())
    jar = {c.name: c.value for c in all_cookies if c.profile == profile}
    if not jar:
        print(f"Warning: no valid cookie for {args.domain} in {chrome.user_data_dir} (requests go out without a session).")
    else:
        print(f"Session: {chrome.user_data_dir}, profile \"{chrome.profiles().get(profile, profile)}\", {len(jar)} cookies for {args.domain}")
    chosen = {n: jar[n] for n in args.cookies.split(",") if n in jar} if args.cookies else None
    if args.cookies and len(chosen) != len(args.cookies.split(",")):
        sys.exit("Requested cookie not found: list them with the `cookies` subcommand.")
    request = {
        "headers": parse_pairs(args.header, "--header"),
        "params": parse_pairs(args.param, "--param"),
        "body": json.loads(args.json) if args.json else None,
    }

    if args.compare:
        modes = [("all cookies of the domain", {"cookies": jar}, True)]
        if chosen:
            modes.append((f"cookies {args.cookies}", {"cookies": chosen}, True))
        for name, value in (chosen or jar).items():
            if find_jwt(value) or name == args.bearer:
                modes.append((f"Bearer (cookie {name})", bearer_auth(name)({name: value}), True))
        modes += [("no auth", {}, True), ("all cookies, no impersonation", {"cookies": jar}, False)]
        print(f"\n{args.method} {args.url}")
        for label, auth, impersonate in modes:
            headers = {**request["headers"], **auth.get("headers", {})}
            response = await send(args.method, args.url, headers=headers, cookies=auth.get("cookies"), params=request["params"], body=request["body"], impersonate=impersonate)
            print(summary_line(label, response))
        print("\nThe simplest mode that works is the one to use in WebApp(auth=…); the \"no auth\" response shows what a"
              " rejected session looks like, for is_auth_error.")
        return

    if args.bearer and args.bearer not in jar:
        sys.exit(f"Cookie {args.bearer} not found: list them with the `cookies` subcommand.")
    auth = bearer_auth(args.bearer)({args.bearer: jar[args.bearer]}) if args.bearer else {"cookies": chosen or jar}
    headers = {**request["headers"], **auth.get("headers", {})}
    response = await send(args.method, args.url, headers=headers, cookies=auth.get("cookies"), params=request["params"], body=request["body"])
    print(summary_line("response", response))
    try:
        data = response.json()
    except ValueError:
        print(response.text[: args.max])
        return
    print("\nShape:\n" + json.dumps(describe(data), ensure_ascii=False, indent=1)[: args.max])
    print("\nContent (start):\n" + json.dumps(data, ensure_ascii=False, indent=1)[: args.max])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    cookies = sub.add_parser("cookies", help="list a domain's cookies in Chrome (without their values)")
    cookies.add_argument("domain")
    cookies.add_argument("--chrome", choices=CHROME_CHOICES, help=CHROME_HELP)
    c = sub.add_parser("call", help="replay a request with the session read from Chrome")
    c.add_argument("domain", help="cookie domain of the session (e.g. example.com)")
    c.add_argument("method", type=str.upper)
    c.add_argument("url")
    c.add_argument("--json", help="JSON body")
    c.add_argument("--param", action="append", help="query parameter name=value (repeatable)")
    c.add_argument("--header", action="append", help="header name=value (repeatable)")
    c.add_argument("--cookies", help="send only these cookies (comma-separated names)")
    c.add_argument("--bearer", help="send this cookie as an Authorization: Bearer header")
    c.add_argument("--chrome", choices=CHROME_CHOICES, help=CHROME_HELP)
    c.add_argument("--profile", help="Chrome profile (folder or name); default: the most recently used")
    c.add_argument("--compare", action="store_true", help="compare auth modes")
    c.add_argument("--max", type=int, default=3000, help="characters shown per section (default 3000)")
    args = parser.parse_args()
    list_cookies(args) if args.command == "cookies" else asyncio.run(call(args))


if __name__ == "__main__":
    main()
