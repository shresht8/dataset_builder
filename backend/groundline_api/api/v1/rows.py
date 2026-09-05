"""Row routes (§8). Optimistic locking via If-Match on PATCH (§4).

    GET   /datasets/{id}/rows?status=&assignee=&q=
    POST  /datasets/{id}/rows
    PATCH /datasets/{id}/rows/{rid}   If-Match: <rev>
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.deps import get_db, require_role
from groundline_api.models.dataset import Dataset
from groundline_api.models.row import DatasetRow
from groundline_api.models.user import Role, User
from groundline_api.schemas.row import RowCreate, RowRead

router = APIRouter(prefix="/datasets", tags=["rows"])


def _get_dataset_or_404(db: Session, dataset_id: uuid.UUID) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


@router.get("/{dataset_id}/rows", response_model=list[RowRead])
def list_rows(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> list[DatasetRow]:
    _get_dataset_or_404(db, dataset_id)
    return list(
        db.scalars(
            select(DatasetRow)
            .where(DatasetRow.dataset_id == dataset_id)
            .order_by(DatasetRow.created_at)
        )
    )


@router.post("/{dataset_id}/rows", response_model=RowRead, status_code=201)
def create_row(
    dataset_id: uuid.UUID,
    payload: RowCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.ANNOTATOR))],
) -> DatasetRow:
    _get_dataset_or_404(db, dataset_id)
    row = DatasetRow(dataset_id=dataset_id, data=payload.data, updated_by=user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

# TODO GL-1-5+: patch_row (409 on rev mismatch), status/assignee/q filters
