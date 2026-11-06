"""Tests of har_summarize on a hand-made HAR: endpoint grouping and secret masking.

    cd template/harness && uv run --with pytest pytest -q
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from har_summarize import path_template, redact, summarize  # noqa: E402

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjE5OTk5OTk5OTl9.c2lnbmF0dXJlLXNlY3JldGU"
SECRET_COOKIE = "s3cr3t-session-value-0123456789abcdef0123456789"


def entry(method: str, url: str, *, body: dict | None = None, response: dict | None = None, resource: str = "fetch", mime: str = "application/json") -> dict:
    return {
        "_resourceType": resource,
        "request": {
            "method": method,
            "url": url,
            "headers": [
                {"name": "Authorization", "value": f"Bearer {JWT}"},
                {"name": "Cookie", "value": f"sid={SECRET_COOKIE}; theme=dark"},
                {"name": "platform", "value": "web"},
                {"name": "x-csrf-token", "value": "csrf-abc"},
                {"name": "x-request-id", "value": "0f8e1a2b3c4d5e6f7a8b9c0d1e2f3a4b"},
                {"name": "User-Agent", "value": "Mozilla"},
            ],
            "cookies": [],
            **({"postData": {"mimeType": "application/json", "text": json.dumps(body)}} if body else {}),
        },
        "response": {"status": 200, "content": {"mimeType": mime, "text": json.dumps(response or {"ok": True})}},
    }


HAR = {
    "log": {
        "entries": [
            entry("GET", "https://api.site.com/v1/orders/12345?page=2&size=50", response={"data": [{"id": 1, "total": 9.5}], "page": 2}),
            entry("GET", "https://api.site.com/v1/orders/67890?page=3"),
            entry("POST", "https://api.site.com/v1/search", body={"query": "x", "filters": {"from": "2026-01-01"}}),
            entry("GET", "https://site.com/static/app.js", resource="script", mime="application/javascript"),
        ]
    }
}


def test_groups_identifiers():
    assert path_template("/v1/orders/12345") == "/v1/orders/{id}"
    assert path_template("/v1/users/550e8400-e29b-41d4-a716-446655440000/items") == "/v1/users/{id}/items"
    assert path_template("/v1/portfolio/queryAll") == "/v1/portfolio/queryAll"


def test_masks_sensitive_values():
    assert redact("x-csrf-token", "abc") == "<masked, 3 chars>"
    assert redact("x-trace", JWT).startswith("<masked")
    assert redact("platform", "web") == "web"


def test_summary_without_any_secret():
    text = summarize(HAR)
    assert "GET https://api.site.com/v1/orders/{id}   ×2" in text
    assert "params   : page, size" in text
    assert '"platform": "web"' in text
    assert "Authorization header Bearer <masked>" in text
    assert "cookies sid, theme" in text
    assert "static/app.js" not in text  # not an API request
    for secret in (JWT, SECRET_COOKIE, "csrf-abc", "0f8e1a2b3c4d5e6f7a8b9c0d1e2f3a4b"):
        assert secret not in text
