"""Collection and folder routes (Phase 4, GL-114: D1, D2, D16, D17, C1, C5).

    POST   /collections                    {name, description?}         editor+
    GET    /collections                                                 viewer+
    GET    /collections/{cid}              + tree: folders[], datasets[] viewer+
    PATCH  /collections/{cid}              {name?, description?}        editor+
    DELETE /collections/{cid}              409 unless no datasets        editor+
    POST   /collections/{cid}/folders      {name, parent_id?}            editor+
    PATCH  /folders/{fid}                  {name?, parent_id?} (no cycles) editor+
    DELETE /folders/{fid}                  409 unless empty              editor+

Names follow the dataset name pattern and are unique within their parent; a
clash is 409 `"<kind> '<name>' already exists in <parent>"`.
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
from groundline_api.models.row import DatasetRow
from groundline_api.models.user import Role, User
from groundline_api.models.version import DatasetVersion
from groundline_api.schemas.collection import (
    CollectionCreate,
    CollectionRead,
    CollectionTree,
    CollectionUpdate,
    FolderCreate,
    FolderRead,
    FolderUpdate,
    TreeDataset,
)
from groundline_api.services.paths import Paths

router = APIRouter(tags=["collections"])


def get_collection_or_404(db: Session, collection_id: uuid.UUID) -> Collection:
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="collection not found")
    return collection


def _get_folder_or_404(db: Session, folder_id: uuid.UUID) -> Folder:
    folder = db.get(Folder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="folder not found")
    return folder


def _dataset_counts(db: Session) -> dict[uuid.UUID, int]:
    return dict(
        db.execute(
            select(Dataset.collection_id, sa.func.count())
            .where(Dataset.collection_id.is_not(None))
            .group_by(Dataset.collection_id)
        ).all()
    )


def _read(collection: Collection, count: int) -> CollectionRead:
    read = CollectionRead.model_validate(collection)
    read.dataset_count = count
    return read


def _collection_clash(name: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"collection '{name}' already exists")


def _folder_clash(db: Session, name: str, collection_id: uuid.UUID, parent_id: uuid.UUID | None) -> HTTPException:
    parent = Paths(db).parent_path(collection_id, parent_id)
    return HTTPException(status_code=409, detail=f"folder '{name}' already exists in {parent}")


def _folder_name_taken(
    db: Session, collection_id: uuid.UUID, parent_id: uuid.UUID | None, name: str, exclude: uuid.UUID | None = None
) -> bool:
    query = select(Folder.id).where(
        Folder.collection_id == collection_id,
        Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id,
        Folder.name == name,
    )
    if exclude is not None:
        query = query.where(Folder.id != exclude)
    return db.scalar(query) is not None


@router.post("/collections", response_model=CollectionRead, status_code=201)
def create_collection(
    payload: CollectionCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> CollectionRead:
    if db.scalar(select(Collection.id).where(Collection.name == payload.name)):
        raise _collection_clash(payload.name)
    collection = Collection(name=payload.name, description=payload.description, created_by=user.id)
    db.add(collection)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _collection_clash(payload.name) from None
    db.refresh(collection)
    return _read(collection, 0)


@router.get("/collections", response_model=list[CollectionRead])
def list_collections(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> list[CollectionRead]:
    counts = _dataset_counts(db)
    return [
        _read(c, counts.get(c.id, 0))
        for c in db.scalars(select(Collection).order_by(Collection.name))
    ]


@router.get("/collections/{collection_id}", response_model=CollectionTree)
def get_collection(
    collection_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> CollectionTree:
    """The collection and its whole tree: every folder and every dataset."""
    collection = get_collection_or_404(db, collection_id)
    folders = list(
        db.scalars(
            select(Folder).where(Folder.collection_id == collection_id).order_by(Folder.name)
        )
    )
    row_counts = (
        select(DatasetRow.dataset_id, sa.func.count().label("n"))
        .where(DatasetRow.deleted_at.is_(None))
        .group_by(DatasetRow.dataset_id)
        .subquery()
    )
    version_counts = (
        select(DatasetVersion.dataset_id, sa.func.count().label("n"))
        .group_by(DatasetVersion.dataset_id)
        .subquery()
    )
    rows = db.execute(
        select(
            Dataset,
            CollectionSchema.name,
            sa.func.coalesce(row_counts.c.n, 0),
            sa.func.coalesce(version_counts.c.n, 0),
        )
        .outerjoin(CollectionSchema, CollectionSchema.id == Dataset.store_schema_id)
        .outerjoin(row_counts, row_counts.c.dataset_id == Dataset.id)
        .outerjoin(version_counts, version_counts.c.dataset_id == Dataset.id)
        .where(Dataset.collection_id == collection_id)
        .order_by(Dataset.name)
    ).all()
    datasets = [
        TreeDataset(
            id=d.id,
            name=d.name,
            description=d.description,
            folder_id=d.folder_id,
            store_schema_id=d.store_schema_id,
            store_schema_name=schema_name,
            row_count=n_rows,
            version_count=n_versions,
        )
        for d, schema_name, n_rows, n_versions in rows
    ]
    base = _read(collection, len(datasets))
    return CollectionTree(
        **base.model_dump(),
        folders=[FolderRead.model_validate(f) for f in folders],
        datasets=datasets,
    )


@router.patch("/collections/{collection_id}", response_model=CollectionRead)
def update_collection(
    collection_id: uuid.UUID,
    payload: CollectionUpdate,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> CollectionRead:
    collection = get_collection_or_404(db, collection_id)
    if payload.name is not None and payload.name != collection.name:
        if db.scalar(select(Collection.id).where(Collection.name == payload.name)):
            raise _collection_clash(payload.name)
        collection.name = payload.name
    if "description" in payload.model_fields_set:
        collection.description = payload.description
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _collection_clash(payload.name or "") from None
    db.refresh(collection)
    return _read(collection, _dataset_counts(db).get(collection.id, 0))


@router.delete("/collections/{collection_id}", status_code=204)
def delete_collection(
    collection_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> Response:
    """Delete a collection that holds no datasets; its folders and store go with it (D17)."""
    collection = get_collection_or_404(db, collection_id)
    count = _dataset_counts(db).get(collection_id, 0)
    if count:
        raise HTTPException(
            status_code=409,
            detail=f"collection '{collection.name}' holds {count} dataset(s); "
            "move or delete them first",
        )
    db.delete(collection)
    db.commit()
    return Response(status_code=204)


@router.post(
    "/collections/{collection_id}/folders", response_model=FolderRead, status_code=201
)
def create_folder(
    collection_id: uuid.UUID,
    payload: FolderCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> Folder:
    get_collection_or_404(db, collection_id)
    if payload.parent_id is not None:
        parent = db.get(Folder, payload.parent_id)
        if parent is None or parent.collection_id != collection_id:
            raise HTTPException(status_code=422, detail="parent folder is not in this collection")
    if _folder_name_taken(db, collection_id, payload.parent_id, payload.name):
        raise _folder_clash(db, payload.name, collection_id, payload.parent_id)
    folder = Folder(
        collection_id=collection_id,
        parent_id=payload.parent_id,
        name=payload.name,
        created_by=user.id,
    )
    db.add(folder)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _folder_clash(db, payload.name, collection_id, payload.parent_id) from None
    db.refresh(folder)
    return folder


@router.patch("/folders/{folder_id}", response_model=FolderRead)
def update_folder(
    folder_id: uuid.UUID,
    payload: FolderUpdate,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> Folder:
    """Rename and/or re-parent within the same collection; no folder may move into its own subtree."""
    folder = _get_folder_or_404(db, folder_id)
    name = payload.name if payload.name is not None else folder.name
    parent_id = payload.parent_id if "parent_id" in payload.model_fields_set else folder.parent_id

    if parent_id is not None:
        parent = db.get(Folder, parent_id)
        if parent is None or parent.collection_id != folder.collection_id:
            raise HTTPException(status_code=422, detail="parent folder is not in this collection")
        ancestor: Folder | None = parent
        while ancestor is not None:
            if ancestor.id == folder.id:
                raise HTTPException(
                    status_code=422, detail="a folder can't move into itself or its own subfolder"
                )
            ancestor = db.get(Folder, ancestor.parent_id) if ancestor.parent_id else None

    if _folder_name_taken(db, folder.collection_id, parent_id, name, exclude=folder.id):
        raise _folder_clash(db, name, folder.collection_id, parent_id)
    folder.name = name
    folder.parent_id = parent_id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _folder_clash(db, name, folder.collection_id, parent_id) from None
    db.refresh(folder)
    return folder


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(
    folder_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> Response:
    """Delete an empty folder: no subfolders and no datasets (D17)."""
    folder = _get_folder_or_404(db, folder_id)
    subfolders = db.scalar(select(sa.func.count()).select_from(Folder).where(Folder.parent_id == folder_id))
    datasets = db.scalar(select(sa.func.count()).select_from(Dataset).where(Dataset.folder_id == folder_id))
    if subfolders or datasets:
        raise HTTPException(
            status_code=409,
            detail=f"folder '{folder.name}' is not empty "
            f"({subfolders} subfolder(s), {datasets} dataset(s))",
        )
    db.delete(folder)
    db.commit()
    return Response(status_code=204)
