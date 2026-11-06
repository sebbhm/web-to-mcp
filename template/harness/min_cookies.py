# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.3,<3", "cryptography>=45", "curl_cffi>=0.16"]
# ///
"""Find the smallest set of session cookies a page needs, by greedy elimination. Prints cookie NAMES only.

    uv run harness/min_cookies.py google.com "https://search.google.com/search-console/performance/search-analytics/ai" \\
        --param resource_id=sc-domain:example.com --param num_of_days=7 --marker "AF_initDataCallback({key: 'ds:9'"

Steps: read the cookies Chrome would send to the URL's host (from one profile), check that all of them together
get HTTP 200 with the marker text in the body, then drop the cookies one at a time (sorted by name) and keep only
those whose removal breaks the response. The marker is required, not just the status: some sites (Google) answer a
rejected session with HTTP 200 and their sign-in page. Pick a marker that only a signed-in page contains.

The result is the --cookies value to pass to tools/new.py.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import CHROME_CHOICES, CHROME_HELP, cookie_header, load_session, page_title, parse_pairs, with_params  # noqa: E402


async def minimize(names: list[str], works: Callable[[list[str]], Awaitable[bool]], log=print) -> list[str]:
    """Greedy elimination: try removing each name (sorted); keep the removal when `works` still holds.
    Returns the names whose removal broke the response."""
    kept = sorted(set(names))
    for name in sorted(set(names)):
        trial = [n for n in kept if n != name]
        if await works(trial):
            kept = trial
            log(f"  - {name:<36} not needed")
        else:
            log(f"  + {name:<36} REQUIRED")
    return kept


async def run(args: argparse.Namespace) -> None:
    from curl_cffi import AsyncSession

    url = with_params(args.url, parse_pairs(args.param, "--param"))
    host = urlsplit(url).hostname or ""
    cookies, profile, chrome = load_session(args.domain, url, args.chrome, args.profile)
    names = sorted({c.name for c in cookies})
    if not names:
        sys.exit(
            f"No cookie of {args.domain} applies to {host} in {chrome.user_data_dir}. "
            "Sign in to the site in that Chrome (or pick another with --chrome), then retry."
        )
    print(f"Chrome: {chrome.user_data_dir}, profile \"{chrome.profiles().get(profile, profile)}\"")
    print(f"{len(names)} cookies apply to {host} ({len(cookies)} with duplicates): {', '.join(names)}")

    async with AsyncSession(impersonate="chrome") as http:

        async def fetch(subset: list[str]):
            headers = {"cookie": cookie_header(cookies, subset)} if subset else {}
            return await http.get(url, headers=headers, timeout=30, allow_redirects=True)

        async def works(subset: list[str]) -> bool:
            response = await fetch(subset)
            return response.status_code == 200 and args.marker in response.text

        baseline = await fetch(names)
        if not (baseline.status_code == 200 and args.marker in baseline.text):
            title = page_title(baseline.text)
            sys.exit(
                f"\nWith all cookies: HTTP {baseline.status_code}, final URL {str(baseline.url).split('?')[0]}"
                f"{f', title {title!r}' if title else ''}, marker {'found' if args.marker in baseline.text else 'NOT found'}.\n"
                "Either the session is missing or expired (sign in again in that Chrome), or the marker is not in "
                "this page (check it with embedded_data.py --grep)."
            )
        print(f"All cookies: HTTP 200, marker found. Without any cookie: ", end="")
        print("marker found (the page does not need a session)" if await works([]) else "marker missing (good)")
        print("\nRemoving one cookie at a time:")
        kept = await minimize(names, works)
        confirmed = await works(kept)

    print(f"\nMinimal set ({len(kept)} of {len(names)}): {', '.join(kept) or '(none)'}"
          f"  [re-check: {'OK' if confirmed else 'FAILED, the session may have rotated: run again'}]")
    if kept:
        print(f"For tools/new.py:  --cookies {','.join(kept)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("domain", help="cookie domain of the site (e.g. google.com)")
    parser.add_argument("url", help="a page (or endpoint) that needs the session")
    parser.add_argument("--marker", required=True, help="text that only a signed-in response contains")
    parser.add_argument("--param", action="append", help="query parameter name=value (repeatable)")
    parser.add_argument("--chrome", choices=CHROME_CHOICES, help=CHROME_HELP)
    parser.add_argument("--profile", help="Chrome profile (folder or name); default: the most recently used")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
