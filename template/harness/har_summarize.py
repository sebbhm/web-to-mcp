# /// script
# requires-python = ">=3.11"
# ///
"""Summarise a HAR export from Chrome DevTools (Network tab → right click → "Save all as HAR"): API endpoints,
methods, parameters, response shapes, headers and cookies sent. Sensitive values are masked.

    uv run harness/har_summarize.py capture.har [--host api.example.com] [--all]

Fallback when capture.py (Chrome AI + dev-browser) is not available. By default Chrome exports a "sanitized" HAR
(no cookies, no Authorization): the shape of the calls is enough, probe.py finds the auth.
"""

import argparse
import base64
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.dont_write_bytecode = True

SENSITIVE_NAME = re.compile(r"auth|cookie|token|session|secret|key|csrf|xsrf|sig|pass|jwt|bearer", re.I)
TOKEN_LIKE = re.compile(r"^[\w\-.~+/=]{32,}$|eyJ[\w-]+\.[\w-]+")
STANDARD_HEADERS = {
    "accept", "accept-encoding", "accept-language", "cache-control", "connection", "content-length", "content-type",
    "host", "origin", "pragma", "priority", "referer", "user-agent", "upgrade-insecure-requests", "dnt", "te",
}
ID_SEGMENT = re.compile(r"^(?:\d+|[0-9a-f]{16,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?=[\w-]*\d)[\w-]{20,})$", re.I)


def redact(name: str, value: str) -> str:
    """Mask any value that could be a secret: telling name or token-shaped value."""
    if SENSITIVE_NAME.search(name) or TOKEN_LIKE.search(value) or len(value) > 80:
        return f"<masked, {len(value)} chars>"
    return value


def path_template(path: str) -> str:
    return "/".join("{id}" if ID_SEGMENT.match(s) else s for s in path.split("/"))


def shape(value: object, depth: int = 0) -> object:
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        keys = list(value.items())[:25]
        return {k: shape(v, depth + 1) for k, v in keys} | ({"…": f"+{len(value) - 25} keys"} if len(value) > 25 else {})
    if isinstance(value, list):
        return [f"{len(value)} items", shape(value[0], depth + 1)] if value else []
    return "null" if value is None else type(value).__name__


def response_json(entry: dict) -> object | None:
    content = entry["response"].get("content", {})
    text = content.get("text")
    if not text:
        return None
    if content.get("encoding") == "base64":
        text = base64.b64decode(text).decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return None


def is_api_call(entry: dict) -> bool:
    mime = entry["response"].get("content", {}).get("mimeType", "")
    return entry.get("_resourceType") in ("xhr", "fetch") or "json" in mime


def cookie_names(request: dict) -> set[str]:
    names = {c["name"] for c in request.get("cookies", [])}
    for header in request.get("headers", []):
        if header["name"].lower() == "cookie":
            names |= {part.split("=", 1)[0].strip() for part in header["value"].split(";") if "=" in part}
    return names


def summarize(har: dict, host_filter: str | None = None, everything: bool = False) -> str:
    groups: dict[tuple, dict] = {}
    cookies_by_host: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    auth_schemes: dict[str, set[str]] = defaultdict(set)

    for entry in har["log"]["entries"]:
        request = entry["request"]
        url = urlsplit(request["url"])
        if host_filter and host_filter not in url.netloc:
            continue
        if not everything and not is_api_call(entry):
            continue
        key = (request["method"], url.netloc, path_template(url.path))
        group = groups.setdefault(key, {"count": 0, "status": set(), "params": set(), "headers": {}, "body": None, "response": None})
        group["count"] += 1
        group["status"].add(entry["response"]["status"])
        group["params"] |= {name for name, _ in parse_qsl(url.query, keep_blank_values=True)}
        for header in request.get("headers", []):
            name = header["name"].lower()
            if name == "authorization":
                auth_schemes[url.netloc].add(header["value"].split(" ", 1)[0] + " <masked>")
            elif name not in STANDARD_HEADERS and name != "cookie" and not name.startswith((":", "sec-")):
                group["headers"][name] = redact(name, header["value"])
        for name in cookie_names(request):
            cookies_by_host[url.netloc][name] += 1
        if group["body"] is None and request.get("postData", {}).get("text"):
            try:
                group["body"] = shape(json.loads(request["postData"]["text"]))
            except ValueError:
                group["body"] = f"{request['postData'].get('mimeType', '?')} ({len(request['postData']['text'])} chars)"
        if group["response"] is None:
            group["response"] = shape(response_json(entry)) if response_json(entry) is not None else None

    lines = [f"{len(groups)} endpoints{' (filter: ' + host_filter + ')' if host_filter else ''}\n"]
    for (method, host, path), g in sorted(groups.items(), key=lambda item: (item[0][1], item[0][2])):
        lines.append(f"## {method} https://{host}{path}   ×{g['count']}  HTTP {', '.join(map(str, sorted(g['status'])))}")
        if g["params"]:
            lines.append(f"   params   : {', '.join(sorted(g['params']))}")
        if g["headers"]:
            lines.append(f"   headers  : {json.dumps(g['headers'], ensure_ascii=False)}")
        if g["body"] is not None:
            lines.append(f"   body     : {json.dumps(g['body'], ensure_ascii=False)[:600]}")
        if g["response"] is not None:
            lines.append(f"   response : {json.dumps(g['response'], ensure_ascii=False)[:800]}")
    lines.append("\n## Auth seen in the capture")
    for host in sorted(set(cookies_by_host) | set(auth_schemes)):
        if auth_schemes[host]:
            lines.append(f"   {host}: Authorization header {', '.join(sorted(auth_schemes[host]))}")
        if cookies_by_host[host]:
            names = sorted(cookies_by_host[host], key=lambda n: (-cookies_by_host[host][n], n.lower()))
            lines.append(f"   {host}: cookies {', '.join(names)}")
    if not cookies_by_host and not auth_schemes:
        lines.append("   none (HAR sanitized by Chrome): use `probe.py cookies <domain>` then `probe.py call … --compare`")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("har", type=Path)
    parser.add_argument("--host", help="keep only calls to this host (substring)")
    parser.add_argument("--all", action="store_true", help="also keep non-API requests (pages, scripts…)")
    args = parser.parse_args()
    print(summarize(json.loads(args.har.read_text()), args.host, args.all))


if __name__ == "__main__":
    main()
