# web-to-mcp template: MCP servers for web apps without an API

Builds MCP servers, shipped as `.mcpb` extensions for Claude Desktop, for sites that have no official API. The MCP
reuses the session already signed in to Chrome: no token to copy, and it picks up a renewed session by itself.

**Easiest:** with the `web-to-mcp` plugin installed in Claude Code, open the folder where the new MCP should live
and ask "make me an MCP for <site>". The plugin's skill runs the steps below and stops where your approval
matters.

## Contents

```
template/
├── core/                 the engine, copied into each MCP
│   ├── webapp_mcp/       chrome.py (cookies + Keychain), session.py, client.py, app.py, fmt.py, jwt.py
│   └── tests/            tests without Chrome or network
├── skeleton/             empty MCP, filled in by tools/new.py
├── harness/              endpoint discovery (all read-only, never print a cookie value)
│   ├── probe.py          a site's cookies (names only); replay an endpoint, compare auth modes
│   ├── min_cookies.py    smallest cookie set a page needs (greedy elimination) → --cookies for new.py
│   ├── embedded_data.py  JSON embedded in a page's HTML (Google AF_initDataCallback, __NEXT_DATA__, …)
│   ├── capture.py        xhr/fetch calls of a page loaded in Chrome AI (dev-browser), secrets masked
│   ├── bundle_scan.py    endpoints cited in a page's JavaScript
│   ├── har_summarize.py  summary of a DevTools HAR export, secrets masked (fallback for capture.py)
│   └── tests/            tests without Chrome or network
└── tools/
    ├── new.py            creates an MCP from the skeleton, in the current directory
    ├── mcpcall.py        command-line MCP client (list, call)
    └── pack.py           copies the engine, locks, syncs the manifest, builds the .mcpb
```

Complete example: `examples/search-console-ai/` at the root of the repository (Google Search Console's AI report,
read from the data embedded in the page).

## Chrome AI or Chrome

The session is read from **Chrome AI** by default: a dedicated Chrome instance for the AI, with its own user data
folder (`$CHROME_AI_PROFILE`, default `~/.chrome-ai-profile`) and remote debugging on `http://127.0.0.1:9333`
(`$CHROME_AI_PORT`). `chrome-ai start` runs it in the background; `chrome-ai show [url]` makes it a visible window to sign in to the site once, `chrome-ai hide` sends it back. `capture.py` drives it
with the `dev-browser` CLI (`npm i -g dev-browser`).

