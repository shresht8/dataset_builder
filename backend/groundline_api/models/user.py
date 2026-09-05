"""`users` and `personal_access_tokens` tables (§5).

Users are JIT-provisioned on first OIDC login with a role mapped from their
Entra group claims. PATs are scoped, expiring, revocable, and inherit the
issuing user's permissions.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from groundline_api.db import Base


class Role(str, enum.Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    ANNOTATOR = "annotator"
    VIEWER = "viewer"


# Native Postgres enum over the four §5 roles. Named `user_role` to avoid the
# `ROLE` keyword.
role_enum = sa.Enum(
    Role,
    name="user_role",
    values_callable=lambda enum: [member.value for member in enum],
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        role_enum, nullable=False, server_default=Role.VIEWER.value
    )
    # Entra object id, set on JIT provisioning; null for locally created users.
    entra_oid: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    # Deactivation flag (GL-1-8 PATCHes this); inactive users cannot sign in.
    active: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, server_default=sa.true()
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    # Only the hash is stored; unique so token lookup is an index hit.
    token_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True)
    # JSON list of scope strings, e.g. ["datasets:read"].
    scopes: Mapped[list] = mapped_column(
        JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
