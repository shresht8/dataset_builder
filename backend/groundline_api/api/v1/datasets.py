"""Dataset routes (§8).

    GET  /datasets            list, filterable by feature_id, collection_id,
                              folder_id, unfiled=true
    GET  /datasets/resolve    ?path=collection/folder/…/name (or a bare unfiled name)
    POST /datasets            create; records created_by; optionally in a
                              collection/folder with a store entry or columns
    GET  /datasets/{id}       single, 404 if missing
    POST /datasets/{id}/move  into/between collections, or unfiled (D9)
    DELETE /datasets/{id}     editor+; only while it has no versions (GL-3.5-18)

Every read carries `path` (C1). A name is unique within its parent (D2): a
clash is 409.
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
from groundline_api.models.collection import Collection, CollectionSchema, Folder
from groundline_api.models.dataset import Dataset
from groundline_api.models.user import Role, User
from groundline_api.models.version import DatasetVersion
from groundline_api.schemas.dataset import (
    DatasetCreate,
    DatasetMove,
    DatasetRead,
    StoreSchemaRef,
)
from groundline_api.services import store
from groundline_api.services.columns import active_wire_columns
from groundline_api.services.paths import Paths, dataset_name_taken, resolve_path

router = APIRouter(prefix="/datasets", tags=["datasets"])


def to_read(dataset: Dataset, paths: Paths, created: bool = False) -> DatasetRead:
    read = DatasetRead.model_validate(dataset)
    read.path = paths.dataset_path(dataset)
    if dataset.store_schema_id is not None:
        read.store_schema = StoreSchemaRef(
            id=dataset.store_schema_id,
            name=paths.store_schema_name(dataset.store_schema_id),
            created=created,
        )
    return read


def _written(
    db: Session, dataset: Dataset, outcome: store.StoreOutcome | None
) -> DatasetRead:
    return to_read(dataset, Paths(db), created=bool(outcome and outcome.created))


def _clash(db: Session, name: str, collection_id: uuid.UUID | None, folder_id: uuid.UUID | None) -> HTTPException:
    parent = Paths(db).parent_path(collection_id, folder_id)
    return HTTPException(status_code=409, detail=f"dataset '{name}' already exists in {parent}")


def _target(
    db: Session, collection_id: uuid.UUID | None, folder_id: uuid.UUID | None
) -> Collection | None:
    """Check a (collection, folder) placement; 422 if inconsistent, 404 if missing."""
    if collection_id is None:
        if folder_id is not None:
            raise HTTPException(status_code=422, detail="folder_id needs a collection_id")
        return None
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="collection not found")
    if folder_id is not None:
        folder = db.get(Folder, folder_id)
        if folder is None or folder.collection_id != collection_id:
            raise HTTPException(
                status_code=422, detail="folder does not belong to the collection"
            )
    return collection


@router.get("", response_model=list[DatasetRead])
def list_datasets(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
    feature_id: str | None = None,
    collection_id: uuid.UUID | None = None,
    folder_id: uuid.UUID | None = None,
    unfiled: bool = False,
) -> list[DatasetRead]:
    query = select(Dataset).order_by(Dataset.created_at)
    if feature_id is not None:
        query = query.where(Dataset.feature_id == feature_id)
    if collection_id is not None:
        query = query.where(Dataset.collection_id == collection_id)
    if folder_id is not None:
        query = query.where(Dataset.folder_id == folder_id)
    if unfiled:
        query = query.where(Dataset.collection_id.is_(None))
    paths = Paths(db)
    return [to_read(d, paths) for d in db.scalars(query)]


# Declared before /{dataset_id}, or "resolve" would hit the UUID converter (422).
@router.get("/resolve", response_model=DatasetRead)
def resolve_dataset(
    path: str,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> DatasetRead:
    return to_read(resolve_path(db, path), Paths(db))


@router.get("/{dataset_id}", response_model=DatasetRead)
def get_dataset(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> DatasetRead:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return to_read(dataset, Paths(db))


@router.post("", response_model=DatasetRead, status_code=201)
def create_dataset(
    payload: DatasetCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> DatasetRead:
    """Create a dataset, with its schema, in one transaction.

    In a collection, give either `store_schema_id` (an entry of that store) or
    `columns` (resolved against the store, D7). Unfiled, `columns` is optional.
    """
    collection = _target(db, payload.collection_id, payload.folder_id)
    entry: CollectionSchema | None = None
    outcome: store.StoreOutcome | None = None
    if collection is None:
        if payload.store_schema_id is not None:
            raise HTTPException(
                status_code=422, detail="store_schema_id needs a collection_id"
            )
    else:
        if (payload.store_schema_id is None) == (payload.columns is None):
            raise HTTPException(
                status_code=422,
                detail="a dataset in a collection needs either store_schema_id or columns",
            )
        if dataset_name_taken(db, payload.name, collection.id, payload.folder_id):
            raise _clash(db, payload.name, collection.id, payload.folder_id)
        if payload.store_schema_id is not None:
            entry = db.get(CollectionSchema, payload.store_schema_id)
            if entry is None or entry.collection_id != collection.id:
                raise HTTPException(
                    status_code=422, detail="store schema does not belong to the collection"
                )
        else:
            outcome = store.resolve_or_add(db, collection.id, payload.columns, payload.name, user)
            entry = outcome.entry

    dataset = Dataset(
        name=payload.name,
        description=payload.description,
        feature_id=payload.feature_id,
        created_by=user.id,
        collection_id=payload.collection_id,
        folder_id=payload.folder_id,
        store_schema_id=entry.id if entry else None,
    )
    db.add(dataset)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        if collection is None:
            raise HTTPException(
                status_code=409,
                detail=f"dataset name '{payload.name}' already exists",
            ) from None
        raise _clash(db, payload.name, payload.collection_id, payload.folder_id) from None
    try:
        if entry is not None:
            store.apply(db, dataset, entry)
        elif payload.columns is not None:
            store.set_dataset_schema(db, dataset, payload.columns, user)
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    db.refresh(dataset)
    return _written(db, dataset, outcome)


@router.post("/{dataset_id}/move", response_model=DatasetRead)
def move_dataset(
    dataset_id: uuid.UUID,
    payload: DatasetMove,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> DatasetRead:
    """Move a dataset (D9). Its columns never change.

    Into a collection, or between collections, its active schema is resolved
    against the target store (an identical entry is reused, else one is
    added). Within one collection it keeps its entry. To unfiled
    (`collection_id: null`) its store entry is cleared. A name clash in the
    target is 409 and nothing moves.
    """
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    collection = _target(db, payload.collection_id, payload.folder_id)
    target_collection_id = collection.id if collection else None
    if dataset_name_taken(db, dataset.name, target_collection_id, payload.folder_id, exclude=dataset.id):
        raise _clash(db, dataset.name, target_collection_id, payload.folder_id)

    outcome: store.StoreOutcome | None = None
    if collection is None:
        store_schema_id = None
    elif dataset.collection_id == collection.id:
        store_schema_id = dataset.store_schema_id
    else:
        outcome = store.resolve_or_add(
            db, collection.id, active_wire_columns(db, dataset.id), dataset.name, user
        )
        store_schema_id = outcome.entry.id

    dataset.collection_id = target_collection_id
    dataset.folder_id = payload.folder_id
    dataset.store_schema_id = store_schema_id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _clash(db, dataset.name, target_collection_id, payload.folder_id) from None
    db.refresh(dataset)
    return _written(db, dataset, outcome)


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
