"""Row create/read/patch payloads (§8).

PATCH carries the expected `rev` via If-Match; a mismatch returns 409 (§4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from groundline_api.models.row import RowStatus


class RowCreate(BaseModel):
    data: dict[str, Any]


class RowPatch(BaseModel):
    """Partial update (§4, §8: status/assignee, GL-2-3).

    Omit a field to leave it unchanged. `data` merges shallowly into the
    existing row. `assignee: null` unassigns the row.
    """

    data: dict[str, Any] | None = None
    status: RowStatus | None = None
    assignee: uuid.UUID | None = None


class RowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    data: dict[str, Any]
    status: RowStatus
    assignee: uuid.UUID | None
    rev: int
    updated_by: uuid.UUID | None
    updated_at: datetime
    created_at: datetime


class RowEditRead(BaseModel):
    """One `row_edits` record (§3, GL-2-4). Append-only audit history."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    row_id: uuid.UUID
    field: str
    old_value: Any
    new_value: Any
    user_id: uuid.UUID | None
    at: datetime

# TODO GL-1-5+: RowsFromTraces


class RowsDelete(BaseModel):
    """Bulk soft delete (GL-3.5-18): exactly one of `ids` or `keys`."""

    ids: list[uuid.UUID] | None = None
    keys: list[str] | None = None

    @model_validator(mode="after")
    def _one_selector(self) -> RowsDelete:
        if (self.ids is None) == (self.keys is None):
            raise ValueError("give exactly one of 'ids' or 'keys'")
        return self
