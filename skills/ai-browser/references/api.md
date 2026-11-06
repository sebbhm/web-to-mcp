# dev-browser 1.x API (Puppeteer)

Checked against dev-browser 1.0.0. For the full, current text run `dev-browser help <topic>`
(topics: quickstart, workflow, scripts, pages, snapshot, refs, screenshots, waiting, forms).

A script is the body of one async function run inside a warm daemon with real Puppeteer objects. Top-level `await`
works and the value of the last expression is printed. End lines with semicolons: a line starting with `(` or `[`
continues the previous one.

## Globals

```text
browser                      pre-connected browser handle
console.log/info/warn/error  output (warn/error go to stderr)
saveFile(name, data)         write ~/.dev-browser/v1/tmp/<name>, returns the full path (bare names only)
readFile(name)               read ~/.dev-browser/v1/tmp/<name>
fetch, URL, URLSearchParams, Buffer, TextEncoder/Decoder, atob/btoa, crypto, setTimeout/setInterval, AbortController
```

Not available: `require()`, `import`/`export`, `process`, `fs`, script arguments, `page.waitForTimeout`
(use `await new Promise(r => setTimeout(r, ms))`).

## Browser handle

```text
browser.getPage(name)        named tab, created if new, kept across runs
browser.getPage(targetId)    an existing tab by its id from listPages()
browser.newPage()            anonymous tab, NOT closed automatically: close it yourself
browser.listPages()          every tab: [{ id, name, url, title }]
browser.closePage(name)      close and forget a named page
```

## Reading the page and acting (refs)

```javascript
const page = await browser.getPage("task-name");
console.log(await page.snapshot({ interactive: true }));   // ARIA YAML with [ref=eN] on elements
await page.click("ref/e12");                               // act by ref
await page.fill("ref/e7", "text");                         // clear + type (React-safe)
const el = await page.ref("e12");                          // ElementHandle when you need one
const t = await page.snapshot({ interactive: true, track: "main" });
console.log(t.incremental);                                // after acting: only what changed
```

Snapshot options: `{ interactive, scope: "e12" | "css", depth, track, boxes, urls, maxChars }`. Refs reset after a
navigation or reload: take a new snapshot.

## Page methods used most

```text
Navigation   page.goto(url) (waits for domcontentloaded), page.goBack(), page.reload()
Actions      page.click(sel), page.fill(sel, value), page.type(sel, text), page.select(sel, value),
             page.hover(sel), page.keyboard.press("Enter"), (await page.ref("e5")).uploadFile("/abs/path")
Reading      page.title(), page.url(), page.$eval(sel, el => el.textContent), page.$$eval(sel, fn),
             page.evaluate(() => ...) with plain browser JS, values passed as arguments
Waiting      page.waitForLoad() (never throws), page.waitForSelector(sel, { visible: true, timeout: 5000 }),
             Promise.all([page.waitForNavigation(), page.click(sel)]), page.waitForNetworkIdle()
Screenshots  await page.shot() -> { path, width, height, scale }
             options: { name: "x.jpg" | "x.png", fullPage, clip: { x, y, width, height } }
Network      page.on("request", fn), page.on("response", fn), req.resourceType(), req.postData(), resp.text()
```

Selectors are CSS selectors or `ref/eN`. There is no `getByRole`, `getByText`, `getByLabel`, `getByPlaceholder` or
`snapshotForAI` (those belong to an older Playwright-based version). Clicks do not wait for the element: use
`waitForSelector` first when it may not be there yet. Key chords go one key at a time:
`keyboard.down("Meta")`, `press("a")`, `up("Meta")`. Alerts and confirms are dismissed automatically.

## CLI

```text
dev-browser --connect http://127.0.0.1:9333 <<'JS' ... JS   run against Chrome AI (always)
dev-browser --connect http://127.0.0.1:9333 run script.js   run a script file (prefer this for long scripts)
dev-browser --connect http://127.0.0.1:9333 -e 'code'       run inline code
dev-browser --headless <<'JS' ... JS                        throwaway signed-out browser
dev-browser pages | status | stop                           list tabs, inspect or stop the daemon
dev-browser help <topic>                                    built-in documentation
chrome-ai start | show [url] | hide | status | stop | app   background start, visible window, back, state, quit
```
