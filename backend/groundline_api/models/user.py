"""`users` and `personal_access_tokens` tables (§5).

Users are JIT-provisioned on first OIDC login with a role mapped from their
Entra group claims. PATs are scoped, expiring, revocable, and inherit the
issuing user's permissions.
"""

from __future__ import annotations

from groundline_api.db import Base


class User(Base):
    __tablename__ = "users"
    # TODO: id, email, display_name, role, entra_oid, created_at


class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"
    # TODO: id, user_id, name, token_hash, scopes, expires_at, revoked_at
