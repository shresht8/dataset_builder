"""Column schema routes (§8). Editor-only writes (§5).

    GET  /datasets/{id}/schema
    PUT  /datasets/{id}/schema
    POST /datasets/{id}/schema/apply   {store_schema_id}   (Phase 4)

Column rules live in `services/columns.py` (dotted keys, options, json_schema,
one text+required key column whose change backfills `row_key` or refuses).
A dataset in a collection takes its schema from the collection's store
(GL-115): `PUT` resolves the columns against the store (an identical entry is
reused, anything else is added, D7) and applies that entry; `apply` switches
to another entry of the same store. Both report `store_schema`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from groundline_api.deps import get_db, require_role
from groundline_api.models.collection import CollectionSchema
from groundline_api.models.dataset import Dataset
from groundline_api.models.user import Role, User
from groundline_api.schemas.column import SchemaRead, SchemaUpdate
from groundline_api.schemas.dataset import SchemaApply, StoreSchemaRef
from groundline_api.services import store
from groundline_api.services.columns import active_wire_columns, columns_of, to_wire

router = APIRouter(prefix="/datasets", tags=["schema"])


def _get_dataset_or_404(db: Session, dataset_id: uuid.UUID) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


def _store_ref(db: Session, dataset: Dataset, created: bool = False) -> StoreSchemaRef | None:
    if dataset.store_schema_id is None:
        return None
    entry = db.get(CollectionSchema, dataset.store_schema_id)
    return StoreSchemaRef(id=entry.id, name=entry.name, created=created)


@router.get("/{dataset_id}/schema", response_model=SchemaRead)
def get_schema(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
    include_archived: bool = False,
) -> SchemaRead:
    dataset = _get_dataset_or_404(db, dataset_id)
    columns = columns_of(db, dataset_id)
    if not include_archived:
        columns = [c for c in columns if not c.archived]
    return SchemaRead(columns=[to_wire(c) for c in columns], store_schema=_store_ref(db, dataset))


@router.put("/{dataset_id}/schema", response_model=SchemaRead)
def put_schema(
    dataset_id: uuid.UUID,
    payload: SchemaUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> SchemaRead:
    """Replace/upsert the column set.

    Keys present are upserted (re-adding an archived key un-archives it);
    keys absent are archived — never hard-deleted — so old rows and version
    snapshots stay interpretable. `order` follows list position.
    """
    dataset = _get_dataset_or_404(db, dataset_id)
    try:
        outcome = store.set_dataset_schema(db, dataset, payload.columns, user)
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    return SchemaRead(
        columns=active_wire_columns(db, dataset_id),
        store_schema=_store_ref(db, dataset, outcome.created) if outcome else None,
    )


@router.post("/{dataset_id}/schema/apply", response_model=SchemaRead)
def apply_schema(
    dataset_id: uuid.UUID,
    payload: SchemaApply,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> SchemaRead:
    """Switch a collection dataset to another entry of its store (D8 rules)."""
    dataset = _get_dataset_or_404(db, dataset_id)
    if dataset.collection_id is None:
        raise HTTPException(
            status_code=422, detail="dataset is not in a collection; set its schema with PUT"
        )
    entry = db.get(CollectionSchema, payload.store_schema_id)
    if entry is None or entry.collection_id != dataset.collection_id:
        raise HTTPException(
            status_code=422, detail="store schema does not belong to the dataset's collection"
        )
    try:
        store.apply(db, dataset, entry)
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    return SchemaRead(columns=active_wire_columns(db, dataset_id), store_schema=_store_ref(db, dataset))
