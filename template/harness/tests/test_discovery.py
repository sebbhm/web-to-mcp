"""Tests of the discovery helpers (no network, no Chrome): cookie applicability, greedy minimisation,
embedded-data extraction, capture masking and summary.

    cd template/harness && uv run --with pytest pytest -q
"""

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _common import applicable_cookies, cookie_applies, cookie_header, with_params  # noqa: E402
from capture import build_script, is_secret, mask_capture, mask_headers, mask_text, summarize  # noqa: E402
from embedded_data import describe_block, extract_blocks  # noqa: E402
from min_cookies import minimize  # noqa: E402

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjE5OTk5OTk5OTl9.c2lnbmF0dXJlLXNlY3JldGU"


@dataclass
class FakeCookie:
    name: str
    value: str
    host: str
    path: str = "/"
    expires: datetime | None = None

    @property
    def expired(self) -> bool:
        return self.expires is not None and self.expires <= datetime.now(timezone.utc)


# --- cookies ---------------------------------------------------------------------------------------------------


def test_cookie_applies_like_chrome():
    assert cookie_applies(".google.com", "/", "search.google.com", "/x")
    assert cookie_applies(".google.com", "/", "google.com", "/")
    assert cookie_applies("search.google.com", "/", "search.google.com", "/")
    assert cookie_applies(".search.google.com", "/", "search.google.com", "/")
    assert not cookie_applies("google.com", "/", "search.google.com", "/")  # host-only cookie of the parent
    assert not cookie_applies("accounts.google.com", "/", "search.google.com", "/")
    assert not cookie_applies(".oogle.com", "/", "search.google.com", "/")  # not a parent domain (no dot boundary)
    assert cookie_applies(".google.com", "/search-console", "search.google.com", "/search-console/performance")
    assert not cookie_applies(".google.com", "/search-console", "search.google.com", "/search-consoles")
    assert not cookie_applies(".google.com", "/admin", "search.google.com", "/")


def test_applicable_cookies_and_header():
    old = datetime(2000, 1, 1, tzinfo=timezone.utc)
    cookies = [
        FakeCookie("SID", "a", ".google.com"),
        FakeCookie("NID", "b", "search.google.com"),
        FakeCookie("OTHER", "c", "accounts.google.com"),
        FakeCookie("OLD", "d", ".google.com", expires=old),
        FakeCookie("NID", "e", ".google.com"),
    ]
    kept = applicable_cookies(cookies, "https://search.google.com/page?x=1")
    assert [c.name for c in kept] == ["NID", "NID", "SID"]  # most specific host first, expired dropped
    assert cookie_header(kept, ["NID"]) == "NID=b; NID=e"
    assert cookie_header(kept) == "NID=b; NID=e; SID=a"


def test_with_params_keeps_existing_query():
    assert with_params("https://a.com/p?x=1", {"y": "sc-domain:z.io"}) == "https://a.com/p?x=1&y=sc-domain%3Az.io"
    assert with_params("https://a.com/p", {}) == "https://a.com/p"


def test_minimize_keeps_only_required_names():
    required = {"__Secure-3PSID", "__Secure-3PSIDTS"}
    names = ["SID", "__Secure-3PSIDTS", "NID", "__Secure-3PSID", "AEC", "SID"]
    tried = []

    async def works(subset):
        tried.append(list(subset))
        return required <= set(subset)

    kept = asyncio.run(minimize(names, works, log=lambda *_: None))
    assert kept == sorted(required)
    assert len(tried) == 5  # one try per distinct name, in name order
    assert tried[0] == ["NID", "SID", "__Secure-3PSID", "__Secure-3PSIDTS"]  # AEC removed first


# --- embedded data ---------------------------------------------------------------------------------------------

