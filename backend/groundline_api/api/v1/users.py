"""Local user management routes (GL-1-8, §5 interim). Admin-only.

    POST  /users            create (email, display_name?, role)
    GET   /users            list
    PATCH /users/{id}       change role / display_name / active
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.deps import get_db, require_role
from groundline_api.models.user import Role, User
from groundline_api.schemas.user import UserCreate, UserPatch, UserRead

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require_role(Role.ADMIN))],
)


@router.get("", response_model=list[UserRead])
def list_users(db: Annotated[Session, Depends(get_db)]) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


@router.post("", response_model=UserRead, status_code=201)
def create_user(
    payload: UserCreate, db: Annotated[Session, Depends(get_db)]
) -> User:
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="email already exists")
    user = User(
        email=email,
        display_name=payload.display_name or email,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserRead)
def patch_user(
    user_id: uuid.UUID,
    payload: UserPatch,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    # exclude_none: no field here may be nulled (display_name is NOT NULL).
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in updates.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user
