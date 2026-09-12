"""Shared FastAPI dependencies: DB session, current-user resolution, RBAC.

`get_current_user` accepts either a signed session cookie (interactive dev
login, GL-1-9) or a bearer personal access token (machine access, GL-1-10) and
yields the resolved active user. `require_role` enforces the §5 capability
table: viewer < annotator < editor < admin.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from groundline_api.auth.sessions import SESSION_COOKIE, resolve_session
from groundline_api.auth.tokens import verify_token
from groundline_api.db import SessionLocal
from groundline_api.models.user import Role, User

_ROLE_RANK = {Role.VIEWER: 0, Role.ANNOTATOR: 1, Role.EDITOR: 2, Role.ADMIN: 3}


def get_db() -> Iterator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> User:
    """Resolve the caller from a session cookie or a bearer PAT; 401 otherwise."""
    user: User | None = None

    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        user = verify_token(db, authorization.removeprefix("Bearer ").strip())
    else:
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie:
            user_id = resolve_session(cookie)
            if user_id is not None:
                user = db.get(User, user_id)

    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def require_role(minimum: Role) -> Callable[..., User]:
    """Dependency factory: caller must hold at least `minimum` role (§5)."""

    def dependency(
        user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if _ROLE_RANK[user.role] < _ROLE_RANK[minimum]:
            raise HTTPException(
                status_code=403, detail=f"requires {minimum.value} role"
            )
        return user

    return dependency
