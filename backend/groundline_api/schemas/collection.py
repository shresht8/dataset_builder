"""Collection, folder and schema-store payloads (Phase 4, C5)."""

from __future__ import annotations

import uuid
from datetime import datetime

from groundline_schema import Column
from pydantic import BaseModel, ConfigDict, Field

from groundline_api.schemas.dataset import DATASET_NAME_PATTERN


class CollectionCreate(BaseModel):
    name: str = Field(pattern=DATASET_NAME_PATTERN)
    description: str | None = None


class CollectionUpdate(BaseModel):
    name: str | None = Field(default=None, pattern=DATASET_NAME_PATTERN)
    description: str | None = None


class CollectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    dataset_count: int = 0


class FolderCreate(BaseModel):
    name: str = Field(pattern=DATASET_NAME_PATTERN)
    parent_id: uuid.UUID | None = None


class FolderUpdate(BaseModel):
    """Rename and/or re-parent; an explicit `parent_id: null` moves to the root."""

    name: str | None = Field(default=None, pattern=DATASET_NAME_PATTERN)
    parent_id: uuid.UUID | None = None


class FolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    collection_id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None


class TreeDataset(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    folder_id: uuid.UUID | None
    store_schema_id: uuid.UUID | None
    store_schema_name: str | None
    row_count: int
    version_count: int


class CollectionTree(CollectionRead):
    folders: list[FolderRead]
    datasets: list[TreeDataset]


class StoreEntryCreate(BaseModel):
    name: str = Field(pattern=DATASET_NAME_PATTERN)
    columns: list[Column]


class StoreEntryUpdate(BaseModel):
    name: str | None = Field(default=None, pattern=DATASET_NAME_PATTERN)
    columns: list[Column] | None = None


class StoreEntryDuplicate(BaseModel):
    name: str = Field(pattern=DATASET_NAME_PATTERN)


class UsedBy(BaseModel):
    id: uuid.UUID
    name: str
    path: str


class StoreEntryRead(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    name: str
    columns: list[Column]
    content_hash: str
    created_at: datetime
    updated_at: datetime
    used_by: list[UsedBy]
