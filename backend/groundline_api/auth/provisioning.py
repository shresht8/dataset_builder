"""JIT provisioning on SSO login (§5, GL-3-11).

Finds the user by Entra `oid`, else links an existing local user by email,
else creates one. The role is re-synced from `groups` on every SSO login.
SCIM is deferred until a customer needs deprovisioning.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.auth.oidc import OIDCError
from groundline_api.auth.roles import role_for_groups
from groundline_api.models.user import User

log = logging.getLogger(__name__)


def _groups(claims: dict) -> list[str]:
    if "groups" in claims:
        return list(claims["groups"])
    # Groups overage: Entra omits `groups` (>200 memberships) and points to
    # Graph via _claim_names. No Graph lookup in the MVP -> default role.
    if "groups" in (claims.get("_claim_names") or {}):
        log.warning("groups overage for oid=%s: using the default role", claims.get("oid"))
    return []


def provision_user(db: Session, claims: dict) -> User:
    oid = claims.get("oid")
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not oid or not email:
        raise OIDCError("ID token has no oid or email/preferred_username claim")
    role = role_for_groups(_groups(claims))

    user = db.scalar(select(User).where(User.entra_oid == oid))
    if user is None:
        user = db.scalar(select(User).where(User.email == email))
        if user is not None:
            if user.entra_oid is not None:
                # Same email, different Entra identity: never re-link silently.
                raise OIDCError("account is linked to a different identity")
            user.entra_oid = oid
        else:
            user = User(
                email=email,
                display_name=(claims.get("name") or email)[:255],
                entra_oid=oid,
                role=role,
                active=True,  # server default only applies at flush
            )
            db.add(user)
    if not user.active:
        raise OIDCError("unknown or inactive user")
    user.role = role
    db.commit()
    db.refresh(user)
    return user
