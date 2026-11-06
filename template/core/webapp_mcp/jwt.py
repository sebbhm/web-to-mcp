"""Reading (without signature verification) the JWTs carried by session cookies."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone

_JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")


def find_jwt(value: str) -> str | None:
    """Extracts a JWT from a cookie value ("Bearer<jwt>", "Bearer%20<jwt>", bare JWT…)."""
    match = _JWT.search(value)
    return match.group(0) if match else None


def jwt_expiry(token: str) -> datetime | None:
    """Expiry date (exp claim) of a JWT, or None if it has none or is not a JWT."""
    jwt = find_jwt(token)
    if not jwt:
        return None
    try:
        payload = jwt.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return datetime.fromtimestamp(claims["exp"], timezone.utc) if isinstance(claims.get("exp"), (int, float)) else None
    except (ValueError, KeyError):
        return None
