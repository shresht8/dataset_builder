"""Dataset routes (§8).

    GET  /datasets            list, filterable by feature_id
    POST /datasets            create; records created_by
    GET  /datasets/{id}       single, 404 if missing
    DELETE /datasets/{id}     editor+; only while it has no versions (GL-3.5-18)
"""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from groundline_api.deps import get_db, require_role
from groundline_api.models.dataset import Dataset
from groundline_api.models.user import Role, User
from groundline_api.models.version import DatasetVersion
from groundline_api.schemas.dataset import DatasetCreate, DatasetRead

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=list[DatasetRead])
def list_datasets(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
    feature_id: str | None = None,
) -> list[Dataset]:
    query = select(Dataset).order_by(Dataset.created_at)
    if feature_id is not None:
        query = query.where(Dataset.feature_id == feature_id)
    return list(db.scalars(query))


@router.get("/{dataset_id}", response_model=DatasetRead)
def get_dataset(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


@router.post("", response_model=DatasetRead, status_code=201)
def create_dataset(
    payload: DatasetCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> Dataset:
    dataset = Dataset(
        name=payload.name,
        description=payload.description,
        feature_id=payload.feature_id,
        created_by=user.id,
    )
    db.add(dataset)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"dataset name '{payload.name}' already exists",
        ) from None
    db.refresh(dataset)
    return dataset


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> Response:
    """Delete a dataset that has no versions, freeing its name.

    A version is an immutable, published snapshot that `groundline.lock` files
    may pin, so a dataset with any version can't be deleted (409). Rows,
    columns, comments and edits go with the dataset.
    """
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    versions = db.scalar(
        select(sa.func.count()).select_from(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    )
    if versions:
        raise HTTPException(
            status_code=409, detail=f"dataset has {versions} version(s) and cannot be deleted"
        )
    db.delete(dataset)
    db.commit()
    return Response(status_code=204)