PAGE = """<html><head><title>Report</title>
<script nonce="n">AF_initDataCallback({key: 'ds:9', hash: '3', data:[["sc-domain:x.io",27],[[[1793188800000,[9839]]]]], sideChannel: {}});</script>
<script>AF_initDataCallback({key: 'ds:1', isError:  false , hash: '7', data:[null,"a, sideChannel: {}-like"], sideChannel: {}});</script>
<script>AF_initDataCallback({key: 'ds:2', hash: '1', data:function(){return 1}, sideChannel: {}});</script>
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"slug":"find-phone-number-by-name"}}}</script>
<script type="application/ld+json">{"@type":"Organization","name":"X"}</script>
<script type="application/json" id="cfg">{"a":1}</script>
<script>window.__INITIAL_STATE__ = {"user":{"id":42}};window.__APOLLO_STATE__ = undefined;
window["__PRELOADED__"] = [1,2];</script>
</head></html>"""


def test_extract_blocks_finds_every_kind_in_order():
    blocks = extract_blocks(PAGE)
    labels = [(b["kind"], b["key"]) for b in blocks]
    assert labels == [
        ("AF_initDataCallback", "ds:9"),
        ("__NEXT_DATA__", "__NEXT_DATA__"),
        ("ld+json", ""),
        ("application/json", "cfg"),
        ("window", "__INITIAL_STATE__"),
        ("window", "__PRELOADED__"),
    ]  # ds:1 (marker inside a string ends it early: invalid JSON) and ds:2 (not JSON) are skipped
    assert blocks[0]["data"][1][0][0] == [1793188800000, [9839]]
    assert blocks[0]["hash"] == "3"
    assert blocks[4]["data"] == {"user": {"id": 42}}


def test_af_block_with_extra_options():
    page = "AF_initDataCallback({key: 'ds:3', isError:  false , hash: '2', data:[1,[2]], sideChannel: {}});"
    (block,) = extract_blocks(page)
    assert (block["key"], block["hash"], block["data"]) == ("ds:3", "2", [1, [2]])


def test_describe_block_flags_grep_and_request():
    blocks = extract_blocks(PAGE)
    af = describe_block(blocks[0], grep="SC-DOMAIN:X.IO")
    assert af["match"] and af["request"] == '["sc-domain:x.io",27]'
    nxt = describe_block(blocks[1], grep="find-phone-number-by-name")
    assert nxt["match"] and "request" not in nxt
    assert not describe_block(blocks[2], grep="find-phone")["match"]
    long = describe_block({"kind": "window", "key": "k", "size": 1, "data": list(range(1000))})
    assert len(long["head"]) == 200 and long["head"].endswith("…")


# --- capture masking -------------------------------------------------------------------------------------------

AT = "AJpMLdSecretXsrfToken0123456789:1793957786117"
FSID = "-1234567890123456789"
KEY = "AIzaSyFakeApiKey0123456789abcdefghijklm"
SESSION_COOKIE = "s3cr3t-cookie-value-abcdef"

RAW = {
    "url": "https://search.google.com/search-console/x?resource_id=sc-domain:x.io",
    "final_url": "https://search.google.com/search-console/x?resource_id=sc-domain:x.io",
    "title": "Report",
    "captured_at": "2026-11-06T00:00:00Z",
    "entries": [
        {
            "method": "POST",
            "type": "xhr",
            "status": 200,
            "url": f"https://search.google.com/_/App/data/batchexecute?rpcids=pPDvCb&f.sid={FSID}&hl=fr&_reqid=1",
            "request_headers": {
                ":path": f"/_/App/data/batchexecute?rpcids=pPDvCb&f.sid={FSID}&hl=fr",
                "Cookie": f"SID={SESSION_COOKIE}",
                "Authorization": f"SAPISIDHASH 123_{SESSION_COOKIE}",
                "X-Goog-AuthUser": "0",
                "x-csrf-token": "csrf-123456",
                "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
                "referer": f"https://search.google.com/x?token={SESSION_COOKIE}",
            },
            "post_data": "f.req=%5B%5B%5B%22pPDvCb%22%2C%22%5B121%5D%22%2Cnull%2C%22generic%22%5D%5D%5D&at=" + AT.replace(":", "%3A") + "&",
            "response_headers": {"set-cookie": f"SIDCC={SESSION_COOKIE}", "content-type": "application/json"},
            "body_length": 50,
            "body_head": f')]}}\'\n[["wrb.fr","pPDvCb","[]"],{{"access_token":"{SESSION_COOKIE}","jwt":"{JWT}"}}]',
        },
        {
            "method": "POST",
            "type": "fetch",
            "status": 200,
            "url": f"https://feedback-pa.clients6.google.com/v1/survey/trigger?key={KEY}",
            "request_headers": {"content-type": "application/json"},
            "post_data": json.dumps({"query": "x", "sessionId": SESSION_COOKIE, "nested": {"csrfToken": "csrf-123456"}}),
        },
        {"method": "GET", "type": "fetch", "status": 200, "url": "https://api.other.com/v1/items?page=2&signature=abcdef0123"},
    ],
}


