"""Row create/read/patch payloads (§8).

PATCH carries the expected `rev` via If-Match; a mismatch returns 409 (§4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class RowCreate(BaseModel):
    data: dict[str, Any]


class RowPatch(BaseModel):
    """Partial update: only the given keys of `data` change (§4)."""

    data: dict[str, Any]


class RowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    data: dict[str, Any]
    rev: int
    updated_by: uuid.UUID | None
    updated_at: datetime
    created_at: datetime

# TODO GL-1-5+: RowsFromTraces
