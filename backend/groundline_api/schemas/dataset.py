"""Dataset create/read/update payloads (§8)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Key-safe: usable unchanged as an object-key segment, a directory name, and
# the <name> in name@vN (GL-3-15, §6/§7).
DATASET_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,99}$"


class DatasetCreate(BaseModel):
    name: str = Field(pattern=DATASET_NAME_PATTERN)
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
