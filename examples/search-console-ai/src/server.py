"""Search Console AI MCP: the "Generative AI performance" report of Google Search Console (Search and Discover).

The official Search Console API (revision 2026-11-04) does not expose this report: its search types are WEB, IMAGE,
VIDEO, NEWS, DISCOVER and GOOGLE_NEWS, nothing for generative AI. So the server reads the report page itself: its
HTML already carries every value, in AF_initDataCallback({key: 'ds:N', ..., data: [...]}) blocks. No batchexecute
call (and no "at" XSRF token) is needed. Mapped on 2026-11-06, see README.md.

Undocumented source: it can change without notice.
"""

import asyncio
import json
import re
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import Field
from webapp_mcp import ToolAnnotations, ToolError, WebApp, prune


def google_auth_error(response) -> bool:
    """Rejected session: Google does not answer 401, it serves its sign-in page with HTTP 200, whose <base> tag points
    to accounts.google.com/v3/signin."""
    if response.status_code in (401, 403):
        return True
    url = str(getattr(response, "url", "") or "")
    return "accounts.google.com" in url or "accounts.google.com/v3/signin" in response.text[:5000]


app = WebApp(
    "search-console-ai",
    domain="google.com",
    # Minimal set found by elimination among the 25 cookies Chrome sends to search.google.com.
    cookies=["__Secure-3PSID", "__Secure-3PSIDTS"],
    login_url="https://search.google.com/search-console",
    base_url="https://search.google.com",
    tool_prefix="gsc",
    is_auth_error=google_auth_error,
    version="0.1.1",
)

# --- Reading the page ----------------------------------------------------------------------------------------------

SURFACE_PATH = {
    "search": "/search-console/performance/search-analytics/ai",
    "discover": "/search-console/performance/discover/ai",
}
BLOCK_RE = re.compile(r"AF_initDataCallback\(\{key: '(ds:\d+)', hash: '[^']*', data:")
# Dimension codes in the embedded request (req[5][0][0]).
DIM_DATE, DIM_PAGE, DIM_COUNTRY, DIM_DEVICE = 1, 3, 4, 7
DEVICES = {1: "desktop", 2: "mobile", 3: "tablet"}  # labels checked in the UI
# Block ds:N: req[1] = 27 for a time series, 32 for a table.
KIND_SERIES, KIND_TABLE = 27, 32
PROPERTY_TYPES = {1: "url_prefix", 5: "domain"}  # 9 = YouTube creator profile: no web report, skipped
DAYS = {"7d": 7, "28d": 28}
MONTHS = {"3m": 3, "6m": 6, "12m": 12, "16m": 16}


def data_blocks(html: str) -> list[Any]:
    """Every JSON data block of the page."""
    blocks = []
    for match in BLOCK_RE.finditer(html):
        start = match.end()
        end = html.find(", sideChannel: {}", start)
        if end < 0:
            continue
        try:
            blocks.append(json.loads(html[start:end]))
        except ValueError:
            continue
    return blocks


def describe(block: Any) -> tuple[int | None, int | None, bool]:
    """(block kind, dimension, surface filter present), read from the request embedded at the head of the block."""
    try:
        req = block[0]
        options = req[5]
        return req[1], options[0][0], bool(options[3])
    except (IndexError, TypeError, KeyError):
        return None, None, False


def find_block(blocks: list[Any], kind: int, dim: int) -> Any | None:
    # The "last 24 hours" block (a one-point series) has no surface filter: skip it.
    return next((b for b in blocks if describe(b) == (kind, dim, True)), None)


def series_points(block: Any) -> list[tuple[str, int]]:
    """Daily series: each point is stamped at noon UTC of its day."""
    points = []
    for point in block[1][0] or []:
        day = datetime.fromtimestamp(point[0] / 1000, timezone.utc).date().isoformat()
        value = point[1][0] if point[1] else None
        points.append((day, int(value) if isinstance(value, (int, float)) else 0))
    return points


def table_rows(block: Any, dim: int) -> list[dict]:
    """Rows of a table (pages, countries or devices), in Google's order (impressions, descending)."""
    rows = []
    for entry in block[1][0] or []:
        key, metrics = entry[0][0], entry[0][1]
        impressions = metrics[1] if len(metrics) > 1 else None
        if dim == DIM_PAGE:
            label = key[-1]
        else:
            # Country: {"519508101": ["usa"]}; device: {"519508101": [null, 1]}.
            value = next(iter(key[17].values())) if len(key) > 17 and isinstance(key[17], dict) else None
            if dim == DIM_COUNTRY:
                label = (value[0] or "").upper() if value else None
            else:
                label = DEVICES.get(value[1], str(value[1])) if value and len(value) > 1 else None
        rows.append({"key": label, "impressions": impressions})
    return rows


