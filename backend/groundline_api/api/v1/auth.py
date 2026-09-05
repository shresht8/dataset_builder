"""OIDC login/callback and personal-access-token management (§5).

    GET  /auth/login       start Entra OIDC Authorization Code + PKCE flow
    GET  /auth/callback    exchange code, JIT-provision user, map role
    POST /auth/tokens      issue a personal access token
    GET  /auth/tokens      list caller's tokens
    DELETE /auth/tokens/{id}   revoke
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])

# TODO: login, callback, issue_token, list_tokens, revoke_token
