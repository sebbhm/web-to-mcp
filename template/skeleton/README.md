# MCP {{DISPLAY_NAME}}

{{DESCRIPTION}}

MCP server built with the web-to-mcp template: the {{DOMAIN}} session is read from Chrome, no token to copy. It
uses the site's internal API: undocumented, it can change without notice.

## Install

1. Sign in on {{LOGIN_URL}} in Chrome AI (the dedicated Chrome instance, started with `chrome-ai start`).
2. Double-click `{{NAME}}.mcpb` (or drag it into Claude Desktop → Settings → Extensions).

If the session expires, the MCP opens the sign-in page in Chrome and waits up to 45 s for you to sign in again;
past that, it asks you to retry the request.

### Which Chrome?

By default the session is read from **Chrome AI** (user data in `$CHROME_AI_PROFILE`, default
`~/.chrome-ai-profile`). To use your regular Chrome instead, set the extension's **Chrome source** field to
`chrome` (env `WEBAPP_CHROME=chrome`), and optionally **Chrome profile** (env `CHROME_PROFILE`) to the profile
where you are signed in; empty means the most recently used profile.

## Tools

| Tool | Purpose |
|---|---|
| `{{PREFIX}}_session` | Connection state: Chrome and profile used, session expiry |

## Technical notes

- Auth: cookie(s) {{COOKIES_TEXT}} of {{DOMAIN}}.
- Endpoints found on {{DATE}}: …
- Pitfalls met: …

## Development

The commands use the tools of the web-to-mcp plugin (`template/tools/` in the plugin folder; in Claude Code, ask
"/web-to-mcp:web-to-mcp" to run them). `smoke.py` finds them by itself, or through `WEB_TO_MCP_TOOLS` after a plugin
update moved them.

```bash
cd {{DIR}}
uv run <web-to-mcp>/template/tools/mcpcall.py .    # list the tools
uv run --no-project python smoke.py                # call each tool
uv run <web-to-mcp>/template/tools/pack.py .       # build {{NAME}}.mcpb
```

`pack.py` copies the template engine into `src/webapp_mcp`: never edit that copy directly, it is replaced at
every package.
