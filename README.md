# web-to-mcp

A Claude Code plugin that does two things:

1. **Gives Claude its own browser.** Chrome AI is a separate Chrome window with its own profile. You sign in there
   once to the sites Claude should use, and Claude drives it with [dev-browser](https://github.com/SawyerHood/dev-browser).
   Your own Chrome and its profiles are never touched.
2. **Turns a web app without an API into an MCP server.** Claude finds where the page gets its data, validates the
   session, writes the tools and packages them as a `.mcpb` extension for Claude Desktop. The extension reuses the
   Chrome AI session: no token to copy, and it picks up a renewed session by itself.

The included example, [`examples/search-console-ai`](examples/search-console-ai), reads Google Search Console's
generative AI performance report, which the official API does not expose.

## Requirements

- macOS and Google Chrome.
- [Claude Code](https://code.claude.com) (terminal or the Code tab of the desktop app) to build MCPs.
- Node.js and [uv](https://docs.astral.sh/uv/); the setup skill checks them and installs uv if needed.

Claude Desktop and Cowork users who only want a ready-made MCP: download the `.mcpb` from the
[releases](https://github.com/sebbhm/web-to-mcp/releases), double-click it, then sign in to the site in Chrome AI
(see "Chrome AI without Claude Code" below).

## Install

In Claude Code:

```
/plugin marketplace add sebbhm/web-to-mcp
/plugin install web-to-mcp@web-to-mcp
```

Or from a terminal: `claude plugin marketplace add sebbhm/web-to-mcp && claude plugin install web-to-mcp@web-to-mcp`.

Update later with `claude plugin marketplace update web-to-mcp && claude plugin update web-to-mcp@web-to-mcp`.

## Use

| Command | What it does |
|---|---|
| `/web-to-mcp:setup-ai-browser` | One-time setup: installs dev-browser 1.0.0 and the `chrome-ai` launcher, creates the "Chrome AI" app, has you sign in, proves it works. |
| `/web-to-mcp:web-to-mcp <site>` | Builds an MCP for a site: discovery, session check, tools, tests, `.mcpb`. |
| (automatic) `ai-browser` | Whenever you ask Claude to browse, click, fill a form or take a screenshot, it works in Chrome AI. |

Try: "open my Stripe dashboard in Chrome AI and tell me yesterday's revenue", or
"/web-to-mcp:web-to-mcp make an MCP for the reports page of my analytics tool".

### Chrome AI without Claude Code

The launcher is a small shell script, [`skills/setup-ai-browser/scripts/chrome-ai`](skills/setup-ai-browser/scripts/chrome-ai).
Copy it to `~/.local/bin/chrome-ai`, make it executable, run `chrome-ai app` once to get a "Chrome AI" app in
Spotlight, then `chrome-ai start` and sign in to your sites in that window.

## How an MCP finds your session

The engine (`template/core/webapp_mcp`) reads the site's cookies from the Chrome AI profile on disk
(`~/.chrome-ai-profile`), decrypting them with Chrome's key from the macOS Keychain ("Chrome Safe Storage"; macOS asks
once). If the session is missing or rejected, it opens the site's sign-in page in Chrome AI and waits for you.

To read your regular Chrome instead, set `WEBAPP_CHROME=chrome` (and optionally `CHROME_PROFILE`) in the extension's
settings.

## Repository layout

```
.claude-plugin/        plugin and marketplace manifests
skills/
  setup-ai-browser/    one-time setup + the chrome-ai launcher
  ai-browser/          how Claude browses in Chrome AI (dev-browser 1.x API reference)
  web-to-mcp/          the site → MCP workflow
template/              engine, MCP skeleton, discovery harness, build tools (see template/README.md)
examples/              complete MCPs built with the template
```

## Limits

- macOS and Google Chrome only (Brave, Edge and Arc use the same format and could be added).
- Internal APIs and page formats are undocumented: an MCP breaks when the site changes, and only its adapter needs
  fixing.
- Sites behind a strong bot challenge (captcha, waiting room) are out of scope.
- Some sites' terms of use forbid automated access: check before you build or share an MCP.

See [SECURITY.md](SECURITY.md) for what runs where and what is never stored.

## Development

```bash
cd template/core && uv run --with pytest --with pytest-asyncio pytest -q
cd template/harness && uv run --with pytest pytest -q
claude plugin validate .
```

## License

MIT. dev-browser is a separate project by Sawyer Hood, installed from npm.
