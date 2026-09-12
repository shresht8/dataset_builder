"""Column schema routes (§8). Editor-only writes (§5).

    GET /datasets/{id}/schema
    PUT /datasets/{id}/schema
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from groundline_schema import Column, ColumnType
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.deps import get_db, require_role
from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.user import Role, User
from groundline_api.schemas.column import SchemaRead, SchemaUpdate

router = APIRouter(prefix="/datasets", tags=["schema"])

_OPTION_TYPES = {ColumnType.SELECT, ColumnType.MULTI_SELECT}


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

    for position, col in enumerate(payload.columns):
        options = col.options if col.type in _OPTION_TYPES else None
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
                )
            )
        else:
            current.label = col.label
            current.type = col.type
            current.options = options
            current.required = col.required
            current.order = position
            current.archived = False

    for key, current in existing.items():
        if key not in payload_keys:
            current.archived = True

    db.commit()
    active = [c for c in _columns_of(db, dataset_id) if not c.archived]
    return SchemaRead(columns=[_to_wire(c) for c in active])
