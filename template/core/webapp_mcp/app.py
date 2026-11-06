"""WebApp: ties together the session, the HTTP client and the MCP server. It is the only object an adapter handles."""

from __future__ import annotations

import functools
import inspect
import logging
import sys
import typing

from curl_cffi import Response
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .client import Auth, WebClient, cookie_auth, http_auth_error
from .fmt import to_text
from .session import BrowserSession

log = logging.getLogger("webapp_mcp")


class WebApp:
    """A web app exposed as an MCP server.

    Minimal example:

        app = WebApp("example", domain="example.com", cookies=["session"],
                     login_url="https://example.com/login", base_url="https://api.example.com",
                     tool_prefix="ex")

        @app.tool()
        async def ex_profile() -> dict:
            \"\"\"Account profile.\"\"\"
            return (await app.client.request("GET", "/me")).json()

        app.run()

    Cookies are read from Chrome AI by default; `chrome="chrome"` (or WEBAPP_CHROME=chrome) reads the
    regular Chrome profiles instead. None follows WEBAPP_CHROME.
    """

    def __init__(
        self,
        name: str,
        *,
        domain: str,
        cookies: list[str],
        login_url: str,
        base_url: str,
        tool_prefix: str,
        auth: Auth = cookie_auth,
        is_auth_error: typing.Callable[[Response], bool] = http_auth_error,
        headers: dict[str, str] | None = None,
        manual_token_env: str | None = None,
        instructions: str | None = None,
        version: str = "",
        chrome: str | None = None,
    ):
        self.session = BrowserSession(
            app_name=name,
            domain=domain,
            cookies=cookies,
            login_url=login_url,
            manual_token_env=manual_token_env,
            chrome_source=chrome,
        )
        self.client = WebClient(self.session, base_url=base_url, auth=auth, is_auth_error=is_auth_error, headers=headers)
        self.server = MCPServer(name, instructions=instructions, version=version)

        @self.tool(
            name=f"{tool_prefix}_session",
            title="Connection status",
            annotations=ToolAnnotations(readOnlyHint=True),
        )
        async def session_status() -> dict:
            """Status of the connection to the site: browser (Chrome AI or Chrome) and profile used, session expiry,
            manual token. Never reveals a cookie value and never opens a page."""
            return self.session.status()

    def tool(self, **kwargs):
        """Declares an MCP tool. The result (dict, list…) is returned as compact JSON; an unexpected exception
        becomes a readable error message instead of being hidden by the SDK."""

        def decorate(fn):
            # Resolve the annotations right away, in the adapter's module: the SDK reads the signature
            # through the wrapper, which cannot see the names the adapter imported.
            fn.__annotations__ = typing.get_type_hints(fn, include_extras=True)

            @functools.wraps(fn)
            async def wrapper(*args, **kw):
                try:
                    result = fn(*args, **kw)
                    if inspect.isawaitable(result):
                        result = await result
                except ToolError:
                    raise
                except Exception as error:
                    log.exception("Error in %s", fn.__name__)
                    raise ToolError(f"{type(error).__name__}: {error}") from error
                return result if isinstance(result, str) else to_text(result)

            self.server.tool(structured_output=False, **kwargs)(wrapper)
            return fn

        return decorate

    def run(self) -> None:
        # The MCP SDK already configures root logging: the engine keeps its own prefix, easy to spot
        # in ~/Library/Logs/Claude/mcp-server-<name>.log.
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("[webapp_mcp] %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
        self.server.run()
