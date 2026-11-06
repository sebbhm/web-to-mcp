"""Shared engine for MCP servers that wrap web apps without a public API: auth through the Chrome AI (or Chrome)
session, an HTTP client that impersonates Chrome, and the MCP server. Copied into each MCP by template/tools/pack.py:
do not edit the copy, edit template/core."""

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .app import WebApp
from .chrome import CHROME_AI_DEFAULT_DIR, CHROME_AI_DEFAULT_PORT, ChromeCookies, Cookie, KeychainError, resolve_user_data_dir
from .client import ApiError, WebClient, bearer_auth, cookie_auth, http_auth_error
from .fmt import prune, to_text
from .jwt import find_jwt, jwt_expiry
from .session import BrowserSession, Credentials, LoginRequired

__all__ = [
    "CHROME_AI_DEFAULT_DIR",
    "CHROME_AI_DEFAULT_PORT",
    "ApiError",
    "BrowserSession",
    "ChromeCookies",
    "Cookie",
    "Credentials",
    "KeychainError",
    "LoginRequired",
    "ToolAnnotations",
    "ToolError",
    "WebApp",
    "WebClient",
    "bearer_auth",
    "cookie_auth",
    "find_jwt",
    "http_auth_error",
    "jwt_expiry",
    "prune",
    "resolve_user_data_dir",
    "to_text",
]
