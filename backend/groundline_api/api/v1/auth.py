"""Auth routes: interim dev login (GL-1-9) and PAT management (GL-1-10, §5).

    POST   /auth/login         dev-only: {email} -> signed session cookie
    GET    /auth/config        which sign-in options to show (GL-3-11)
    GET    /auth/sso/login     Entra OIDC: redirect to the IdP (PKCE)
    GET    /auth/sso/callback  Entra OIDC: validate, JIT-provision, session
    GET    /auth/me            current user + role
    POST   /auth/logout        clear the session
    POST   /auth/tokens        issue a personal access token
    GET    /auth/tokens        list caller's tokens (no secrets)
    DELETE /auth/tokens/{id}   revoke

Entra SSO (GL-3-11) runs alongside dev login; GL-3-14 switches dev login off.
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.auth import oidc
from groundline_api.auth.provisioning import provision_user
from groundline_api.auth.sessions import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    create_session,
    read_signed_data,
    sign_data,
)
from groundline_api.auth.tokens import issue_token
from groundline_api.config import settings
from groundline_api.deps import get_current_user, get_db
from groundline_api.models.user import PersonalAccessToken, User
from groundline_api.schemas.user import EMAIL_PATTERN, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

# Short-lived signed cookie carrying state, nonce and the PKCE verifier across
# the round trip to the IdP (GL-3-11).
SSO_FLOW_COOKIE = "groundline_sso_flow"
SSO_FLOW_TTL_SECONDS = 600


class LoginRequest(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)


@router.post("/login", response_model=UserRead)
def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """DEV ONLY — no credential is checked. Gated by AUTH_DEV_LOGIN."""
    if not settings.auth_dev_login:
        raise HTTPException(status_code=403, detail="dev login is disabled")
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="unknown or inactive user")
    response.set_cookie(
        SESSION_COOKIE,
        create_session(user.id),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return user


@router.get("/config")
def auth_config() -> dict:
    """Which sign-in options the login page should offer (GL-3-11)."""
    return {"dev_login": settings.auth_dev_login, "sso": settings.auth_sso_enabled}


def _require_sso() -> None:
    if not settings.auth_sso_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/sso/login")
def sso_login() -> RedirectResponse:
    """Start Entra sign-in: redirect to the IdP with PKCE, state and nonce."""
    _require_sso()
    flow = {
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        "verifier": secrets.token_urlsafe(64),
    }
    try:
        url = oidc.build_authorization_url(flow["state"], flow["nonce"], flow["verifier"])
    except oidc.OIDCError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        SSO_FLOW_COOKIE,
        sign_data(flow, SSO_FLOW_TTL_SECONDS),
        max_age=SSO_FLOW_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/v1/auth/sso",
    )
    return response


@router.get("/sso/callback")
def sso_callback(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """Finish Entra sign-in: check state, redeem the code, validate the ID
    token, JIT-provision, then issue the existing session cookie (GL-1-9)."""
    _require_sso()
    flow = read_signed_data(request.cookies.get(SSO_FLOW_COOKIE, ""))
    if flow is None or not state or not hmac.compare_digest(state, flow["state"]):
        raise HTTPException(status_code=401, detail="sign-in state mismatch")
    if not code:
        raise HTTPException(status_code=401, detail="sign-in failed: no authorization code")
    try:
        disc, id_token = oidc.exchange_code(code, flow["verifier"])
        claims = oidc.validate_id_token(disc, id_token, flow["nonce"])
        user = provision_user(db, claims)
    except oidc.OIDCError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        create_session(user.id),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(SSO_FLOW_COOKIE, path="/v1/auth/sso")
    return response


@router.get("/me", response_model=UserRead)
def me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Default TTL is settings.pat_default_ttl_days (90, §11 Q5).
    ttl_days: int | None = Field(default=None, ge=1, le=3650)


class TokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    expires_at: datetime
    revoked_at: datetime | None


class TokenCreated(TokenRead):
    token: str  # the raw secret, shown exactly once


@router.post("/tokens", response_model=TokenCreated, status_code=201)
def create_token(
    payload: TokenCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> TokenCreated:
    raw, record = issue_token(db, user, payload.name, payload.ttl_days)
    return TokenCreated(
        id=record.id,
        name=record.name,
        expires_at=record.expires_at,
        revoked_at=record.revoked_at,
        token=raw,
    )


@router.get("/tokens", response_model=list[TokenRead])
def list_tokens(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[PersonalAccessToken]:
    return list(
        db.scalars(
            select(PersonalAccessToken)
            .where(PersonalAccessToken.user_id == user.id)
            .order_by(PersonalAccessToken.expires_at)
        )
    )


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(
    token_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    record = db.get(PersonalAccessToken, token_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status_code=404, detail="token not found")
    if record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        db.commit()
