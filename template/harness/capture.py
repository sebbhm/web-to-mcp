# /// script
# requires-python = ">=3.11"
# ///
"""Capture the API calls (xhr/fetch) a page makes, in Chrome AI, and summarise them with secrets masked.

    uv run harness/capture.py "https://search.google.com/search-console/performance/search-analytics/ai?resource_id=sc-domain:example.com"
    uv run harness/capture.py <page_url> --wait 10 --host-filter api.example.com

How: writes a dev-browser script to a temp file and runs `dev-browser --connect <cdp> run <file>`. The script opens
a named page (web-to-mcp-capture) in the running Chrome AI, so the requests carry the session signed in there;
records xhr/fetch requests and responses while the page loads and for --wait seconds; saves them with saveFile;
closes the page. If the CDP endpoint does not answer and `chrome-ai` is installed, `chrome-ai start` runs first.

Printed: method, host, path, query parameters, POST body keys, and for Google batchexecute the rpcids and f.req.
Saved (--out, default ./.web-to-mcp/capture-<host>.json): every request, masked, with the first 4 KB of each
response body. Masked everywhere: parameters named at, f.sid, key, token, access_token, auth, sig, signature;
any parameter, JSON key or header whose name contains token, auth, session, cookie, csrf, xsrf (also secret,
password, api key); JWT-looking values; Authorization and Cookie headers keep their NAME only.

Needs: dev-browser 1.x (npm i -g dev-browser) and Chrome AI running with remote debugging (chrome-ai start).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import output_path  # noqa: E402

PAGE_NAME = "web-to-mcp-capture"
BODY_LIMIT = 4096
DEV_BROWSER_TMP = Path.home() / ".dev-browser" / "v1" / "tmp"
PATH_MARKER = "WEB_TO_MCP_CAPTURE="

# --- Masking (pure) -----------------------------------------------------------------------------------------

SECRET_NAMES = {"at", "f.sid", "key", "token", "access_token", "auth", "sig", "signature"}
SECRET_FRAGMENT = re.compile(r"token|auth|session|cookie|csrf|xsrf|secret|passw|api[_-]?key", re.I)
NAME_ONLY_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie"}
JWT = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*")
JSON_PAIR = re.compile(r'"([^"\\]{1,64})"(\s*:\s*)"((?:[^"\\]|\\.)*)"')
FORM_PAIR = re.compile(r"(?<![\w.%-])([\w.\-]{1,64})=([^&\s\"'<>]*)")


def is_secret(name: str) -> bool:
    return name.lower() in SECRET_NAMES or bool(SECRET_FRAGMENT.search(name))


def masked(value: str) -> str:
    return f"<masked {len(value)} chars>"


def mask_pairs(pairs: list[tuple[str, str]]) -> list[list[str]]:
    return [[k, masked(v) if is_secret(k) else JWT.sub("<masked jwt>", v)] for k, v in pairs]


def mask_text(text: str) -> str:
    """Mask secrets inside free text (a response body, an unknown body format): JWTs, "secret_name": "value"
    JSON pairs and secret_name=value form pairs."""
    text = JWT.sub("<masked jwt>", text)
    text = JSON_PAIR.sub(lambda m: f'"{m[1]}"{m[2]}"{masked(m[3])}"' if is_secret(m[1]) else m[0], text)
    return FORM_PAIR.sub(lambda m: f"{m[1]}={masked(m[2])}" if is_secret(m[1]) else m[0], text)


def mask_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (masked(json.dumps(v)) if is_secret(k) else mask_json(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_json(v) for v in value]
    if isinstance(value, str):
        return JWT.sub("<masked jwt>", value)
    return value


def mask_headers(headers: dict[str, str] | None) -> dict[str, str]:
    result = {}
    for name, value in (headers or {}).items():
        lower = name.lower()
        if lower in NAME_ONLY_HEADERS:
            result[lower] = "<masked>"
        elif is_secret(lower):
            result[lower] = masked(str(value))
        else:  # URL-valued headers (:path, referer) carry query strings: mask their secret parameters too
            result[lower] = mask_text(str(value))
    return result


def looks_like_form(body: str) -> bool:
    return bool(re.fullmatch(r"[\w.\-%+]+=[^&]*(?:&[\w.\-%+]+=[^&]*)*&?", body.strip()))


def mask_body(body: str | None, content_type: str = "") -> dict[str, Any]:
    """Masked POST body: {"body": text} plus "fields" (form) or "json" (JSON) when the format is recognised."""
    if not body:
        return {}
    if "json" in content_type or body.lstrip()[:1] in ("{", "["):
        try:
            data = mask_json(json.loads(body))
            return {"body": json.dumps(data, ensure_ascii=False), "json": data}
        except ValueError:
            pass
    if "x-www-form-urlencoded" in content_type or looks_like_form(body):
        return {"body": mask_text(body), "fields": mask_pairs(parse_qsl(body, keep_blank_values=True))}
    return {"body": mask_text(body)}


def mask_entry(raw: dict[str, Any], body_limit: int = BODY_LIMIT) -> dict[str, Any]:
    """One captured request, ready to save or print: URL split (query masked), headers masked, body masked."""
    parts = urlsplit(raw.get("url", ""))
    request_headers = mask_headers(raw.get("request_headers"))
    entry: dict[str, Any] = {
        "method": raw.get("method", "GET"),
        "type": raw.get("type"),
        "status": raw.get("status"),
        "host": parts.hostname or "",
        "path": parts.path or "/",
        "query": mask_pairs(parse_qsl(parts.query, keep_blank_values=True)),
        "request_headers": request_headers,
    }
    entry |= {f"post_{k}": v for k, v in mask_body(raw.get("post_data"), request_headers.get("content-type", "")).items()}
    if raw.get("response_headers") is not None:
        entry["response_headers"] = mask_headers(raw["response_headers"])
    if raw.get("body_head") is not None:
        entry["response_length"] = raw.get("body_length")
        entry["response_head"] = mask_text(raw["body_head"][:body_limit])
    if raw.get("body_error"):
        entry["response_error"] = raw["body_error"]
    return entry


def mask_capture(raw: dict[str, Any], host_filter: str | None = None) -> dict[str, Any]:
    entries = [mask_entry(e) for e in raw.get("entries", [])]
    if host_filter:
        entries = [e for e in entries if host_filter in e["host"]]
    def strip(url):
        return urlsplit(url)._replace(query="", fragment="").geturl() if url else url
    return {
        "page": strip(raw.get("url")),
        "final_url": strip(raw.get("final_url")),
        "title": raw.get("title"),
        "captured_at": raw.get("captured_at"),
        "host_filter": host_filter,
        "entries": entries,
    }


# --- Summary (pure) -----------------------------------------------------------------------------------------


def short(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def body_keys(entry: dict[str, Any]) -> list[str]:
    if "post_fields" in entry:
        return list(OrderedDict.fromkeys(k for k, _ in entry["post_fields"]))
    if isinstance(entry.get("post_json"), dict):
        return list(entry["post_json"])
    return []


def batchexecute(entry: dict[str, Any]) -> dict[str, str] | None:
    """Google batchexecute details: rpcids (query) and f.req (form body)."""
    if not entry["path"].endswith("/batchexecute"):
        return None
    query = dict(entry["query"])
    fields = dict(entry.get("post_fields") or [])
    return {"rpcids": query.get("rpcids", "?"), "f.req": fields.get("f.req", "")}


def summarize(capture: dict[str, Any]) -> str:
    groups: OrderedDict[tuple, list[dict]] = OrderedDict()
    for e in capture["entries"]:
        key = (e["method"], e["host"], e["path"], tuple(k for k, _ in e["query"]), tuple(body_keys(e)),
               (batchexecute(e) or {}).get("rpcids"))
        groups.setdefault(key, []).append(e)
    lines = [f"{len(capture['entries'])} xhr/fetch requests, {len(groups)} distinct"
             + (f" (host filter: {capture['host_filter']})" if capture.get("host_filter") else "")]
    for (method, host, path, *_), entries in groups.items():
        first = entries[0]
        statuses = ", ".join(sorted({str(e["status"]) for e in entries}))
        lines.append(f"\n{method:<6} {statuses:<5} {host}{path}" + (f"   x{len(entries)}" if len(entries) > 1 else ""))
        if first["query"]:
            lines.append("   query: " + short(", ".join(f"{k}={short(v, 60)}" for k, v in first["query"]), 300))
        if keys := body_keys(first):
            lines.append("   body keys: " + ", ".join(keys))
        elif first.get("post_body"):
            lines.append(f"   body: {short(first['post_body'], 120)}")
        if be := batchexecute(first):
            lines.append(f"   rpcids: {be['rpcids']}")
            lines.append(f"   f.req: {short(be['f.req'], 400)}")
    return "\n".join(lines)


# --- dev-browser script --------------------------------------------------------------------------------------

SCRIPT = r"""
const CFG = __CONFIG__;
const SECRET_HEADER = /authorization|cookie|token|auth|session|csrf|xsrf/i;
const headersOf = (h) => Object.fromEntries(Object.entries(h || {}).map(([k, v]) => [k, SECRET_HEADER.test(k) ? "<masked>" : v]));
const entries = [];
const byRequest = new Map();
const pending = [];
const page = await browser.getPage(CFG.pageName);
const onRequest = (req) => {
  const type = req.resourceType();
  if (type !== "xhr" && type !== "fetch") return;
  const entry = { method: req.method(), url: req.url(), type, status: null,
                  request_headers: headersOf(req.headers()), post_data: req.postData() ?? null };
  if (entry.post_data === null && typeof req.hasPostData === "function" && req.hasPostData()) {
    pending.push(req.fetchPostData().then((d) => { entry.post_data = d ?? null; }, () => {}));
  }
  byRequest.set(req, entry);
  entries.push(entry);
};
const onResponse = (resp) => {
  const entry = byRequest.get(resp.request());
  if (!entry) return;
  entry.status = resp.status();
  entry.response_headers = headersOf(resp.headers());
  pending.push(resp.text().then(
    (t) => { entry.body_length = t.length; entry.body_head = t.slice(0, CFG.bodyLimit); },
    (e) => { entry.body_error = String(e).slice(0, 200); }));
};
let saved = null;
try {
  page.on("request", onRequest);
  page.on("response", onResponse);
  let error = null;
  try {
    await page.goto(CFG.url, { waitUntil: "domcontentloaded", timeout: CFG.gotoMs });
    await new Promise((r) => setTimeout(r, CFG.waitMs));
  } catch (e) {
    error = String(e).slice(0, 300);
  } finally {
    page.off("request", onRequest);
    page.off("response", onResponse);
  }
  await Promise.race([Promise.allSettled(pending), new Promise((r) => setTimeout(r, 5000))]);
  saved = await saveFile(CFG.fileName, JSON.stringify({
    url: CFG.url, final_url: page.url(), title: await page.title().catch(() => null),
    captured_at: new Date().toISOString(), error, entries }));
} finally {
  await browser.closePage(CFG.pageName).catch(() => {});
}
console.log(CFG.marker + saved);
"""


def build_script(url: str, wait_s: float, file_name: str, body_limit: int = BODY_LIMIT) -> str:
    config = {"url": url, "waitMs": int(wait_s * 1000), "gotoMs": 45000, "bodyLimit": body_limit,
              "fileName": file_name, "pageName": PAGE_NAME, "marker": PATH_MARKER}
    return SCRIPT.replace("__CONFIG__", json.dumps(config))


# --- Running -------------------------------------------------------------------------------------------------


def cdp_alive(cdp: str) -> bool:
    try:
        with urllib.request.urlopen(cdp.rstrip("/") + "/json/version", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def ensure_chrome_ai(cdp: str) -> None:
    if cdp_alive(cdp):
        return
    chrome_ai = shutil.which("chrome-ai") or next(
        (str(p) for p in [Path.home() / ".local/bin/chrome-ai"] if p.exists()), None)
    if not chrome_ai:
        sys.exit(f"No Chrome answers on {cdp}. Start Chrome AI (chrome-ai start) or pass --cdp.")
    print(f"No Chrome on {cdp}: running {chrome_ai} start")
    subprocess.run([chrome_ai, "start"], check=False)
    for _ in range(30):
        if cdp_alive(cdp):
            return
        time.sleep(0.5)
    sys.exit(f"Chrome AI did not open {cdp} after chrome-ai start.")


def run_capture(args: argparse.Namespace) -> dict[str, Any]:
    dev_browser = shutil.which("dev-browser")
    if not dev_browser:
        sys.exit("dev-browser not found: install it with `npm i -g dev-browser` (version 1.x).")
    ensure_chrome_ai(args.cdp)
    host = urlsplit(args.url).hostname or "page"
    file_name = f"web-to-mcp-capture-{re.sub(r'[^A-Za-z0-9.-]', '_', host)}-{int(time.time())}.json"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as script:
        script.write(build_script(args.url, args.wait, file_name))
    print(f"Capturing {urlsplit(args.url)._replace(query='').geturl()} in Chrome AI ({args.cdp}), {args.wait:g} s after load…")
    raw_path = DEV_BROWSER_TMP / file_name
    try:
        result = subprocess.run(
            [dev_browser, "--connect", args.cdp, "-t", str(int(args.wait) + 90), "--quiet-page", "run", script.name],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            if line.startswith(PATH_MARKER) and line[len(PATH_MARKER):] not in ("", "null"):
                raw_path = Path(line[len(PATH_MARKER):].strip())
        if result.returncode != 0 or not raw_path.exists():
            sys.exit(f"dev-browser failed (exit {result.returncode}):\n{result.stderr.strip()[-2000:]}")
        raw = json.loads(raw_path.read_text())
    finally:
        os.unlink(script.name)
        raw_path.unlink(missing_ok=True)  # unmasked: never leave it behind
    if raw.get("error"):
        print(f"Warning: navigation error: {raw['error']}")
    return raw


def main() -> None:
    port = os.environ.get("CHROME_AI_PORT", "").strip() or "9333"
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="page to load (the one that shows the wanted data)")
    parser.add_argument("--wait", type=float, default=6, help="seconds to keep recording after the page loads (default 6)")
    parser.add_argument("--cdp", default=f"http://127.0.0.1:{port}", help="Chrome AI DevTools endpoint (default $CHROME_AI_PORT or 9333)")
    parser.add_argument("--host-filter", help="keep only requests whose host contains this text")
    parser.add_argument("--out", help="JSON file for the masked capture (default ./.web-to-mcp/capture-<host>.json)")
    args = parser.parse_args()

    capture = mask_capture(run_capture(args), args.host_filter)
    title = capture.get("title")
    print(f"Final URL: {capture['final_url']}" + (f", title {title!r}" if title else ""))
    print(summarize(capture))
    out = output_path(args.out, "capture", urlsplit(args.url).hostname or "page")
    out.write_text(json.dumps(capture, ensure_ascii=False, indent=1))
    print(f"\nMasked capture (with the first {BODY_LIMIT // 1024} KB of each response) saved to {out}")
    if not capture["entries"]:
        print("No xhr/fetch request: the data may be embedded in the page (embedded_data.py), or need a click: "
              "drive the page with dev-browser, or raise --wait.")


if __name__ == "__main__":
    main()
