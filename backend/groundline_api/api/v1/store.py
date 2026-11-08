"""Schema store routes (Phase 4, GL-115: D6, D7, C5).

    GET    /collections/{cid}/schemas                    entries + used_by[]   viewer+
    POST   /collections/{cid}/schemas                    {name, columns}       editor+
    GET    /collections/{cid}/schemas/{sid}                                    viewer+
    PUT    /collections/{cid}/schemas/{sid}              {name?, columns?}     editor+
    DELETE /collections/{cid}/schemas/{sid}                                    editor+
    POST   /collections/{cid}/schemas/{sid}/duplicate    {name}                editor+

An entry in use by any dataset can't be edited or deleted (D6): 409 with the
using datasets in `used_by`. To change a dataset's schema, duplicate its entry,
edit the copy, apply it, then delete the old entry once nothing uses it.
Entries are validated exactly like a dataset schema.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from groundline_api.api.v1.collections import get_collection_or_404
from groundline_api.deps import get_db, require_role
from groundline_api.models.collection import CollectionSchema
from groundline_api.models.user import Role, User
from groundline_api.schemas.collection import (
    StoreEntryCreate,
    StoreEntryDuplicate,
    StoreEntryRead,
    StoreEntryUpdate,
    UsedBy,
)
from groundline_api.services import store
from groundline_api.services.paths import Paths

router = APIRouter(prefix="/collections/{collection_id}/schemas", tags=["store"])


def _get_entry_or_404(db: Session, collection_id: uuid.UUID, entry_id: uuid.UUID) -> CollectionSchema:
    get_collection_or_404(db, collection_id)
    entry = db.get(CollectionSchema, entry_id)
    if entry is None or entry.collection_id != collection_id:
        raise HTTPException(status_code=404, detail="schema not found")
    return entry


def _used_by(db: Session, entry: CollectionSchema, paths: Paths) -> list[UsedBy]:
    return [
        UsedBy(id=d.id, name=d.name, path=paths.dataset_path(d)) for d in store.used_by(db, entry.id)
    ]


def _read(db: Session, entry: CollectionSchema, paths: Paths | None = None) -> StoreEntryRead:
    return StoreEntryRead(
        id=entry.id,
        collection_id=entry.collection_id,
        name=entry.name,
        columns=store.entry_columns(entry),
        content_hash=entry.content_hash,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        used_by=_used_by(db, entry, paths or Paths(db)),
    )


def _clash(db: Session, collection_id: uuid.UUID, name: str) -> HTTPException:
    parent = Paths(db).collection_name(collection_id)
    return HTTPException(status_code=409, detail=f"schema '{name}' already exists in {parent}")


def _in_use(db: Session, entry: CollectionSchema, action: str) -> JSONResponse | None:
    used = _used_by(db, entry, Paths(db))
    if not used:
        return None
    listed = ", ".join(u.path for u in used)
    return JSONResponse(
        status_code=409,
        content={
            "detail": f"schema '{entry.name}' is used by {len(used)} dataset(s) ({listed}) "
            f"and can't be {action}; duplicate it to change it",
            "used_by": [u.model_dump(mode="json") for u in used],
        },
    )


def _commit(db: Session, collection_id: uuid.UUID, name: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _clash(db, collection_id, name) from None


@router.get("", response_model=list[StoreEntryRead])
def list_entries(
    collection_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> list[StoreEntryRead]:
    get_collection_or_404(db, collection_id)
    paths = Paths(db)
    entries = db.scalars(
        select(CollectionSchema)
        .where(CollectionSchema.collection_id == collection_id)
        .order_by(CollectionSchema.name)
    )
    return [_read(db, e, paths) for e in entries]


@router.post("", response_model=StoreEntryRead, status_code=201)
def create_entry(
    collection_id: uuid.UUID,
    payload: StoreEntryCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> StoreEntryRead:
    get_collection_or_404(db, collection_id)
    if store.name_taken(db, collection_id, payload.name):
        raise _clash(db, collection_id, payload.name)
    entry = store.create_entry(db, collection_id, payload.name, payload.columns, user)
    _commit(db, collection_id, payload.name)
    db.refresh(entry)
    return _read(db, entry)


@router.get("/{entry_id}", response_model=StoreEntryRead)
def get_entry(
    collection_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> StoreEntryRead:
    return _read(db, _get_entry_or_404(db, collection_id, entry_id))


@router.put("/{entry_id}", response_model=None)
def update_entry(
    collection_id: uuid.UUID,
    entry_id: uuid.UUID,
    payload: StoreEntryUpdate,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> StoreEntryRead | JSONResponse:
    """Edit an unused entry (D6); one in use is 409 with `used_by`."""
    entry = _get_entry_or_404(db, collection_id, entry_id)
    refused = _in_use(db, entry, "edited")
    if refused is not None:
        return refused
    if payload.name is not None and store.name_taken(db, collection_id, payload.name, exclude=entry.id):
        raise _clash(db, collection_id, payload.name)
    store.update_entry(entry, payload.name, payload.columns)
    _commit(db, collection_id, entry.name)
    db.refresh(entry)
    return _read(db, entry)


@router.delete("/{entry_id}", status_code=204, response_model=None)
def delete_entry(
    collection_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> Response:
    entry = _get_entry_or_404(db, collection_id, entry_id)
    refused = _in_use(db, entry, "deleted")
    if refused is not None:
        return refused
    db.delete(entry)
    db.commit()
    return Response(status_code=204)


@router.post("/{entry_id}/duplicate", response_model=StoreEntryRead, status_code=201)
def duplicate_entry(
    collection_id: uuid.UUID,
    entry_id: uuid.UUID,
    payload: StoreEntryDuplicate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> StoreEntryRead:
    entry = _get_entry_or_404(db, collection_id, entry_id)
    if store.name_taken(db, collection_id, payload.name):
        raise _clash(db, collection_id, payload.name)
    copy = store.create_entry(db, collection_id, payload.name, store.entry_columns(entry), user)
    _commit(db, collection_id, payload.name)
    db.refresh(copy)
    return _read(db, copy)
