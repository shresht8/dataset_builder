"""Column-set validation and writes (§3; extracted from api/v1/schema.py, GL-115).

One place for the column rules, so a dataset schema (`PUT …/schema`) and a
schema store entry are validated identically:

- keys may be dotted paths (`expected.golden_response`); no active key may be
  a dotted prefix of another, or nesting would be ambiguous;
- select / multi_select need non-empty options; other types take none;
- a `json` column may carry a JSON Schema (local `$ref`s only);
- at most one key column: text and required.

`write_columns` applies a column list to a dataset with today's schema-PUT
rules: keys present are upserted (re-adding an archived key un-archives it),
keys absent are archived -- never hard-deleted -- and a key-column change
backfills every row's `row_key`, or refuses (422) if any value is missing or
duplicated (GL-3.5-13). It flushes but never commits.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from fastapi import HTTPException
from groundline_schema import Column, ColumnType
from groundline_schema.paths import PathError, split_path
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.models.dataset import DatasetColumn
from groundline_api.models.row import DatasetRow

OPTION_TYPES = {ColumnType.SELECT, ColumnType.MULTI_SELECT}
_MAX_LISTED = 20


def columns_of(db: Session, dataset_id: uuid.UUID) -> list[DatasetColumn]:
    """All columns, active first, in schema order."""
    return list(
        db.scalars(
            select(DatasetColumn)
            .where(DatasetColumn.dataset_id == dataset_id)
            .order_by(DatasetColumn.archived, DatasetColumn.order, DatasetColumn.key)
        )
    )


def to_wire(col: DatasetColumn) -> Column:
    return Column(
        key=col.key,
        label=col.label,
        type=col.type,
        options=col.options,
        required=col.required,
        order=col.order,
        archived=col.archived,
        json_schema=col.json_schema,
        is_key=col.is_key,
    )


def active_wire_columns(db: Session, dataset_id: uuid.UUID) -> list[Column]:
    return [to_wire(c) for c in columns_of(db, dataset_id) if not c.archived]


def _remote_refs(node: Any) -> list[str]:
    """Every `$ref` in a JSON Schema that doesn't point inside the schema itself."""
    if isinstance(node, dict):
        found = [
            value
            for key, value in node.items()
            if key in ("$ref", "$dynamicRef") and isinstance(value, str)
            and not value.startswith("#")
        ]
        for value in node.values():
            found.extend(_remote_refs(value))
        return found
    if isinstance(node, list):
        return [ref for item in node for ref in _remote_refs(item)]
    return []


def _validate_json_schema(col: Column) -> None:
    if col.json_schema is None:
        return
    if col.type != ColumnType.JSON:
        raise HTTPException(
            status_code=422,
            detail=f"column '{col.key}': json_schema is only allowed for json columns",
        )
    try:
        Draft202012Validator.check_schema(col.json_schema)
    except SchemaError as exc:
        raise HTTPException(
            status_code=422, detail=f"column '{col.key}': invalid json_schema: {exc.message}"
        ) from None
    remote = _remote_refs(col.json_schema)
    if remote:
        # Validation never fetches anything over the network.
        raise HTTPException(
            status_code=422,
            detail=(
                f"column '{col.key}': json_schema may only use local $ref ('#/...'), "
                f"got {remote[0]!r}"
            ),
        )


def _validate_paths(columns: list[Column]) -> None:
    keys = {col.key for col in columns}
    for col in columns:
        try:
            segments = split_path(col.key)
        except PathError:
            raise HTTPException(
                status_code=422,
                detail=f"column key '{col.key}': every '.'-separated part must be non-empty",
            ) from None
        for end in range(1, len(segments)):
            prefix = ".".join(segments[:end])
            if prefix in keys:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"column keys '{prefix}' and '{col.key}' clash: '{prefix}' "
                        "can't be both a value and an object"
                    ),
                )


def _validate_key_column(columns: list[Column]) -> None:
    keys = [col for col in columns if col.is_key]
    if len(keys) > 1:
        raise HTTPException(
            status_code=422,
            detail="only one key column is allowed, got "
            + ", ".join(f"'{col.key}'" for col in keys),
        )
    if keys and keys[0].type != ColumnType.TEXT:
        raise HTTPException(
            status_code=422, detail=f"key column '{keys[0].key}' must be of type text"
        )
    if keys and not keys[0].required:
        raise HTTPException(
            status_code=422, detail=f"key column '{keys[0].key}' must be required"
        )