def test_secret_names():
    for name in ("at", "f.sid", "key", "token", "access_token", "auth", "sig", "signature", "X-Goog-AuthUser",
                 "sessionId", "x-xsrf-token", "csrfToken", "api_key"):
        assert is_secret(name), name
    for name in ("rpcids", "hl", "f.req", "page", "monkey", "keyword", "signal"):
        assert not is_secret(name), name


def test_mask_headers_keeps_names_only_for_auth_and_cookie():
    headers = mask_headers(RAW["entries"][0]["request_headers"])
    assert headers["cookie"] == "<masked>" and headers["authorization"] == "<masked>"
    assert headers["x-goog-authuser"].startswith("<masked")
    assert "f.sid=<masked" in headers[":path"] and "rpcids=pPDvCb" in headers[":path"]
    assert "token=<masked" in headers["referer"]


def test_mask_text_handles_json_form_and_jwt():
    text = mask_text(f'{{"token": "{SESSION_COOKIE}", "name": "ok"}} at={AT}&hl=fr {JWT}')
    assert SESSION_COOKIE not in text and AT not in text and JWT not in text
    assert '"name": "ok"' in text and "hl=fr" in text


def test_masked_capture_contains_no_secret_and_keeps_structure():
    capture = mask_capture(RAW)
    dumped = json.dumps(capture)
    for secret in (AT, AT.replace(":", "%3A"), FSID, KEY, SESSION_COOKIE, JWT, "csrf-123456", "abcdef0123"):
        assert secret not in dumped, secret
    first = capture["entries"][0]
    assert dict(first["query"])["rpcids"] == "pPDvCb"
    assert dict(first["post_fields"])["f.req"] == '[[["pPDvCb","[121]",null,"generic"]]]'
    assert dict(first["post_fields"])["at"].startswith("<masked")
    assert capture["page"] == "https://search.google.com/search-console/x"  # query dropped from the page URL
    assert capture["entries"][1]["post_json"]["query"] == "x"


def test_summary_shows_batchexecute_and_body_keys():
    text = summarize(mask_capture(RAW))
    assert "3 xhr/fetch requests, 3 distinct" in text
    assert "POST   200   search.google.com/_/App/data/batchexecute" in text
    assert "rpcids: pPDvCb" in text
    assert 'f.req: [[["pPDvCb","[121]",null,"generic"]]]' in text
    assert "body keys: f.req, at" in text
    assert "body keys: query, sessionId, nested" in text
    assert FSID not in text and KEY not in text


def test_host_filter():
    capture = mask_capture(RAW, host_filter="api.other")
    assert [e["host"] for e in capture["entries"]] == ["api.other.com"]


def test_build_script_embeds_config_as_json():
    script = build_script('https://x.com/?q="a"', 2.5, "capture.json")
    assert "__CONFIG__" not in script
    config = json.loads(script.split("const CFG = ", 1)[1].split(";\n", 1)[0])
    assert config["url"] == 'https://x.com/?q="a"' and config["waitMs"] == 2500 and config["fileName"] == "capture.json"
    assert "browser.closePage(CFG.pageName)" in script and "saveFile(CFG.fileName" in script
    assert "require(" not in script and "process." not in script
