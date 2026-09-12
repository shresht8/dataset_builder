"""Row routes (§8). Optimistic locking via If-Match on PATCH (§4).

    GET   /datasets/{id}/rows?status=&assignee=&q=   assignee accepts 'me' or a user id
    POST  /datasets/{id}/rows
    PATCH /datasets/{id}/rows/{rid}   If-Match: <rev>   data/status/assignee (§4, §8)
    GET/POST /datasets/{id}/rows/{rid}/comments   row comment thread (§4, GL-2-4)
    GET   /datasets/{id}/rows/{rid}/edits         append-only edit history (§3, GL-2-4)
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
from sqlalchemy.orm import Session

from groundline_api.deps import get_db, require_role
from groundline_api.models.comment import RowComment
from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.row import DatasetRow, RowEdit, RowStatus
from groundline_api.models.user import Role, User
from groundline_api.schemas.comment import CommentCreate, CommentRead
from groundline_api.schemas.row import RowCreate, RowEditRead, RowPatch, RowRead

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


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


def _type_error(col: DatasetColumn, value: Any) -> str | None:
    """Return an error fragment if `value` doesn't match `col.type`, else None."""
    if col.type in (ColumnType.TEXT, ColumnType.LONG_TEXT):
        if not isinstance(value, str):
            return f"expected text, got {type(value).__name__}"
    elif col.type == ColumnType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"expected a number, got {type(value).__name__}"
    elif col.type == ColumnType.BOOLEAN:
        if not isinstance(value, bool):
            return f"expected a boolean, got {type(value).__name__}"
    elif col.type == ColumnType.SELECT:
        if not isinstance(value, str):
            return f"expected one of {col.options}, got {type(value).__name__}"
        if value not in (col.options or []):
            return f"'{value}' is not one of {col.options}"
    elif col.type == ColumnType.MULTI_SELECT:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return f"expected a list of strings from {col.options}"
        bad = [v for v in value if v not in (col.options or [])]
        if bad:
            return f"{bad} not in {col.options}"
    return None


def _validate_row_data(data: dict[str, Any], columns: list[DatasetColumn]) -> None:
    """Validate `data` against the dataset's column schema (§3, §8).

    Archived columns are accepted read-only: their values are neither
    required nor type-checked. Fields with no matching column pass through
    unvalidated (a dataset may have no schema defined yet).
    """
    for col in columns:
        if col.archived:
            continue
        value = data.get(col.key)
        if _is_empty(value):
            if col.required:
                raise HTTPException(
                    status_code=422, detail=f"column '{col.key}': required"
                )
            continue
        error = _type_error(col, value)
        if error:
            raise HTTPException(
                status_code=422, detail=f"column '{col.key}': {error}"
            )


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
    _validate_row_data(payload.data, _columns_of(db, dataset_id))
    row = DatasetRow(dataset_id=dataset_id, data=payload.data, updated_by=user.id)
    db.add(row)
    db.commit()
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

    if payload.data is not None:
        merged_data = {**row.data, **payload.data}
        _validate_row_data(merged_data, _columns_of(db, dataset_id))
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
    db.commit()
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
