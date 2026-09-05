"""Auth routes: interim dev login (GL-1-9) and PAT management (GL-1-10, §5).

    POST   /auth/login         dev-only: {email} -> signed session cookie
    GET    /auth/me            current user + role
    POST   /auth/logout        clear the session
    POST   /auth/tokens        issue a personal access token
    GET    /auth/tokens        list caller's tokens (no secrets)
    DELETE /auth/tokens/{id}   revoke

Entra OIDC login/callback replaces the dev login in GL-3-11.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.auth.sessions import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    create_session,
)
from groundline_api.auth.tokens import issue_token
from groundline_api.config import settings
from groundline_api.deps import get_current_user, get_db
from groundline_api.models.user import PersonalAccessToken, User
from groundline_api.schemas.user import EMAIL_PATTERN, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


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
