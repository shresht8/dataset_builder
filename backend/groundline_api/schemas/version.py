"""Version cut/list/diff payloads (§8).

Reuses `groundline_schema.Manifest` for snapshot metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VersionCut(BaseModel):
    notes: str | None = None
    include_unapproved: bool = False


class VersionRead(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    version: int
    content_hash: str  # "sha256:<hex>" (C2), unlike the DB column
    row_count: int
    notes: str | None
    created_by: uuid.UUID | None
    # UI can't resolve user ids: GET /v1/users is admin-only (C3).
    created_by_email: str | None
    created_at: datetime


class DiffRow(BaseModel):
    id: uuid.UUID
    status: str
    data: dict[str, Any]


class StatusChange(BaseModel):
    old: str
    new: str


class FieldChange(BaseModel):
    field: str
    old: Any
    new: Any


class ModifiedRow(BaseModel):
    id: uuid.UUID
    status: StatusChange | None
    changes: list[FieldChange]


class SchemaChanges(BaseModel):
    added: list[str]
    removed: list[str]
    changed: list[str]


class VersionDiff(BaseModel):
    """Rows matched by id; a field absent on one side reads as null (C3)."""

    model_config = ConfigDict(populate_by_name=True)

    from_: int = Field(alias="from")
    to: int
    added: list[DiffRow]
    removed: list[DiffRow]
    modified: list[ModifiedRow]
    schema_changes: SchemaChanges
