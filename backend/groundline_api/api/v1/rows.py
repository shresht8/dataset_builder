"""Row routes (§8). Optimistic locking via If-Match on PATCH (§4).

    GET   /datasets/{id}/rows?status=&assignee=&q=   assignee accepts 'me' or a user id
    POST  /datasets/{id}/rows
    PATCH /datasets/{id}/rows/{rid}   If-Match: <rev>   data/status/assignee (§4, §8)
    GET/POST /datasets/{id}/rows/{rid}/comments   row comment thread (§4, GL-2-4)
    GET   /datasets/{id}/rows/{rid}/edits         append-only edit history (§3, GL-2-4)

When the dataset has a key column, create/patch keep `row_key` in sync with
its value; a value another row already uses is a 409, and changing an
existing row's key value needs editor+ (GL-3.5-13).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from groundline_schema import ColumnType
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from groundline_api.deps import get_db, has_role, require_role
from groundline_api.models.comment import RowComment
from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.row import DatasetRow, RowEdit, RowStatus
from groundline_api.models.user import Role, User
from groundline_api.schemas.comment import CommentCreate, CommentRead
from groundline_api.schemas.row import RowCreate, RowEditRead, RowPatch, RowRead
from groundline_api.services.validation import key_column, validate_row_data

router = APIRouter(prefix="/datasets", tags=["rows"])

_TEXT_SEARCH_TYPES = {ColumnType.TEXT, ColumnType.LONG_TEXT}


def _get_dataset_or_404(db: Session, dataset_id: uuid.UUID) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


def _columns_of(db: Session, dataset_id: uuid.UUID) -> list[DatasetColumn]:
    return list(
        db.scalars(
            select(DatasetColumn).where(DatasetColumn.dataset_id == dataset_id)
        )
    )


def _ensure_key_free(
    db: Session, dataset_id: uuid.UUID, value: str, row_id: uuid.UUID | None = None
) -> None:
    """409 if another row of the dataset already uses this key value."""
    stmt = select(DatasetRow.id).where(
        DatasetRow.dataset_id == dataset_id, DatasetRow.row_key == value
    )
    if row_id is not None:
        stmt = stmt.where(DatasetRow.id != row_id)
    other = db.scalar(stmt)
    if other is not None:
        raise HTTPException(status_code=409, detail=f"key '{value}' already exists (row {other})")


def _commit_keyed(db: Session, value: str | None) -> None:
    """Commit; a concurrent writer taking the same key surfaces as a 409."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if value is None:
            raise
        raise HTTPException(status_code=409, detail=f"key '{value}' already exists") from None


@router.get("/{dataset_id}/rows", response_model=list[RowRead])
def list_rows(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.VIEWER))],
    status: RowStatus | None = None,
    assignee: str | None = None,
    q: str | None = None,
) -> list[DatasetRow]:
    _get_dataset_or_404(db, dataset_id)
    stmt = select(DatasetRow).where(DatasetRow.dataset_id == dataset_id)
    if status is not None:
        stmt = stmt.where(DatasetRow.status == status)
    if assignee is not None:
        if assignee == "me":
            assignee_id = user.id
        else:
            try:
                assignee_id = uuid.UUID(assignee)
            except ValueError:
                raise HTTPException(
                    status_code=422, detail="assignee must be 'me' or a user id"
                )
        stmt = stmt.where(DatasetRow.assignee == assignee_id)
    if q:
        text_keys = [
            col.key for col in _columns_of(db, dataset_id) if col.type in _TEXT_SEARCH_TYPES
        ]
        if not text_keys:
            return []
        stmt = stmt.where(
            sa.or_(*[DatasetRow.data[key].astext.ilike(f"%{q}%") for key in text_keys])
        )
    return list(db.scalars(stmt.order_by(DatasetRow.created_at)))


@router.post("/{dataset_id}/rows", response_model=RowRead, status_code=201)
def create_row(
    dataset_id: uuid.UUID,
    payload: RowCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.ANNOTATOR))],
) -> DatasetRow:
    _get_dataset_or_404(db, dataset_id)
    columns = _columns_of(db, dataset_id)
    validate_row_data(payload.data, columns)
    key_col = key_column(columns)
    row_key = payload.data[key_col.key] if key_col is not None else None
    if row_key is not None:
        _ensure_key_free(db, dataset_id, row_key)
    row = DatasetRow(
        dataset_id=dataset_id, data=payload.data, updated_by=user.id, row_key=row_key
    )
    db.add(row)
    _commit_keyed(db, row_key)
    db.refresh(row)
    return row


