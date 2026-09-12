"""Dataset create/read/update payloads (§8)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    # Optional feature scope, default unscoped (§11 Q3).
    feature_id: str | None = Field(default=None, max_length=255)


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    feature_id: str | None
    created_by: uuid.UUID | None
    created_at: datetime
