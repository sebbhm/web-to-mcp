"""HTTP client: impersonates Chrome (TLS fingerprint and headers), injects the session, renews it if the site rejects it."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from curl_cffi import AsyncSession, Response
from curl_cffi.requests.exceptions import RequestException
from mcp.server.mcpserver.exceptions import ToolError

from .jwt import find_jwt
from .session import BrowserSession, Credentials

log = logging.getLogger("webapp_mcp")

RETRY_STATUSES = {429, 502, 503, 504}

# auth(cookie values) → {"headers": {...}} and/or {"cookies": {...}} to add to the request
Auth = Callable[[dict[str, str]], dict[str, dict[str, str]]]


class ApiError(ToolError):
    """Unusable response from the site (HTTP status, unexpected content)."""


def cookie_auth(values: dict[str, str]) -> dict[str, dict[str, str]]:
    """Default auth: sends the cookies back as they are, like the browser does."""
    return {"cookies": values}


def bearer_auth(cookie: str) -> Auth:
    """"Bearer" auth: the token (often a JWT, sometimes prefixed with "Bearer") is read from a cookie and sent
    in the Authorization header, as some sites' own code does (CoinMarketCap…)."""

    def auth(values: dict[str, str]) -> dict[str, dict[str, str]]:
        token = values[cookie]
        return {"headers": {"authorization": f"Bearer {find_jwt(token) or token}"}}

    return auth


def http_auth_error(response: Response) -> bool:
    return response.status_code in (401, 403)


class WebClient:
    def __init__(
        self,
        session: BrowserSession,
        *,
        base_url: str,
        auth: Auth = cookie_auth,
        is_auth_error: Callable[[Response], bool] = http_auth_error,
        headers: dict[str, str] | None = None,
        impersonate: str = "chrome",
        timeout: float = 30,
    ):
        self.session = session
        self.base_url = base_url.rstrip("/") + "/"
        self.host = urlsplit(self.base_url).hostname
        self.auth = auth
        self.is_auth_error = is_auth_error
        self.headers = headers or {}
        self.impersonate = impersonate
        self.timeout = timeout
        self._http: AsyncSession | None = None

    def url(self, path: str) -> str:
        url = urljoin(self.base_url, path.lstrip("/")) if not path.startswith("http") else path
        if urlsplit(url).hostname != self.host:
            raise ToolError(f"Host not allowed: {urlsplit(url).hostname} (expected: {self.host})")
        return url

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Sends the request with the current session. If the site rejects it, looks for a fresh session
        (Chrome AI or Chrome, then the login page) and retries once."""
        url = self.url(path)
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        for attempt in (1, 2):
            creds = await self.session.credentials()
            response = await self._send(method, url, creds, params, json, headers)
            if not self.is_auth_error(response):
                return response
            self.session.reject(creds)
        raise ApiError(
            f"{self.session.app_name} also rejects the new session ({creds.source}). "
            f"Sign out of the site and back in, in {self.session.browser}, then retry."
        )

    async def request_json(self, method: str, path: str, **kwargs) -> Any:
        """Like request, but returns the decoded JSON. Raises a readable ApiError if the HTTP status is not 2xx
        or if the response is not JSON."""
        response = await self.request(method, path, **kwargs)
        if not 200 <= response.status_code < 300:
            raise ApiError(f"{method} {path} → HTTP {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError:
            raise ApiError(f"{method} {path} → non-JSON response (HTTP {response.status_code}): {response.text[:300]}") from None

    async def _send(self, method, url, creds: Credentials, params, json, headers) -> Response:
        auth = self.auth(creds.values)
        for attempt in range(3):
            try:
                response = await self._client().request(
                    method,
                    url,
                    params=params or None,
                    json=json,
                    headers={**self.headers, **auth.get("headers", {}), **(headers or {})},
                    cookies=auth.get("cookies"),
                    timeout=self.timeout,
                )
            except RequestException as error:
                if attempt == 2:
                    raise ApiError(f"{method} {urlsplit(url).path}: network failure ({error})") from error
            else:
                if response.status_code not in RETRY_STATUSES or attempt == 2:
                    return response
                log.warning("%s %s → HTTP %s, retrying", method, urlsplit(url).path, response.status_code)
            await asyncio.sleep(2**attempt)
        raise AssertionError("unreachable")

    def _client(self) -> AsyncSession:
        # impersonate's default headers matter: without them, some sites (CMC) return 403.
        if self._http is None:
            self._http = AsyncSession(impersonate=self.impersonate)
        return self._http
