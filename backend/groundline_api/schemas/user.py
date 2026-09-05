"""User management payloads for /v1/users (GL-1-8, §5 interim local auth)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from groundline_api.models.user import Role

# Light shape check; full RFC validation would need the email-validator package.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserCreate(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    role: Role = Role.VIEWER


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    role: Role | None = None
    active: bool | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    active: bool
    created_at: datetime
