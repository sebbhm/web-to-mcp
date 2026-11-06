---
name: setup-ai-browser
description: Set up Chrome AI, a dedicated Chrome window for the agent with its own profile where the user signs in once, plus the dev-browser CLI that drives it. Use when the user wants to set up the AI browser, Chrome AI, dev-browser, or before building an MCP with web-to-mcp on a new machine.
argument-hint: "[port, default 9333]"
disable-model-invocation: true
allowed-tools:
  - Bash(dev-browser *)
  - Bash(chrome-ai *)
  - Bash(~/.local/bin/chrome-ai *)
---

Give the agent its own Chrome: a separate window with its own profile, where the user signs in once to the sites the
agent will use. The user's normal Chrome and its profiles are never touched or read. The agent drives that window with
the `dev-browser` CLI, and MCP servers built with `/web-to-mcp:web-to-mcp` read their session from it.

Do every step yourself and stop only for what needs a human: installing software that asks for a password, and
signing in. Talk to the user in their language, in short messages, without jargon (say "the AI's Chrome window",
not "CDP" or "user-data-dir").

The setup lives in four places:
- the launcher `~/.local/bin/chrome-ai`, copied from `${CLAUDE_SKILL_DIR}/scripts/chrome-ai`;
- the profile folder `~/.chrome-ai-profile`;
- the debugging port `9333`, open on `127.0.0.1` only;
- the `ai-browser` skill of this plugin, which tells agents to browse there.

If the argument gives another port, add `export CHROME_AI_PORT=<port>` once to `~/.zshrc` and `~/.zprofile` and use
that port everywhere below.

## 0. Started from a link?

This skill may have been started from a link (for example "Run https://github.com/sebbhm/web-to-mcp/tree/main/skills/setup-ai-browser"
pasted in the Code tab of the Claude desktop app) instead of the installed plugin. Then `${CLAUDE_SKILL_DIR}` is not a
real folder: install nothing else from the repository, and download the launcher only when step 3 needs it:

```bash
curl -fsSL https://raw.githubusercontent.com/sebbhm/web-to-mcp/main/skills/setup-ai-browser/scripts/chrome-ai -o /tmp/chrome-ai
```

Then install the plugin, so the `ai-browser` and `web-to-mcp` skills exist in the user's next conversations:

```bash
claude plugin marketplace add sebbhm/web-to-mcp && claude plugin install web-to-mcp@web-to-mcp
```

If `claude` is not found (the desktop app alone may not put it on the PATH), check `~/.local/bin/claude`. If it is
missing too, tell the user you are installing the Claude Code command line with Anthropic's official installer (no
password, into `~/.local/bin`), run `curl -fsSL https://claude.ai/install.sh | bash`, then run the two commands above
with `~/.local/bin/claude`. The terminal and the desktop app share the same settings, so the plugin also appears in
the app (`+` next to the prompt box, then Plugins, to manage it). Either way, continue with the setup: it does not
need the plugin. The new skills are available in a new conversation (or after `/reload-plugins`).

## 1. Check the machine

macOS only. On Windows or Linux, stop and say so.

```bash
sw_vers -productVersion; node --version; npm --version; uv --version
ls -d "/Applications/Google Chrome.app" "$HOME/Applications/Google Chrome.app" 2>/dev/null
```

- Node missing: ask the user to install the LTS from https://nodejs.org (or `brew install node` if they use
  Homebrew), then continue.
- uv missing: run `curl -LsSf https://astral.sh/uv/install.sh | sh` (it installs to `~/.local/bin`, no password).
- Chrome missing: send the user to https://www.google.com/chrome and wait for "done".

## 2. Install dev-browser

Pin the version this plugin was tested with: later versions may change the API.

```bash
npm install -g dev-browser@1.0.0 && dev-browser install && dev-browser --version
```