def _get_row_or_404(db: Session, dataset_id: uuid.UUID, row_id: uuid.UUID) -> DatasetRow:
    row = db.get(DatasetRow, row_id)
    if row is None or row.dataset_id != dataset_id:
        raise HTTPException(status_code=404, detail="row not found")
    return row


@router.patch("/{dataset_id}/rows/{row_id}", response_model=RowRead)
def patch_row(
    dataset_id: uuid.UUID,
    row_id: uuid.UUID,
    payload: RowPatch,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.ANNOTATOR))],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> DatasetRow | JSONResponse:
    """Optimistic-locking update of data, status, and assignee (§4, §8)."""
    _get_dataset_or_404(db, dataset_id)
    row = _get_row_or_404(db, dataset_id, row_id)

    if if_match is None:
        raise HTTPException(status_code=428, detail="If-Match header required")
    try:
        expected_rev = int(if_match)
    except ValueError:
        raise HTTPException(status_code=400, detail="If-Match must be an integer rev")

    if row.rev != expected_rev:
        return JSONResponse(
            status_code=409,
            content=jsonable_encoder(RowRead.model_validate(row)),
        )

    fields_set = payload.model_fields_set
    changes: list[tuple[str, Any, Any]] = []
    claimed_key: str | None = None  # a new key value this patch takes, if any

    if payload.data is not None:
        merged_data = {**row.data, **payload.data}
        columns = _columns_of(db, dataset_id)
        validate_row_data(merged_data, columns)
        key_col = key_column(columns)
        if key_col is not None and merged_data.get(key_col.key) != row.data.get(key_col.key):
            if not has_role(user, Role.EDITOR):
                raise HTTPException(
                    status_code=403, detail="changing the key column requires editor role"
                )
            claimed_key = merged_data[key_col.key]
            _ensure_key_free(db, dataset_id, claimed_key, row.id)
        if key_col is not None:
            row.row_key = merged_data[key_col.key]
        for key, new_value in payload.data.items():
            old_value = row.data.get(key)
            if old_value != new_value:
                changes.append((key, old_value, new_value))
        row.data = merged_data

    if payload.status is not None:
        if row.status != payload.status:
            changes.append(("status", row.status.value, payload.status.value))
        row.status = payload.status

    if "assignee" in fields_set:
        if payload.assignee is not None and db.get(User, payload.assignee) is None:
            raise HTTPException(status_code=422, detail="assignee: user not found")
        if row.assignee != payload.assignee:
            old_assignee = str(row.assignee) if row.assignee is not None else None
            new_assignee = str(payload.assignee) if payload.assignee is not None else None
            changes.append(("assignee", old_assignee, new_assignee))
        row.assignee = payload.assignee

    row.rev = row.rev + 1
    row.updated_by = user.id
    for field, old_value, new_value in changes:
        db.add(
            RowEdit(
                row_id=row.id,
                field=field,
                old_value=old_value,
                new_value=new_value,
                user_id=user.id,
            )
        )
    _commit_keyed(db, claimed_key)
    db.refresh(row)
    return row


@router.get("/{dataset_id}/rows/{row_id}/comments", response_model=list[CommentRead])
def list_comments(
    dataset_id: uuid.UUID,
    row_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> list[RowComment]:
    _get_dataset_or_404(db, dataset_id)
    _get_row_or_404(db, dataset_id, row_id)
    stmt = (
        select(RowComment)
        .where(RowComment.row_id == row_id)
        .order_by(RowComment.created_at)
    )
    return list(db.scalars(stmt))


@router.post(
    "/{dataset_id}/rows/{row_id}/comments", response_model=CommentRead, status_code=201
)
def create_comment(
    dataset_id: uuid.UUID,
    row_id: uuid.UUID,
    payload: CommentCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.ANNOTATOR))],
) -> RowComment:
    _get_dataset_or_404(db, dataset_id)
    _get_row_or_404(db, dataset_id, row_id)
    comment = RowComment(row_id=row_id, user_id=user.id, body=payload.body)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment


@router.get("/{dataset_id}/rows/{row_id}/edits", response_model=list[RowEditRead])
def list_edits(
    dataset_id: uuid.UUID,
    row_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> list[RowEdit]:
    _get_dataset_or_404(db, dataset_id)
    _get_row_or_404(db, dataset_id, row_id)
    stmt = select(RowEdit).where(RowEdit.row_id == row_id).order_by(RowEdit.at)
    return list(db.scalars(stmt))
