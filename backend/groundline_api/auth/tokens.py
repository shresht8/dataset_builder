"""Personal access tokens for machine access (§5).

Scoped to read datasets, expiring (default 90 days), revocable, inheriting the
issuing user's permissions. CI uses a service-account token.
"""

from __future__ import annotations

# TODO: issue_token(user, name, ttl_days) -> (raw_token, record)
# TODO: verify_token(raw_token) -> User | None