def validate_columns(columns: list[Column]) -> None:
    """options/uniqueness rules on top of the shared Column model. 422 on breach."""
    seen: set[str] = set()
    for col in columns:
        if col.key in seen:
            raise HTTPException(
                status_code=422, detail=f"duplicate column key: '{col.key}'"
            )
        seen.add(col.key)
        _validate_json_schema(col)
        if col.type in OPTION_TYPES:
            if not col.options:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"column '{col.key}': type '{col.type.value}' requires "
                        "non-empty options"
                    ),
                )
        elif col.options is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"column '{col.key}': options are only allowed for "
                    f"select/multi_select, not '{col.type.value}'"
                ),
            )
    _validate_paths(columns)
    _validate_key_column(columns)


def _key_problems(rows: list[DatasetRow], key: str) -> list[str]:
    """Why `key` can't identify these rows: missing/untrimmed or duplicated values."""
    problems: list[str] = []
    rows_by_value: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        value = row.data.get(key)
        if not isinstance(value, str) or value.strip() == "":
            problems.append(f"row {row.id} has no value")
        elif value != value.strip():
            problems.append(f"row {row.id}: {value!r} has leading or trailing whitespace")
        else:
            rows_by_value[value].append(str(row.id))
    for value, ids in rows_by_value.items():
        if len(ids) > 1:
            problems.append(f"{value!r} is used by rows " + ", ".join(ids))
    return problems


def _backfill_row_keys(db: Session, dataset_id: uuid.UUID, key: str | None) -> None:
    """Point every row's `row_key` at the new key column (or clear it).

    Old values are cleared and flushed first, so swapping keys between rows
    can't trip the unique index halfway through.
    """
    rows = list(
        db.scalars(
            select(DatasetRow).where(
                DatasetRow.dataset_id == dataset_id, DatasetRow.deleted_at.is_(None)
            )
        )
    )
    if key is not None:
        problems = _key_problems(rows, key)
        if problems:
            more = len(problems) - _MAX_LISTED
            raise HTTPException(
                status_code=422,
                detail=f"can't make '{key}' the key column: "
                + "; ".join(problems[:_MAX_LISTED])
                + (f" (and {more} more)" if more > 0 else ""),
            )
    for row in rows:
        row.row_key = None
    db.flush()
    if key is not None:
        for row in rows:
            row.row_key = row.data[key]
        db.flush()


def write_columns(db: Session, dataset_id: uuid.UUID, columns: list[Column]) -> None:
    """Replace/upsert a dataset's column set. Validate first; flushes, never commits.

    `order` follows list position. On a 422 (key backfill refused) the caller
    must roll back.
    """
    existing = {c.key: c for c in columns_of(db, dataset_id)}
    payload_keys = {c.key for c in columns}
    old_key = next((c.key for c in existing.values() if c.is_key and not c.archived), None)
    new_key = next((c.key for c in columns if c.is_key), None)

    # Unset the current key column first: at most one active key column may
    # exist at any moment (partial unique index), whatever order the updates run.
    for current in existing.values():
        current.is_key = False
    db.flush()

    for position, col in enumerate(columns):
        options = col.options if col.type in OPTION_TYPES else None
        current = existing.get(col.key)
        if current is None:
            db.add(
                DatasetColumn(
                    dataset_id=dataset_id,
                    key=col.key,
                    label=col.label,
                    type=col.type,
                    options=options,
                    required=col.required,
                    order=position,
                    archived=False,
                    json_schema=col.json_schema,
                    is_key=col.is_key,
                )
            )
        else:
            current.label = col.label
            current.type = col.type
            current.options = options
            current.required = col.required
            current.order = position
            current.archived = False
            current.json_schema = col.json_schema
            current.is_key = col.is_key

    for key, current in existing.items():
        if key not in payload_keys:
            current.archived = True
    db.flush()

    if new_key != old_key:
        _backfill_row_keys(db, dataset_id, new_key)
