---
name: ai-browser
description: Drive Chrome AI, the user's dedicated signed-in Chrome window, with the dev-browser CLI to open sites, read pages, click, fill forms, take screenshots and check a live app. Use when the user asks to browse, open a site, click, fill a form, take a screenshot, scrape a page, test their site or check something on the web.
allowed-tools:
  - Bash(dev-browser *)
  - Bash(chrome-ai *)
---

Do browser work in Chrome AI: a dedicated Chrome with its own profile (`~/.chrome-ai-profile`), where the user is
already signed in to their tools. The user's own Chrome is never touched. `chrome-ai start` runs it **in the
background**: no window, no Dock icon, nothing on the user's screen. It becomes a visible window only when a human is
needed (`chrome-ai show`), then goes back (`chrome-ai hide`).

**Leave nothing open.** Before the first browser command of a task, run `chrome-ai status` and remember the answer.
When the task is done (or abandoned), close every page you opened with `browser.closePage(name)`, then:
- if Chrome AI was stopped and you started it, run `chrome-ai stop`;
- if it was already running, the user may be using it: leave it open, with only your pages closed.

Never leave anonymous tabs from `browser.newPage()` open. The sign-ins stay saved after a stop.

Start it (it does nothing when already open), then connect to it on every run:

```bash
chrome-ai status   # once, at the start of the task: running or stopped?
chrome-ai start
dev-browser --connect http://127.0.0.1:9333 <<'JS'
const page = await browser.getPage("example-task");
await page.goto("https://example.com");
console.log(JSON.stringify({ title: await page.title(), url: page.url() }));
JS
```

If `chrome-ai` or `dev-browser` is missing, stop and tell the user to run `/web-to-mcp:setup-ai-browser`. Never fall
back to the user's own Chrome or to a standalone Playwright script. If `CHROME_AI_PORT` is set, connect to that port
instead of 9333 (`chrome-ai url` prints the address).

- Pages are Puppeteer pages (dev-browser 1.x). Open [references/api.md](references/api.md), or run
  `dev-browser help <topic>`, before using a method you are not sure exists.
- Give each task its own page name (`browser.getPage("stripe-sales")`) so parallel agents never share a tab, reuse
  the name across runs to keep the page state, and close it with `browser.closePage(name)` when the task is done.
  Never close or navigate a tab you did not open.
- Write small scripts that do one thing and end by logging the state as JSON (url, title, what you found). For a
  script longer than a few lines, write it to a file and use `dev-browser --connect … run <file>`.
- Before clicking, read the page with `await page.snapshot({ interactive: true })`, then act on the refs it prints:
  `page.click("ref/e12")`, `page.fill("ref/e7", "text")`.
- Take screenshots with `await page.shot()` (it returns `{ path }` under `~/.dev-browser/v1/tmp/`) and look at the
  image before saying a page looks right. Other files go there too, through `saveFile(name, data)`.
- If a site shows its sign-in page (or the user is signed out), run `chrome-ai show <that page's url>`: Chrome AI
  becomes a visible window on that page. Ask the user to sign in there and say "done", then run `chrome-ai hide` to
  send it back to the background, and continue: the session stays saved. When the user wants to watch what you do,
  `chrome-ai show` too, and `chrome-ai hide` when they are done. Never type passwords, one-time codes or card numbers, and never solve a captcha.
- Ask the user before any click that sends, publishes, pays, deletes or accepts terms.
- For a signed-out check (how a visitor sees a site), run without `--connect` and with `--headless`, and say so.
- When a script fails, the page stays where it was: reconnect, take a screenshot, log the url and title, and
  continue from there.

Reply with what you saw or did, the final URL, and the screenshot path when it helps, after closing your pages
(and Chrome AI if you started it).
