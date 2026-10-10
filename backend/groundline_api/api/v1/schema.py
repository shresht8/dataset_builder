"""Column schema routes (§8). Editor-only writes (§5).

    GET /datasets/{id}/schema
    PUT /datasets/{id}/schema

Column keys may be dotted paths (`expected.golden_response`); no active key
may be a dotted prefix of another, or nesting would be ambiguous. A `json`
column may carry a JSON Schema (local `$ref`s only). At most one column is the
key column: text, required, and setting or changing it backfills every row's
`row_key` — or refuses if any row's value is missing or duplicated (GL-3.5-13).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from groundline_schema import Column, ColumnType
from groundline_schema.paths import PathError, split_path
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.deps import get_db, require_role
from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.row import DatasetRow
from groundline_api.models.user import Role, User
from groundline_api.schemas.column import SchemaRead, SchemaUpdate

router = APIRouter(prefix="/datasets", tags=["schema"])

_OPTION_TYPES = {ColumnType.SELECT, ColumnType.MULTI_SELECT}
_MAX_LISTED = 20


def _get_dataset_or_404(db: Session, dataset_id: uuid.UUID) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


def _columns_of(db: Session, dataset_id: uuid.UUID) -> list[DatasetColumn]:
    return list(
        db.scalars(
            select(DatasetColumn)
            .where(DatasetColumn.dataset_id == dataset_id)
            .order_by(DatasetColumn.archived, DatasetColumn.order, DatasetColumn.key)
        )
    )


def _to_wire(col: DatasetColumn) -> Column:
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


def _validate(columns: list[Column]) -> None:
    """options/uniqueness rules on top of the shared Column model. 422 on breach."""
    seen: set[str] = set()
    for col in columns:
        if col.key in seen:
            raise HTTPException(
                status_code=422, detail=f"duplicate column key: '{col.key}'"
            )
        seen.add(col.key)
        _validate_json_schema(col)
        if col.type in _OPTION_TYPES:
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
    rows = list(db.scalars(select(DatasetRow).where(DatasetRow.dataset_id == dataset_id)))
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


@router.get("/{dataset_id}/schema", response_model=SchemaRead)
def get_schema(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
    include_archived: bool = False,
) -> SchemaRead:
    _get_dataset_or_404(db, dataset_id)
    columns = _columns_of(db, dataset_id)
    if not include_archived:
        columns = [c for c in columns if not c.archived]
    return SchemaRead(columns=[_to_wire(c) for c in columns])


@router.put("/{dataset_id}/schema", response_model=SchemaRead)
def put_schema(
    dataset_id: uuid.UUID,
    payload: SchemaUpdate,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> SchemaRead:
    """Replace/upsert the column set.

    Keys present are upserted (re-adding an archived key un-archives it);
    keys absent are archived — never hard-deleted — so old rows and version
    snapshots stay interpretable. `order` follows list position.
    """
    _get_dataset_or_404(db, dataset_id)
    _validate(payload.columns)

    existing = {c.key: c for c in _columns_of(db, dataset_id)}
    payload_keys = {c.key for c in payload.columns}
    old_key = next((c.key for c in existing.values() if c.is_key and not c.archived), None)
    new_key = next((c.key for c in payload.columns if c.is_key), None)

    # Unset the current key column first: at most one active key column may
    # exist at any moment (partial unique index), whatever order the updates run.
    for current in existing.values():
        current.is_key = False
    db.flush()

    for position, col in enumerate(payload.columns):
        options = col.options if col.type in _OPTION_TYPES else None
        json_schema = col.json_schema
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
                    json_schema=json_schema,
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
            current.json_schema = json_schema
            current.is_key = col.is_key

    for key, current in existing.items():
        if key not in payload_keys:
            current.archived = True

    if new_key != old_key:
        _backfill_row_keys(db, dataset_id, new_key)

    db.commit()
    active = [c for c in _columns_of(db, dataset_id) if not c.archived]
    return SchemaRead(columns=[_to_wire(c) for c in active])
