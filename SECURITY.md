# Security

## What runs where

- **Chrome AI** is Google Chrome started with its own profile folder (`~/.chrome-ai-profile`) and the DevTools
  debugging port open on `127.0.0.1:9333` only. Any program running on your Mac under your user can drive that window
  while it is open, including your signed-in sessions in it. Close it with `chrome-ai stop` when you do not need it,
  and only sign in there to the sites you want Claude to use.
- **MCP servers** built with this template run locally, started by Claude Desktop or Claude Code. They read the
  cookies they declare (and only those) from the Chrome AI profile on disk, decrypt them with the "Chrome Safe
  Storage" key from the macOS Keychain, and send them only to the single host declared as `base_url`.
- Your regular Chrome profiles are never read unless you set `WEBAPP_CHROME=chrome` for an MCP.

## What is never stored or shown

- Cookie and token values are never printed, logged, written to files or sent to Claude by the engine or the
  discovery harness; tools show cookie names only. Network captures mask `Authorization`, `Cookie`, CSRF and
  token-like parameters.
- The Keychain key stays in the MCP server's memory.

## The Keychain prompt

The first time an MCP reads Chrome's cookies, macOS asks to allow access to "Chrome Safe Storage". "Allow" grants it
once. "Always Allow" removes the prompt but also lets any program that goes through `/usr/bin/security` decrypt your
Chrome cookies.

## Reporting a vulnerability

Open a private security advisory on the GitHub repository (Security tab → "Report a vulnerability").
