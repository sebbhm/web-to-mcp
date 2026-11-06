# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.3,<3", "cryptography>=45", "curl_cffi>=0.16"]
# ///
"""List the data a page embeds in its own HTML, fetched with the Chrome session (impersonating Chrome).

Many apps render their first screen from JSON already present in the page, so no internal API call is needed:
on Google Search Console every report value sits in AF_initDataCallback blocks. Look here before capturing
network traffic.

    uv run harness/embedded_data.py google.com "https://search.google.com/search-console/performance/search-analytics/ai" \\
        --param resource_id=sc-domain:example.com --param num_of_days=7 --grep my-page-slug

Recognised blocks:
  - Google  AF_initDataCallback({key: 'ds:N', hash: '…', data: …, sideChannel: {}})  (data[0] is the request
    that produced the block: printed too)
  - <script id="__NEXT_DATA__" type="application/json">, any <script type="application/json">,
    <script type="application/ld+json">
  - window.__INITIAL_STATE__ = {…} / window.__APOLLO_STATE__ = … style assignments, when they parse as JSON

All decoded blocks are written to --out (default ./.web-to-mcp/embedded-<host>.json) for a closer look.
"""

from __future__ import annotations

import argparse
import asyncio
import html as html_lib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import CHROME_CHOICES, CHROME_HELP, cookie_header, load_session, output_path, page_title, parse_pairs, with_params  # noqa: E402

# The options between key and data vary (hash, isError…): allow a short run of anything but braces.
AF_BLOCK = re.compile(r"AF_initDataCallback\(\{key:\s*'([^']+)'([^{}]{0,200}?)data:", re.S)
AF_HASH = re.compile(r"hash:\s*'([^']*)'")
AF_END = ", sideChannel: {}"
SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.I | re.S)
ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
JSON_TYPES = ("application/json", "application/ld+json")
WINDOW_STATE = re.compile(r"""window(?:\.(__[A-Za-z0-9_]+__)|\[\s*["'](__[A-Za-z0-9_]+__)["']\s*\])\s*=\s*""")


def _attrs(text: str) -> dict[str, str]:
    return {m.group(1).lower(): next(g for g in m.groups()[1:] if g is not None) for m in ATTR.finditer(text)}


def extract_blocks(page: str) -> list[dict[str, Any]]:
    """Every JSON block embedded in the page, in document order: {kind, key, size, data, [hash]}.
    size is the length of the raw JSON text; blocks that do not parse are skipped."""
    found: list[tuple[int, dict[str, Any]]] = []

    for match in AF_BLOCK.finditer(page):
        start = match.end()
        end = page.find(AF_END, start)
        if end < 0:
            continue
        raw = page[start:end].strip()
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        block = {"kind": "AF_initDataCallback", "key": match.group(1), "size": len(raw), "data": data}
        if hash_match := AF_HASH.search(match.group(2)):
            block["hash"] = hash_match.group(1)
        found.append((match.start(), block))

    for match in SCRIPT_TAG.finditer(page):
        attrs = _attrs(match.group(1))
        kind = attrs.get("type", "").lower()
        if kind not in JSON_TYPES:
            continue
        raw = match.group(2).strip()
        try:
            data = json.loads(raw)
        except ValueError:
            try:  # some servers HTML-escape the JSON inside the tag
                data = json.loads(html_lib.unescape(raw))
            except ValueError:
                continue
        label = "__NEXT_DATA__" if attrs.get("id") == "__NEXT_DATA__" else ("ld+json" if "ld+json" in kind else "application/json")
        found.append((match.start(), {"kind": label, "key": attrs.get("id", ""), "size": len(raw), "data": data}))

    decoder = json.JSONDecoder()
    for match in WINDOW_STATE.finditer(page):
        try:
            data, end = decoder.raw_decode(page, match.end())
        except ValueError:
            continue  # a JS expression, not JSON
        name = match.group(1) or match.group(2)
        found.append((match.start(), {"kind": "window", "key": name, "size": end - match.end(), "data": data}))

    return [block for _, block in sorted(found, key=lambda item: item[0])]


def compact(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_block(block: dict[str, Any], grep: str | None = None, head: int = 200) -> dict[str, Any]:
    """What the listing shows for one block: label, size, grep hit, head, and for AF blocks the request (data[0])."""
    text = json.dumps(block["data"], ensure_ascii=False)
    info = {
        "label": f"{block['kind']} {block['key']}".strip(),
        "size": block["size"],
        "match": bool(grep) and grep.lower() in text.lower(),
        "head": compact(block["data"], head),
    }
    if block["kind"] == "AF_initDataCallback" and isinstance(block["data"], list) and block["data"]:
        info["request"] = compact(block["data"][0], 300)
    return info


def listing(blocks: list[dict[str, Any]], grep: str | None = None) -> str:
    lines = []
    for block in blocks:
        info = describe_block(block, grep)
        flag = f"  <-- contains {grep!r}" if info["match"] else ""
        lines.append(f"\n## {info['label']}   {info['size']:,} chars{flag}")
        if "request" in info:
            lines.append(f"   request data[0]: {info['request']}")
        lines.append(f"   head: {info['head']}")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> None:
    from curl_cffi import AsyncSession

    url = with_params(args.url, parse_pairs(args.param, "--param"))
    host = urlsplit(url).hostname or ""
    cookies, profile, chrome = load_session(args.domain, url, args.chrome, args.profile)
    if cookies:
        print(f"Session: {chrome.user_data_dir}, profile \"{chrome.profiles().get(profile, profile)}\", "
              f"{len({c.name for c in cookies})} cookies for {host}")
    else:
        print(f"Warning: no cookie of {args.domain} applies to {host} in {chrome.user_data_dir}: fetching without a session.")
    headers = {"cookie": cookie_header(cookies)} if cookies else {}
    async with AsyncSession(impersonate="chrome") as http:
        response = await http.get(url, headers=headers, timeout=30)
    page = response.text
    title = page_title(page)
    print(f"HTTP {response.status_code}, {len(page):,} chars, final URL {str(response.url).split('?')[0]}"
          f"{f', title {title!r}' if title else ''}")

    blocks = extract_blocks(page)
    hits = sum(describe_block(b, args.grep)["match"] for b in blocks) if args.grep else 0
    print(f"{len(blocks)} embedded JSON blocks" + (f", {hits} containing {args.grep!r}" if args.grep else ""))
    if args.grep and args.grep.lower() in page.lower() and not hits:
        print(f"(the raw HTML contains {args.grep!r}, but outside the recognised blocks: search the saved page)")
    print(listing(blocks, args.grep))

    out = output_path(args.out, "embedded", host)
    out.write_text(json.dumps({
        "url": url,
        "status": response.status_code,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "blocks": blocks,
    }, ensure_ascii=False, indent=1))
    print(f"\nDecoded blocks saved to {out}")
    if not blocks:
        print("Nothing embedded: the data probably comes from API calls. Next: capture.py (network) or bundle_scan.py.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("domain", help="cookie domain of the site (e.g. google.com)")
    parser.add_argument("url", help="the page that shows the wanted data")
    parser.add_argument("--param", action="append", help="query parameter name=value (repeatable)")
    parser.add_argument("--grep", help="flag the blocks containing this text (case-insensitive)")
    parser.add_argument("--chrome", choices=CHROME_CHOICES, help=CHROME_HELP)
    parser.add_argument("--profile", help="Chrome profile (folder or name); default: the most recently used")
    parser.add_argument("--out", help="JSON file for the decoded blocks (default ./.web-to-mcp/embedded-<host>.json)")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
