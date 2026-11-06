# Search Console AI (MCP example)

Google Search Console's **generative AI performance** report as MCP tools: impressions of your pages in Google's AI
answers (AI Overviews, AI Mode) and in Discover, by day, page, country and device. The official Search Console API
does not expose this report, so the server reads the report page itself with your Google session from Chrome AI.

Built with the web-to-mcp template; read `src/server.py` as a commented, complete example.

## Install

1. Set up Chrome AI (`/web-to-mcp:setup-ai-browser` in Claude Code, or the launcher alone, see the repository
   README) and sign in to Google there, with an account that has access to your Search Console properties.
2. Download `search-console-ai.mcpb` from the [releases](https://github.com/sebbhm/web-to-mcp/releases) (or build
   it, below) and double-click it, or drag it into Claude Desktop → Settings → Extensions.
3. The first call asks macOS for access to "Chrome Safe Storage": click "Allow".

Then ask Claude, for example: "which pages of example.com are most cited in Google's AI answers this month?"

## Tools

| Tool | Purpose |
|---|---|
| `gsc_session` | Connection state: Chrome and profile used, session expiry |
| `gsc_list_properties` | Properties of the account (domain or URL prefix) |
| `gsc_ai_overview` | Total AI impressions, daily series, top pages and countries, devices, shares in % |
| `gsc_ai_breakdown` | Full table by page, country, device or date, `contains` filter, paging |

Periods: `7d`, `28d` (default), `3m`, `6m`, `12m`, `16m`, or `start_date` + `end_date` (YYYY-MM-DD). Surfaces:
`search` (default) or `discover`. A property can be given by its bare name ("example.com"): the server looks for
the domain property, then a URL-prefix property on that host.

The report only counts impressions (no clicks or positions), stops at the last consolidated day (usually 2 or 3
days ago), and exists only for properties with enough AI impressions. Google caps the page table at 1000 rows.

## How it works

- **Why not the official API**: `searchanalytics.query` (revision 2026-11-04) has no search type for generative AI
  (WEB, IMAGE, VIDEO, NEWS, DISCOVER, GOOGLE_NEWS only).
- **Source**: the report page's HTML carries every value in `AF_initDataCallback({key: 'ds:N', …, data: […]})`
  blocks, so no `batchexecute` call and no XSRF token are needed.
  - `/search-console/performance/search-analytics/ai` (Search) and `/search-console/performance/discover/ai`.
  - URL parameters: `resource_id`, `num_of_days` (7, 28), `num_of_months` (3…16) or `start_date`/`end_date`
    (YYYYMMDD), `breakdown=device`.
  - Each block starts with the request that produced it: `req[1]` is 27 (series) or 32 (table), `req[5][0][0]` the
    dimension (1 date, 3 page, 4 country, 7 device), `req[5][3]` the surface filter (absent on the "last 24 hours"
    block, which is skipped). Blocks are found by this content, never by their `ds:N` number.
  - Countries are ISO 3166 alpha-3 codes; devices are coded 1 desktop, 2 mobile, 3 tablet (checked in the UI).
  - Series points are stamped at noon UTC of their day. Total = sum of days = sum of countries = sum of devices.
  - The device table only exists with `breakdown=device`, where it replaces the page table: the overview makes two
    requests in parallel (about 7 MB of HTML each).
  - Properties: the block whose entries are `[id, type (1 URL prefix, 5 domain, 9 YouTube creator), …, name]`, in
    the `/search-console` page.
- **Auth**: `__Secure-3PSID` + `__Secure-3PSIDTS` are enough (found with `min_cookies.py` among the cookies Chrome
  sends to search.google.com). A single-cookie manual token cannot work here, so the example has none.
- **Traps**:
  - A rejected session is HTTP 200 with the sign-in page (`<base href="https://accounts.google.com/v3/signin/">`),
    not 401: hence `google_auth_error`.
  - A property without access is HTTP 200 "Oops, you don't have access to this property"; the title comes after
    about 1 MB of scripts.

## Build and test

From the repository root:

```bash
uv run template/tools/mcpcall.py examples/search-console-ai                        # list the tools
uv run template/tools/mcpcall.py examples/search-console-ai gsc_ai_overview '{"property": "example.com", "period": "7d"}'
SMOKE_PROPERTY=example.com uv run --no-project python examples/search-console-ai/smoke.py
uv run template/tools/pack.py examples/search-console-ai                           # builds search-console-ai.mcpb
```
