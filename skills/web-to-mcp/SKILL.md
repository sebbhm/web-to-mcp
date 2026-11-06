---
name: web-to-mcp
description: Build an MCP server (a .mcpb extension for Claude Desktop) for a web app that has no usable API, reusing the session the user opened in Chrome AI instead of a copied token. Use when the user wants an MCP, a connector or tools to get their data out of a site they are signed in to, when a site "has no API", or when an official API lacks the report they need.
argument-hint: "[site or page URL]"
allowed-tools:
  - Bash(dev-browser *)
  - Bash(chrome-ai *)
  - Bash(~/.local/bin/chrome-ai *)
  - Bash(uv run "${CLAUDE_PLUGIN_ROOT}/template/*)
---

Turn a page the user can see in their browser into an MCP server. The template lives in `${CLAUDE_PLUGIN_ROOT}/template`:

- `core/webapp_mcp/`: the engine. Reads the session from Chrome AI (or, on request, the regular Chrome), sends
  requests that look like Chrome, serves the MCP tools.
- `skeleton/` and `tools/new.py`: create an MCP that starts right away.
- `harness/`: discovery. `capture.py` (network calls of a page), `embedded_data.py` (data already in the HTML),
  `probe.py` (cookies and auth modes), `min_cookies.py` (smallest working cookie set), `bundle_scan.py` (endpoints
  named in the page's JavaScript), `har_summarize.py` (fallback from a DevTools HAR export).
- `tools/mcpcall.py` (call tools from the terminal), `tools/pack.py` (build the `.mcpb`).

A complete, commented example: `${CLAUDE_PLUGIN_ROOT}/examples/search-console-ai/` (Google Search Console's
generative AI report, which the official API does not expose). Engine API: `${CLAUDE_PLUGIN_ROOT}/template/README.md`.

New MCPs are created in the current working directory:
check with the user where they want it (a folder of their projects) before step 5.

## Rules

- **Official API first.** If an official API exposes the data, say so and prefer it. Check its reference (for a
  Google API, its discovery document) before concluding it does not.
- **Read-only by default.** A tool that writes (create, update, delete) only when the user explicitly asks, with
  `ToolAnnotations(readOnlyHint=False, destructiveHint=...)`.
- **Never a cookie or token value** in the conversation, code, files or logs. The harness masks values: do not work
  around it (no direct read of the cookie database, no `print` of a value).
- Say once that the site's internal API is undocumented (it can change) and that the site's terms of use may forbid
  automation.
- **Show real data before writing a tool**: the user sees what the endpoint or page returns and agrees.
- **Leave no browser open.** Run `chrome-ai status` before the first browser step. At the end, close the pages you
  opened, and `chrome-ai stop` if Chrome AI was stopped when you began (`capture.py` does this on its own).
- Long scripts for `dev-browser` go in a file run with `dev-browser --connect … run <file>`: complex heredocs can be
  refused by the shell safety check.

## 0. Prerequisites

`chrome-ai status` and `dev-browser --version` must answer, and `uv --version` too. If one is missing, run
`/web-to-mcp:setup-ai-browser` first.

## 1. Frame the job

Agree with the user on: the site, the page that shows the data, and 3 to 6 tools shaped around what they will ask
(not one tool per endpoint). Ask only what is missing. Make sure they are signed in to the site in Chrome AI (open
the page there with the `ai-browser` skill and look at a screenshot).

## 2. Find where the data comes from

Try these in order and stop at the first that shows the data:

**A. Data already in the HTML.** Many apps (Google products, Next.js sites, server-rendered dashboards) ship the
page's data inside the HTML. Then a plain GET is enough, with no internal API to reverse.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/template/harness/embedded_data.py" <domain> "<page url>" --grep "<a value you can see on the page>"
```

It lists Google `AF_initDataCallback` blocks (with the request that produced each one), `__NEXT_DATA__`, JSON
scripts and `window.__*_STATE__` assignments, and saves them all to `./.web-to-mcp/`. Decode the matching block,
then change the page's URL parameters (period, filters, tabs) and check which blocks change: often every option of
the report is reachable through the URL.

**B. Network calls of the page.** Capture what the page loads in Chrome AI, secrets masked:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/template/harness/capture.py" "<page url>" --wait 6
```

It prints method, host, path, parameters and body keys of each XHR/fetch call (for Google `batchexecute`, the
`rpcids` and `f.req`), and saves the masked capture with the start of each response. If the data loads only after an
action (a tab, a filter), do that action with the `ai-browser` skill while capturing, or read the capture's output
file.

**C. The page's JavaScript.** Endpoints that the page did not call yet:
`uv run "${CLAUDE_PLUGIN_ROOT}/template/harness/bundle_scan.py" "<page url>" --grep <keyword>`.

**D. Fallback: a HAR export.** The user does DevTools → Network → right click → "Save all as HAR", then
`uv run "${CLAUDE_PLUGIN_ROOT}/template/harness/har_summarize.py" <file.har> --host <api host>`.

## 3. Find the session

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/template/harness/probe.py" cookies <domain>
```

Cookie names per profile, expiry, JWTs spotted, likely session cookies flagged; never the values. No cookies: ask the
user to sign in to the site in Chrome AI, wait 30 s (Chrome writes cookies to disk about every 30 s), retry.

## 4. Validate the call and the auth

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/template/harness/probe.py" call <domain> <METHOD> <url> [--json '{…}'] [--param k=v] [--header k=v] --compare
```

The table compares all cookies, each JWT cookie as `Bearer`, no auth, and no Chrome impersonation:

- the simplest mode that returns the data becomes `auth` in `WebApp` (cookies by default, or `bearer_auth("name")`);
- the "no auth" row is the signature of a rejected session, for `is_auth_error`. Many sites answer a rejected
  session with HTTP 200 and a sign-in page, not 401: detect that page (Google: `accounts.google.com/v3/signin`);
- 403 everywhere, even impersonating Chrome: a bot protection stronger than the template handles. Stop and explain.

Then find the smallest cookie set, so the MCP does not depend on cookies it does not need:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/template/harness/min_cookies.py" <domain> "<url>" [--param k=v] --marker "<text only a signed-in page contains>"
```

It prints the `--cookies a,b` string for step 5. Replay once without `--compare` and **show the user what comes back**
before writing a tool. Note required headers and traps in comments where they are handled.

## 5. Create the MCP

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/template/tools/new.py" <Folder> --display "<Display name>" --domain <domain> --cookies <c1[,c2]> \
    --login-url <https://…> --base-url <https://…> --prefix <pfx> --description "<one sentence>"
uv run "${CLAUDE_PLUGIN_ROOT}/template/tools/mcpcall.py" <Folder> <pfx>_session '{}'
```

`--login-url`: the page that shows the data (it redirects to sign-in when needed). `--base-url`: the only host the
MCP may call. The session check must name Chrome AI as its source.

## 6. Write the tools (`<Folder>/src/server.py`)

- `WebApp(...)`: `auth`, `is_auth_error` and `headers` from step 4 (see the example).
- Tools `@app.tool(title=…, annotations=ToolAnnotations(readOnlyHint=True))`, named `<pfx>_<verb>_<object>`. A
  usage-oriented docstring: what comes back, units, defaults, limits. Parameters as
  `Annotated[type, Field(description=…)]`.
- Calls: `await app.client.request_json(method, path, params=…, json=…)`, or `request(...)` for HTML or a file.
  For data embedded in HTML, parse it with small pure functions and identify blocks by their content (the request
  they carry, their shape), never by their position or `ds:N` number, which change.
- Responses: aggregate and paginate on the server, resolve names to ids, add totals and shares the user will ask for,
  drop noise with `prune`. Offer `raw: bool` as an escape hatch for JSON APIs.
- User-facing errors: `raise ToolError("…")` (other exceptions reach Claude as a bare "Error executing tool"). Detect
  the site's "no access" or "not found" page and say it plainly.

## 7. Test

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/template/tools/mcpcall.py" <Folder>                       # tools exposed
uv run "${CLAUDE_PLUGIN_ROOT}/template/tools/mcpcall.py" <Folder> <tool> '{"arg": 1}'   # one call
uv run --no-project python <Folder>/smoke.py                 # fill CALLS first
```

Cross-check numbers against the page the user sees (a total equals the sum of its rows), test a bad input and a
resource the account cannot access, show real outputs to the user and adjust the format with them.

## 8. Package and install

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/template/tools/pack.py" <Folder>    # copies the engine, uv lock, tools → manifest, validation, .mcpb
open "<Folder>/<name>.mcpb"           # the user clicks "Install" in Claude Desktop
```

The `.mcpb` works in Claude Desktop, including Cowork, on any Mac where Chrome AI is signed in to the site. Fill in
the MCP's `README.md`: tools, data source, traps. Logs: `~/Library/Logs/Claude/mcp-server-<Display name>.log`
(lines `[webapp_mcp]` come from the engine).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `KeychainError` | the "Chrome Safe Storage" prompt was refused: retry and click "Allow" |
| `LoginRequired` while signed in | Chrome writes cookies to disk about every 30 s: wait and retry; or the user signed in to their regular Chrome instead of Chrome AI (set `WEBAPP_CHROME=chrome` in the extension settings to read the regular Chrome) |
| HTTP 200 but sign-in page | rejected session: `is_auth_error` must detect that page |
| HTTP 403 with HTML | bot protection: check with `probe.py --compare`; some sites also need their firewall cookie (e.g. `aws-waf-token`) in `cookies` |
| "Error executing tool X" with no detail | an exception other than `ToolError`: read the server log |
