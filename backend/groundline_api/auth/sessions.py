"""HMAC-signed session cookies for the interim dev login (GL-1-9, §5).

Cookie value is `<user_id>.<expires_epoch>.<hmac_sha256_hex>` signed with
`settings.app_secret_key`. Stdlib-only; replaced by Entra OIDC in GL-3-11.
"""

from __future__ import annotations

import hashlib
import hmac
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
