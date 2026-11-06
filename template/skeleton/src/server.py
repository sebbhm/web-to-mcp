"""MCP {{DISPLAY_NAME}}: {{DESCRIPTION}}

Internal API of {{DOMAIN}}, authenticated with the web session read from Chrome (webapp_mcp engine).
Endpoints found on {{DATE}} with the web-to-mcp harness: see README.md.

Session source: by default the cookies are read from Chrome AI, the dedicated Chrome instance
($CHROME_AI_PROFILE, default ~/.chrome-ai-profile). Setting the extension's env WEBAPP_CHROME=chrome
(the "Chrome source" field) reads the regular Chrome instead, and CHROME_PROFILE picks one of its profiles.
"""

from typing import Annotated, Any

from pydantic import Field
from webapp_mcp import ToolAnnotations, ToolError, WebApp, prune

app = WebApp(
    "{{NAME}}",
    domain="{{DOMAIN}}",
    cookies={{COOKIES}},
    login_url="{{LOGIN_URL}}",
    base_url="{{BASE_URL}}",
    tool_prefix="{{PREFIX}}",
    # Depending on what the harness showed:
    # auth=bearer_auth("…"),   the site sends a cookie's token in an Authorization header
    # is_auth_error=…,         the site signals an expired session otherwise than with HTTP 401/403
    #                          (e.g. Google answers HTTP 200 with its sign-in page)
    # headers={…},             headers the site requires (never a secret value here)
    manual_token_env="{{TOKEN_ENV}}",
    version="0.1.0",
)


@app.tool(title="Example", annotations=ToolAnnotations(readOnlyHint=True))
async def {{PREFIX}}_example(
    query: Annotated[str | None, Field(description="What the user is looking for")] = None,
) -> Any:
    """Say in one sentence what the tool returns, from the user's point of view. Then give the units, the
    defaults and the limits (pagination…)."""
    if query == "":
        raise ToolError("Clear message for the user: only ToolError messages reach Claude as they are.")
    data = await app.client.request_json("GET", "/path/found/with/the/harness", params={"q": query})
    return prune(data)


if __name__ == "__main__":
    app.run()
