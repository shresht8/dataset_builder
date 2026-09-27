"""HMAC-signed session cookies for the interim dev login (GL-1-9, §5).

Cookie value is `<user_id>.<expires_epoch>.<hmac_sha256_hex>` signed with
`settings.app_secret_key`. Stdlib-only; replaced by Entra OIDC in GL-3-11.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from groundline_api.config import settings

SESSION_COOKIE = "groundline_session"
SESSION_TTL_SECONDS = 12 * 3600


def _signature(payload: str) -> str:
    return hmac.new(
        settings.app_secret_key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def create_session(user_id: uuid.UUID) -> str:
    payload = f"{user_id}.{int(time.time()) + SESSION_TTL_SECONDS}"
    return f"{payload}.{_signature(payload)}"


def sign_data(data: dict, ttl_seconds: int) -> str:
    """Signed, expiring cookie value for small JSON data (GL-3-11 SSO state)."""
    body = base64.urlsafe_b64encode(
        json.dumps({**data, "exp": int(time.time()) + ttl_seconds}).encode()
    ).decode()
    return f"{body}.{_signature(body)}"


def read_signed_data(value: str) -> dict | None:
    """The data from `sign_data`, or None if tampered, malformed or expired."""
    body, _, signature = value.rpartition(".")
    if not body or not hmac.compare_digest(signature, _signature(body)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    return data


def resolve_session(value: str) -> uuid.UUID | None:
    """Return the user id for a valid, unexpired session value, else None."""
    try:
        user_part, expires_part, signature = value.split(".")
    except ValueError:
        return None
    payload = f"{user_part}.{expires_part}"
    if not hmac.compare_digest(signature, _signature(payload)):
        return None
    try:
        if int(expires_part) < time.time():
            return None
        return uuid.UUID(user_part)
    except ValueError:
        return None
