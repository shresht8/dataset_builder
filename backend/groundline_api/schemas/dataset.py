"""Dataset create/read/update payloads (§8)."""

from __future__ import annotations

import uuid
from datetime import datetime

from groundline_schema import Column
from pydantic import BaseModel, ConfigDict, Field

# Key-safe: usable unchanged as a directory name and a path segment (GL-3-15).
# Phase 4 (C1): every collection, folder and store-entry name uses it too; it
# forbids '/', '@' and '.', so a dataset path never needs escaping.
DATASET_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,99}$"


class DatasetCreate(BaseModel):
    name: str = Field(pattern=DATASET_NAME_PATTERN)
    description: str | None = None
    # Optional feature scope, default unscoped (§11 Q3).
    feature_id: str | None = Field(default=None, max_length=255)
    # Phase 4 (GL-115): place the dataset in a collection (and folder), with
    # either an existing store entry or a column list (resolved against the
    # store, D7). An unfiled dataset may also take `columns`.
    collection_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    store_schema_id: uuid.UUID | None = None
    columns: list[Column] | None = None


class StoreSchemaRef(BaseModel):
    """The store entry a dataset uses; `created` when a write just added it (D7)."""

    id: uuid.UUID
    name: str
    created: bool = False


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    feature_id: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    collection_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    store_schema_id: uuid.UUID | None = None
    # `collection/folder/…/name`, or the bare name when unfiled (C1).
    path: str = ""
    # The store entry a collection dataset uses; `created` only on the write
    # that added it (D7). Null when unfiled.
    store_schema: StoreSchemaRef | None = None


class DatasetMove(BaseModel):
    """Target of a move; `collection_id: null` makes the dataset unfiled (D9)."""

    collection_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None


class SchemaApply(BaseModel):
    store_schema_id: uuid.UUID
