"""Row comment payloads (§4, §8, GL-2-4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CommentCreate(BaseModel):
    body: str = Field(min_length=1)


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    row_id: uuid.UUID
    user_id: uuid.UUID | None
    body: str
    created_at: datetime