To read the regular Chrome instead: `--chrome chrome` on the harness scripts, and `WEBAPP_CHROME=chrome` for an
MCP (the extension's "Chrome source" field), plus `CHROME_PROFILE` to pick a profile.

## Creating an MCP, in short

The tools live in the plugin; set `T` to this `template/` folder (e.g.
`T=~/.claude/plugins/cache/<marketplace>/web-to-mcp/<version>/template`). Run everything from the folder where
the MCP should be created: `new.py` creates it there, and the discovery output goes to `./.web-to-mcp/`.

```bash
uv run "$T/harness/probe.py" cookies mysite.com                                # 1. which session cookies?
uv run "$T/harness/embedded_data.py" mysite.com https://mysite.com/page --grep x   # 2a. data already in the page?
uv run "$T/harness/capture.py" https://mysite.com/page                         # 2b. which API calls? (Chrome AI)
uv run "$T/harness/bundle_scan.py" https://mysite.com/page --grep x            # 2c. endpoints in the JavaScript
uv run "$T/harness/probe.py" call mysite.com GET https://api.mysite.com/v1/x --compare   # 3. which auth?
uv run "$T/harness/min_cookies.py" mysite.com https://mysite.com/page --marker "text only signed-in pages show"   # 3b.
uv run "$T/tools/new.py" MySite --domain mysite.com --cookies sid \
    --login-url https://mysite.com/app --base-url https://api.mysite.com --prefix ms   # 4. create ./MySite
# 5. write the tools in MySite/src/server.py
uv run "$T/tools/mcpcall.py" MySite ms_my_tool '{}'                             # 6. test
uv run "$T/tools/pack.py" MySite && open MySite/my-site.mcpb                    # 7. package, install
```

`new.py` prints these follow-up commands with absolute paths, ready to paste.

## Discovery tools

| Script | Use it to |
|---|---|
| `probe.py cookies <domain> [--chrome ai\|chrome]` | List the cookies (names, hosts, expiry, length; never values) and spot the session ones. |
| `probe.py call <domain> <METHOD> <url> [--compare]` | Replay a request with the session; `--compare` tries every auth mode and shows what a rejected session looks like. |
| `embedded_data.py <domain> <url> [--param k=v] [--grep TEXT] [--out FILE]` | Find data the page already embeds: Google `AF_initDataCallback` blocks (with `data[0]`, the request that produced each block), `__NEXT_DATA__`, `application/json` and `ld+json` scripts, `window.__INITIAL_STATE__`-style assignments. Often no API call is needed at all. |
| `capture.py <url> [--wait 6] [--host-filter TEXT] [--out FILE]` | Load the page in Chrome AI and record its xhr/fetch calls: method, host, path, query, POST body keys, Google `batchexecute` rpcids and `f.req`. Secrets masked; the first 4 KB of each response saved. |
| `min_cookies.py <domain> <url> --marker TEXT [--param k=v]` | Reduce the cookies Chrome sends to the URL's host to the minimal set (a marker is required: some sites answer a rejected session with HTTP 200 and a sign-in page). Prints the `--cookies` value for `new.py`. |
| `bundle_scan.py <url> [--grep RE] [--with-session]` | List API paths cited in the page's JavaScript, including calls the page did not make. |
| `har_summarize.py <file.har>` | Summarise a DevTools HAR export when Chrome AI is not available. |

## The `webapp_mcp` engine

An adapter only deals with one object, `WebApp`:

```python
from typing import Annotated
from pydantic import Field
from webapp_mcp import ToolAnnotations, ToolError, WebApp, bearer_auth, prune

app = WebApp(
    "my-site",                                   # technical name (= extension name)
    domain="mysite.com",                         # cookie domain of the session (subdomains included)
    cookies=["sid"],                             # cookies to read from Chrome
    login_url="https://mysite.com/app",          # opened in Chrome when the session is missing or expired
    base_url="https://api.mysite.com",           # the only host requests may go to
    tool_prefix="ms",                            # tool prefix; also creates the ms_session tool
    auth=bearer_auth("sid"),                     # default: cookie_auth (cookies sent as they are)
    is_auth_error=lambda r: r.status_code == 401,  # default: HTTP 401 or 403
    headers={"platform": "web"},                 # headers the site requires
    manual_token_env="MS_AUTH_TOKEN",            # fallback token (optional field of the extension)
)

@app.tool(title="My orders", annotations=ToolAnnotations(readOnlyHint=True))
async def ms_list_orders(status: Annotated[str | None, Field(description="Filter by status")] = None) -> dict:
    """The account's orders, most recent first."""
    return prune(await app.client.request_json("GET", "/v1/orders", params={"status": status}))

if __name__ == "__main__":
    app.run()
```

| Element | Role |
|---|---|
| `app.tool(**kwargs)` | Declares a tool. A returned `dict`/`list` becomes compact JSON (one list item per line). Any exception becomes a readable message. |
| `app.client.request(method, path, params=, json=, headers=)` | Authenticated request impersonating Chrome (TLS fingerprint and headers, via `curl_cffi`). Returns the raw response. |
| `app.client.request_json(...)` | Same, returns the decoded JSON; `ApiError` if the status is not 2xx or the body is not JSON. |
| `cookie_auth`, `bearer_auth(name)` | Ways to send the session; a custom function can return `{"headers": {...}, "cookies": {...}}`. |
| `ToolError`, `LoginRequired`, `ApiError` | Errors whose message reaches Claude as is. MCP SDK v2 hides the others. |
| `prune`, `to_text` | Lighten responses (drop `None` and `""`) and render them as compact JSON. |
| `find_jwt`, `jwt_expiry`, `ChromeCookies` | Low-level helpers (read a JWT, read Chrome's cookies). |

## How auth works

Whenever a session is needed, the engine tries in order:

1. **The cookies read from Chrome AI** (or the regular Chrome with `WEBAPP_CHROME=chrome`): across profiles, the
   one whose session was used most recently wins (or the profile set by `CHROME_PROFILE`, the extension's
   "Chrome profile" field). Expired cookies, and JWTs whose `exp` date has passed, are ignored.
2. **The manual token**, if configured.
3. **Otherwise the sign-in page opens in that Chrome**: for Chrome AI, which runs in the background, as a visible
   window (`chrome-ai show <login_url>`); for the regular Chrome, in the right profile. The engine waits up to 45 s
   for the session to appear (Chrome writes its cookies to disk about every 30 s), then sends Chrome AI back to the
   background (`chrome-ai hide`). Past 45 s, it asks to retry the request, and does not reopen the page for 2
   minutes.

If the site rejects a session, that session is never reused: the engine looks for a fresh one and retries once.

Decryption uses the "Chrome Safe Storage" secret from the macOS Keychain, read with `/usr/bin/security`. The key
stays in memory. On the first read macOS asks for access: "Allow" covers that one time; "Always Allow" removes the
prompt, but then lets any program going through `security` decrypt Chrome's cookies.

## MCPB format

- Manifest 0.4, server type `uv`: the `.mcpb` (~70 KB) only holds the code, `pyproject.toml` and `uv.lock`.
  Claude Desktop runs `uv run --directory … --frozen src/server.py`, with the installed `uv` if there is one; on
  the first launch it installs Python and the dependencies (network required).
- `pack.py` copies the engine into `<MCP>/src/webapp_mcp/`: never edit that copy. It also starts the server to
  copy its actual tool list into `manifest.json`.
- Logs: `~/Library/Logs/Claude/mcp-server-<Display name>.log`, lines `[webapp_mcp]` for the engine.

## Limits

- macOS and Google Chrome only. Brave, Edge and Arc use the same format: they would need their folder and Keychain
  service in `chrome.py`.
- Sites behind an anti-bot challenge (captcha, Cloudflare waiting page): out of scope. `probe.py --compare` detects
  it (403 everywhere, even when impersonating Chrome).
- Session cookies without an expiry date: Chrome does not always write them to disk. The manual token is then the
  fallback.
- Internal APIs are undocumented: an MCP breaks when the site changes. Only its adapter needs rework.
- Remote access (VPS): not yet.

## Tests

```bash
cd template/core && uv run --with pytest --with pytest-asyncio --with "mcp>=2.3,<3" --with cryptography --with curl_cffi pytest -q
cd template/harness && uv run --with pytest pytest -q
```

After changing the engine: run its tests, then `pack.py` on each MCP to propagate it.