async def property_id(prop: str) -> str:
    """Exact id of a property. An sc-domain: id or a URL is kept as is; a bare name ("example.com") is looked up in
    the account's properties: domain property first, then a URL-prefix property on that host (with or without www)."""
    p = prop.strip()
    if p.startswith(("sc-domain:", "http://", "https://")):
        return p
    host = p.lower().removeprefix("www.").rstrip("/")
    props = await list_properties()
    for candidate in props:
        if candidate["id"] == f"sc-domain:{host}":
            return candidate["id"]
    for candidate in props:
        if re.sub(r"^https?://(www\.)?", "", candidate["id"]).rstrip("/") == host:
            return candidate["id"]
    return f"sc-domain:{host}"


def period_params(period: str | None, start_date: str | None, end_date: str | None) -> tuple[dict, str]:
    if start_date or end_date:
        if not (start_date and end_date):
            raise ToolError("Give both start_date and end_date (YYYY-MM-DD), or a period.")
        try:
            start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        except ValueError:
            raise ToolError("Dates must be YYYY-MM-DD, for example 2026-10-01.") from None
        if start > end:
            raise ToolError("start_date must be on or before end_date.")
        return {"start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, f"{start} to {end}"
    p = (period or "28d").lower()
    if p in DAYS:
        return {"num_of_days": DAYS[p]}, p
    if p in MONTHS:
        return {"num_of_months": MONTHS[p]}, p
    raise ToolError(f"Unknown period '{period}': use 7d, 28d, 3m, 6m, 12m or 16m (or start_date + end_date).")


async def load_page(surface: str, params: dict) -> list[Any]:
    response = await app.client.request("GET", SURFACE_PATH[surface], params={**params, "hl": "en"})
    if response.status_code != 200:
        raise ToolError(f"Search Console answered HTTP {response.status_code}.")
    # Property not in the account: Google serves (HTTP 200) "Oops, you don't have access to this property".
    # The title comes after ~1 MB of scripts, so search the whole page.
    if "have access to this property</title>" in response.text:
        raise ToolError(
            f"The Google account signed in to Chrome has no access to '{params.get('resource_id')}'. "
            "See gsc_list_properties for the available properties."
        )
    return data_blocks(response.text)


async def fetch_report(prop: str, surface: str, period: str | None, start_date: str | None, end_date: str | None, *, with_devices: bool, with_pages: bool = True) -> dict:
    """Loads the report page (and its breakdown=device variant when asked) and decodes its blocks."""
    pid = await property_id(prop)
    pparams, label = period_params(period, start_date, end_date)
    base = {"resource_id": pid, **pparams}
    calls = [load_page(surface, base)]
    if with_devices:
        # The device table is only served with breakdown=device, where it replaces the page table.
        calls.append(load_page(surface, {**base, "breakdown": "device"}))
    pages = await asyncio.gather(*calls)
    main = pages[0]
    series = find_block(main, KIND_SERIES, DIM_DATE)
    if series is None:
        raise ToolError(
            f"No AI report for '{pid}': Google does not show this report for the property yet (not enough "
            "impressions in AI answers), or the page format changed."
        )
    points = series_points(series)
    report = {
        "property": pid,
        "surface": surface,
        "period": label,
        "from": points[0][0] if points else None,
        "to": points[-1][0] if points else None,
        "total_impressions": sum(v for _, v in points),
        "daily": points,
        "countries": table_rows(b, DIM_COUNTRY) if (b := find_block(main, KIND_TABLE, DIM_COUNTRY)) else [],
    }
    if with_pages:
        report["pages"] = table_rows(b, DIM_PAGE) if (b := find_block(main, KIND_TABLE, DIM_PAGE)) else []
    if with_devices:
        b = find_block(pages[1], KIND_TABLE, DIM_DEVICE)
        report["devices"] = table_rows(b, DIM_DEVICE) if b else []
    return report


async def list_properties() -> list[dict]:
    """Properties of the account, read from the block that feeds the property picker."""
    response = await app.client.request("GET", "/search-console", params={"hl": "en"})
    for block in data_blocks(response.text):
        try:
            entries = block[0]
            if not (isinstance(entries, list) and entries and isinstance(entries[0], list) and isinstance(entries[0][0], str)):
                continue
            if not entries[0][0].startswith(("sc-domain:", "http://", "https://", "sc-creator-profile:")):
                continue
        except (IndexError, TypeError):
            continue
        return [
            {"id": e[0], "type": PROPERTY_TYPES[e[1]], "name": e[5] if len(e) > 5 and e[5] else e[0]}
            for e in entries
            if len(e) > 1 and e[1] in PROPERTY_TYPES
        ]
    return []


def with_share(rows: list[dict], total: int) -> list[dict]:
    return [{**r, "share_pct": round(100 * r["impressions"] / total, 1) if total and r["impressions"] is not None else None} for r in rows]


# --- Tools ---------------------------------------------------------------------------------------------------------

PropertyParam = Annotated[
    str,
    Field(description="Search Console property: a domain ('example.com' = domain property sc-domain:example.com) or a URL prefix ('https://example.com/'). See gsc_list_properties."),
]
PeriodParam = Annotated[
    Literal["7d", "28d", "3m", "6m", "12m", "16m"] | None,
    Field(description="Rolling period ending on the last available day (default 28d). Ignored when start_date and end_date are given."),
]
StartParam = Annotated[str | None, Field(description="Start of a custom period, YYYY-MM-DD (with end_date)")]
EndParam = Annotated[str | None, Field(description="End of a custom period, YYYY-MM-DD, included (with start_date)")]
SurfaceParam = Annotated[
    Literal["search", "discover"] | None,
    Field(description="search = AI Overviews and AI Mode in Google Search (default); discover = generative AI in Discover"),
]


@app.tool(title="List properties", annotations=ToolAnnotations(readOnlyHint=True))
async def gsc_list_properties() -> Any:
    """Search Console properties of the Google account signed in to Chrome: id to pass to the other tools, type
    (domain or url_prefix) and display name."""
    props = await list_properties()
    if not props:
        raise ToolError("Property list not found in the Search Console page (format changed?).")
    return props


@app.tool(title="AI report overview", annotations=ToolAnnotations(readOnlyHint=True))
async def gsc_ai_overview(
    property: PropertyParam,
    period: PeriodParam = None,
    start_date: StartParam = None,
    end_date: EndParam = None,
    surface: SurfaceParam = None,
    top: Annotated[int | None, Field(ge=1, le=100, description="Number of pages and countries returned (default 10)")] = None,
    daily: Annotated[bool | None, Field(description="Include the day-by-day series (default true)")] = None,
) -> Any:
    """"Generative AI performance" report of a property: total impressions in Google's AI answers (AI Overviews,
    AI Mode), daily series, most seen pages and countries, and the split by device, each row with its share in %.
    The report only counts impressions: Google gives no clicks or positions here. Data ends on the last consolidated
    day (usually 2 or 3 days ago)."""
    r = await fetch_report(property, surface or "search", period, start_date, end_date, with_devices=True)
    total, n = r["total_impressions"], top or 10
    days = [v for _, v in r["daily"]]
    return prune(
        {
            "property": r["property"],
            "surface": r["surface"],
            "period": r["period"],
            "from": r["from"],
            "to": r["to"],
            "total_impressions": total,
            "daily_average": round(total / len(days), 1) if days else None,
            "best_day": max(r["daily"], key=lambda p: p[1]) if total else None,
            "worst_day": min(r["daily"], key=lambda p: p[1]) if total else None,
            "pages_count": len(r["pages"]),
            "countries_count": len(r["countries"]),
            "top_pages": with_share(r["pages"][:n], total),
            "top_countries": with_share(r["countries"][:n], total),
            "devices": with_share(r["devices"], total),
            "daily": [{"date": d, "impressions": v} for d, v in r["daily"]] if daily is not False else None,
            "note": "Google caps the page table at 1000 rows." if len(r["pages"]) >= 1000 else None,
        }
    )


@app.tool(title="AI report breakdown", annotations=ToolAnnotations(readOnlyHint=True))
async def gsc_ai_breakdown(
    property: PropertyParam,
    dimension: Annotated[Literal["page", "country", "device", "date"], Field(description="Split by page, country (ISO 3-letter code), device or date")],
    period: PeriodParam = None,
    start_date: StartParam = None,
    end_date: EndParam = None,
    surface: SurfaceParam = None,
    contains: Annotated[str | None, Field(description="Keep only rows whose key contains this text, case-insensitive (e.g. '/fr/' for French pages, 'FRA')")] = None,
    limit: Annotated[int | None, Field(ge=1, le=1000, description="Maximum number of rows (default 100)")] = None,
    offset: Annotated[int | None, Field(ge=0, description="Rows to skip, for paging (default 0)")] = None,
) -> Any:
    """Full table of the AI report for one dimension: impressions by page, country, device or day, sorted as in
    Search Console (impressions descending, or dates ascending for date), with the share of the total in %. The
    contains filter applies after reading; total_impressions stays the whole property's and matched_impressions is
    the sum of the kept rows. Google caps the page table at 1000 rows."""
    r = await fetch_report(property, surface or "search", period, start_date, end_date, with_devices=dimension == "device", with_pages=dimension == "page")
    total = r["total_impressions"]
    if dimension == "date":
        rows = [{"key": d, "impressions": v} for d, v in r["daily"]]
    else:
        rows = r[{"page": "pages", "country": "countries", "device": "devices"}[dimension]]
    if contains:
        needle = contains.lower()
        rows = [row for row in rows if needle in str(row["key"]).lower()]
    start, size = offset or 0, limit or 100
    window = rows[start : start + size]
    return prune(
        {
            "property": r["property"],
            "surface": r["surface"],
            "dimension": dimension,
            "period": r["period"],
            "from": r["from"],
            "to": r["to"],
            "total_impressions": total,
            "matched_rows": len(rows),
            "matched_impressions": sum(row["impressions"] or 0 for row in rows),
            "offset": start,
            "rows": with_share(window, total),
            "next_offset": start + size if start + size < len(rows) else None,
        }
    )


if __name__ == "__main__":
    app.run()