`dev-browser install` reuses the installed Chrome. If `npm install -g` fails with a permission error, do not use
sudo: tell the user their Node install needs a user-owned prefix (`npm config set prefix ~/.npm-global` and add
`~/.npm-global/bin` to PATH), do it with their agreement, and retry.

## 3. Install the launcher

```bash
mkdir -p ~/.local/bin && cp "${CLAUDE_SKILL_DIR}/scripts/chrome-ai" ~/.local/bin/chrome-ai && chmod +x ~/.local/bin/chrome-ai
# Started from a link (step 0): copy /tmp/chrome-ai instead of "${CLAUDE_SKILL_DIR}/scripts/chrome-ai".
grep -l '.local/bin' ~/.zshenv ~/.zprofile ~/.zshrc ~/.bash_profile ~/.bashrc 2>/dev/null
```

If no file mentions `.local/bin`, add `export PATH="$HOME/.local/bin:$PATH"` once (never twice) to `~/.zprofile`
and `~/.zshrc` (bash: `~/.bash_profile` and `~/.bashrc`). Then create the Spotlight app and start the window:

```bash
~/.local/bin/chrome-ai app
~/.local/bin/chrome-ai show
```

Chrome AI normally runs in the background, with no window: `chrome-ai start` (what agents use) never shows anything
on screen. `show` makes it a visible window, for signing in or watching; `hide` sends it back. Tell the user this
window is the AI's Chrome, that it will usually stay invisible and reappear only when a site needs them to sign in,
and suggest picking a different theme color in it (Settings, Appearance) so they never confuse it with their own.

## 4. The user signs in

Ask the user to sign in, in that window only, to the sites the agent will work with (Google, their CRM, their
dashboards, their own apps), then to say "done". Never type a password, a one-time code or a card number yourself,
and never copy cookies from their normal Chrome.

## 5. Permissions (the user does this)

A plugin cannot change the user's permission settings, and you must not edit them yourself. Each skill of this plugin
pre-approves `dev-browser` and `chrome-ai` while it runs. To stop being asked in other conversations too, the user can
add these two rules to `permissions.allow` in `~/.claude/settings.json` (or with `/permissions` in a terminal
session):

```json
"Bash(dev-browser *)", "Bash(chrome-ai *)"
```

Show them this and move on; do not wait.

## 6. Prove it works

Ask which site they signed in to, then open it in a named page, take a screenshot and look at it:

```bash
~/.local/bin/chrome-ai hide >/dev/null && dev-browser --connect http://127.0.0.1:9333 <<'JS'
const page = await browser.getPage("setup-check");
await page.goto("https://github.com");
const shot = await page.shot({ name: "setup-check.jpg" });
console.log(JSON.stringify({ title: await page.title(), url: page.url(), screenshot: shot.path }));
JS
```

This runs in the background (nothing on screen): it also proves the sign-ins work without a window. Replace the URL
with a site the user signed in to. Open the screenshot and check the user appears signed in. Then restart Chrome AI
and check again, so the login is proven to survive a restart:

```bash
~/.local/bin/chrome-ai stop; sleep 1; ~/.local/bin/chrome-ai start
```

Finish by closing the check page and the window, so nothing stays open (the sign-ins are saved):

```bash
dev-browser --connect http://127.0.0.1:9333 -e 'await browser.closePage("setup-check")'; ~/.local/bin/chrome-ai stop
```

## 7. Report

End with a short checklist, a check or a cross on each line: Chrome found, dev-browser 1.0.0 installed, launcher
works, Chrome AI app created, user signed in, permissions explained, screenshot shows a signed-in page, login
survives a restart, works in the background. Then three lines for the user: Chrome AI runs invisibly when the agent
needs it and shows up only when a site asks them to sign in; to sign in to a new site, open "Chrome AI" from
Spotlight (or `chrome-ai show`); and one thing to try now, such as "open my analytics
dashboard and tell me yesterday's visits" or "/web-to-mcp:web-to-mcp to turn a site into an MCP".
