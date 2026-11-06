# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.3,<3", "cryptography>=45", "curl_cffi>=0.16"]
# ///
"""Look for API endpoints cited in a page's JavaScript code. Complements the network capture (capture.py), which
only shows the calls actually made.

    uv run harness/bundle_scan.py https://example.com/portfolio/ --grep portfolio
    uv run harness/bundle_scan.py <url> --with-session      # page loaded with the Chrome session

Limit: only the page's scripts and the ones they cite directly are read; code loaded later (another page, an
action) may be missed.
"""

import argparse
import asyncio
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import CHROME_CHOICES, CHROME_HELP, chrome_cookies  # noqa: E402
from curl_cffi import AsyncSession  # noqa: E402

SCRIPT_TAG = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.I)
PRELOAD = re.compile(r"""<link[^>]+href=["']([^"']+\.m?js(?:\?[^"']*)?)["']""", re.I)
JS_REFERENCE = re.compile(r"""["'`]((?:https?://[^"'`\s]+|/[^"'`\s]+|static/chunks/[^"'`\s]+)\.m?js)["'`]""")
# A string that looks like an API path: at least two segments, possibly a full URL.
ENDPOINT = re.compile(r"""["'`]((?:https?://[a-z0-9.-]+)?/[\w\-.{}$:]+(?:/[\w\-.{}$:]+)+/?(?:\?[^"'`\s]*)?)["'`]""", re.I)
ASSET = re.compile(r"\.(?:m?js|css|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|map|mp4|webm)(?:\?|$)", re.I)
API_HOST = re.compile(r"https?://([a-z0-9.-]*api[a-z0-9.-]*\.[a-z]{2,})", re.I)
MAX_BYTES = 15_000_000


def script_urls(html: str, page: str) -> list[str]:
    return list(dict.fromkeys(urljoin(page, src) for src in SCRIPT_TAG.findall(html) + PRELOAD.findall(html)))


def referenced_scripts(code: str, script: str) -> list[str]:
    """Scripts cited by a script (Next.js chunks, dynamic imports)."""
    urls = []
    for ref in JS_REFERENCE.findall(code):
        if ref.startswith("static/chunks/"):
            urls.append(urljoin(script, "/_next/" + ref))
        else:
            urls.append(urljoin(script, ref))
    return urls


async def fetch(http: AsyncSession, url: str, limit: asyncio.Semaphore) -> str:
    async with limit:
        try:
            response = await http.get(url, timeout=30)
        except Exception as error:  # an unreachable script does not stop the scan
            print(f"  (skipped) {url}: {error}", file=sys.stderr)
            return ""
    if response.status_code != 200 or len(response.content) > MAX_BYTES:
        return ""
    return response.text


async def scan(args: argparse.Namespace) -> None:
    cookies = None
    if args.with_session:
        domain = urlsplit(args.url).hostname.removeprefix("www.")
        cookies = {c.name: c.value for c in chrome_cookies(args.chrome).read(domain) if not c.expired}
    limit = asyncio.Semaphore(8)
    async with AsyncSession(impersonate="chrome", cookies=cookies) as http:
        page = await http.get(args.url, timeout=30)
        if page.status_code != 200:
            sys.exit(f"Page unreachable: HTTP {page.status_code}")
        scripts = script_urls(page.text, args.url)[: args.max_files]
        codes = dict(zip(scripts, await asyncio.gather(*(fetch(http, s, limit) for s in scripts))))
        # One more level: the scripts cited by the page's scripts.
        extra = [u for s, code in codes.items() for u in referenced_scripts(code, s) if u not in codes]
        extra = list(dict.fromkeys(extra))[: max(0, args.max_files - len(codes))]
        codes |= dict(zip(extra, await asyncio.gather(*(fetch(http, s, limit) for s in extra))))

    found: dict[str, set[str]] = defaultdict(set)
    hosts: dict[str, int] = defaultdict(int)
    for script, code in codes.items():
        for host in API_HOST.findall(code):
            hosts[host.lower()] += 1
        for endpoint in ENDPOINT.findall(code):
            if not ASSET.search(endpoint) and not endpoint.startswith(("/_next/", "/static/")):
                found[endpoint].add(script)
    grep = re.compile(args.grep, re.I) if args.grep else None
    endpoints = sorted(e for e in found if not grep or grep.search(e))

    read = sum(1 for code in codes.values() if code)
    print(f"{read} of {len(codes)} scripts read ({sum(len(c) for c in codes.values()) // 1024} KB)")
    if hosts:
        print("\nAPI hosts cited: " + ", ".join(f"{h} ({n})" for h, n in sorted(hosts.items(), key=lambda x: -x[1])[:15]))
    print(f"\n{len(endpoints)} paths{f' matching {args.grep!r}' if grep else ''}:")
    for endpoint in endpoints[: args.limit]:
        print(f"  {endpoint}")
    if len(endpoints) > args.limit:
        print(f"  … {len(endpoints) - args.limit} more (narrow with --grep)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="page of the app (the one that shows the wanted data)")
    parser.add_argument("--grep", help="keep only the paths matching this pattern (regex)")
    parser.add_argument("--with-session", action="store_true", help="load the page with the session read from Chrome")
    parser.add_argument("--chrome", choices=CHROME_CHOICES, help=CHROME_HELP + " (with --with-session)")
    parser.add_argument("--max-files", type=int, default=120, help="maximum number of scripts read (default 120)")
    parser.add_argument("--limit", type=int, default=200, help="maximum number of paths shown (default 200)")
    asyncio.run(scan(parser.parse_args()))


if __name__ == "__main__":
    main()
