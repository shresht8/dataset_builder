"""Per-collection schema store (Phase 4, GL-115: D5–D9, C3).

A dataset in a collection uses exactly one store entry, and its
`dataset_columns` equal that entry's columns. Every schema write for a
collection dataset goes through `apply` (directly, or via `set_dataset_schema`
/ `resolve_or_add`), so no route can leave a dataset out of step with the
store. `dataset_columns` stays the source of truth for validation, import,
sync and versioning.

- `resolve_or_add` (D7): an entry with the same C3 hash is reused; anything
  else becomes a new entry named after the dataset (`-2`, `-3`, … on a clash).
- An entry in use by any dataset is frozen (D6): routes refuse to edit or
  delete it (409 listing the datasets).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from groundline_schema import Column, schema_hash
from groundline_schema.schema_hash import canonical_columns
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.models.collection import CollectionSchema
from groundline_api.models.dataset import Dataset
from groundline_api.models.user import User
from groundline_api.services.columns import validate_columns, write_columns
from groundline_api.services.paths import NAME_RE

_MAX_NAME = 100


@dataclass
class StoreOutcome:
    """Which entry a schema write used, and whether it was added just now."""

    entry: CollectionSchema
    created: bool


def normalise(columns: list[Column]) -> list[Column]:
    """Columns as they will be stored: all active, `order` from list position."""
    return [col.model_copy(update={"order": i, "archived": False}) for i, col in enumerate(columns)]


def entry_columns(entry: CollectionSchema) -> list[Column]:
    return [Column.model_validate({**c, "order": i}) for i, c in enumerate(entry.columns)]


def used_by(db: Session, entry_id: uuid.UUID) -> list[Dataset]:
    return list(
        db.scalars(
            select(Dataset).where(Dataset.store_schema_id == entry_id).order_by(Dataset.name)
        )
    )


def name_taken(db: Session, collection_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> bool:
    query = select(CollectionSchema.id).where(
        CollectionSchema.collection_id == collection_id, CollectionSchema.name == name
    )
    if exclude is not None:
        query = query.where(CollectionSchema.id != exclude)
    return db.scalar(query) is not None


def _free_name(db: Session, collection_id: uuid.UUID, preferred: str) -> str:
    if not name_taken(db, collection_id, preferred):
        return preferred
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = preferred[: _MAX_NAME - len(suffix)] + suffix
        if not name_taken(db, collection_id, candidate):
            return candidate
        n += 1


def check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail=f"invalid schema name '{name}': lowercase letters, digits, '-' and '_' only",
        )


def create_entry(
    db: Session, collection_id: uuid.UUID, name: str, columns: list[Column], user: User | None
) -> CollectionSchema:
    """Validate and add an entry (flushes). The caller handles a name clash first."""
    columns = normalise(columns)
    validate_columns(columns)
    entry = CollectionSchema(
        collection_id=collection_id,
        name=name,
        columns=canonical_columns(columns),
        content_hash=schema_hash(columns),
        created_by=user.id if user else None,
    )
    db.add(entry)
    db.flush()
    return entry


def update_entry(entry: CollectionSchema, name: str | None, columns: list[Column] | None) -> None:
    if name is not None:
        entry.name = name
    if columns is not None:
        columns = normalise(columns)
        validate_columns(columns)
        entry.columns = canonical_columns(columns)
        entry.content_hash = schema_hash(columns)
    entry.updated_at = datetime.now(timezone.utc)


def resolve_or_add(
    db: Session,
    collection_id: uuid.UUID,
    columns: list[Column],
    preferred_name: str,
    user: User | None,
) -> StoreOutcome:
    """The entry with exactly these columns (C3 hash), or a new one (D7)."""
    columns = normalise(columns)
    validate_columns(columns)
    existing = db.scalar(
        select(CollectionSchema)
        .where(
            CollectionSchema.collection_id == collection_id,
            CollectionSchema.content_hash == schema_hash(columns),
        )
        .order_by(CollectionSchema.created_at, CollectionSchema.name)
        .limit(1)
    )
    if existing is not None:
        return StoreOutcome(existing, False)
    name = _free_name(db, collection_id, preferred_name)
    return StoreOutcome(create_entry(db, collection_id, name, columns, user), True)


def apply(db: Session, dataset: Dataset, entry: CollectionSchema) -> None:
    """Make the dataset's columns equal the entry's, then point it at the entry (D8).

    Absent columns are archived; a key change backfills or refuses (422).
    Flushes, never commits.
    """
    write_columns(db, dataset.id, entry_columns(entry))
    dataset.store_schema_id = entry.id
    db.flush()


def set_dataset_schema(
    db: Session, dataset: Dataset, columns: list[Column], user: User | None
) -> StoreOutcome | None:
    """The one entry point for setting a dataset's schema from a column list.

    Unfiled: write the columns as today. In a collection: resolve the columns
    against the store (D7) and apply that entry.
    """
    columns = normalise(columns)
    validate_columns(columns)
    if dataset.collection_id is None:
        write_columns(db, dataset.id, columns)
        return None
    outcome = resolve_or_add(db, dataset.collection_id, columns, dataset.name, user)
    apply(db, dataset, outcome.entry)
    return outcome
