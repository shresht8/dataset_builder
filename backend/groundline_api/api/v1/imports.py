"""Import routes (§4, §8, GL-2-8): two-step CSV/XLSX import with column mapping.

    POST /datasets/{id}/import/preview   multipart file [+ mapping]
        Parses the upload only; persists nothing. Returns detected source
        columns, a suggested auto-mapping (case-insensitive header -> schema
        key), a sample of the first 20 parsed rows, the total row count, and
        -- if a `mapping` form field is supplied -- the full per-row
        validation report under that mapping.

    POST /datasets/{id}/import           multipart file + mapping [+ skip] [+ fixes]
        `mapping` (JSON object: source_column -> schema_key), `skip` (JSON
        array of row indices to skip, optional), `fixes` (JSON object:
        {row_index: {schema_key: value}} applied over parsed values before
        validation, optional). Valid rows are created as draft rows; invalid
        rows are not created. Every source row is accounted for as imported,
        explicitly skipped, or reported in `errors` -- never silently dropped.

Row index convention: 1-based, counting only rows of data (the header row is
not numbered).

Import is editor+ (§5: Editor imports; annotators do not).
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.deps import get_db, require_role
from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.row import DatasetRow
from groundline_api.models.user import Role, User
from groundline_api.schemas.import_ import ImportCommitResponse, ImportPreviewResponse
from groundline_api.services import importer

router = APIRouter(prefix="/datasets", tags=["import"])


def _get_dataset_or_404(db: Session, dataset_id: uuid.UUID) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


def _columns_of(db: Session, dataset_id: uuid.UUID) -> list[DatasetColumn]:
    """Schema-order columns, so per-row validation errors are deterministically ordered."""
    return list(
        db.scalars(
            select(DatasetColumn)
            .where(DatasetColumn.dataset_id == dataset_id)
            .order_by(DatasetColumn.order, DatasetColumn.key)
        )
    )


def _parse_json_form(raw: str | None, field: str) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail=f"{field}: invalid JSON")


async def _read_upload(file: UploadFile) -> importer.ParsedSource:
    content = await file.read()
    try:
        return importer.parse_upload(file.filename or "", content)
    except importer.ImportRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{dataset_id}/import/preview", response_model=ImportPreviewResponse)
async def preview_import(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
    file: Annotated[UploadFile, File()],
    mapping: Annotated[str | None, Form()] = None,
) -> ImportPreviewResponse:
    _get_dataset_or_404(db, dataset_id)
    columns = _columns_of(db, dataset_id)
    source = await _read_upload(file)

    parsed_mapping = _parse_json_form(mapping, "mapping")
    if parsed_mapping is not None:
        if not isinstance(parsed_mapping, dict):
            raise HTTPException(status_code=422, detail="mapping: expected a JSON object")
        try:
            importer.validate_mapping(parsed_mapping, columns)
        except importer.ImportRequestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    sample, total, validation = importer.preview(source, columns, parsed_mapping)
    return ImportPreviewResponse(
        columns=source.header,
        suggested_mapping=importer.suggest_mapping(source.header, columns),
        sample_rows=sample,
        total_rows=total,
        validation=validation,
    )


@router.post("/{dataset_id}/import", response_model=ImportCommitResponse)
async def commit_import(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
    file: Annotated[UploadFile, File()],
    mapping: Annotated[str, Form()],
    skip: Annotated[str | None, Form()] = None,
    fixes: Annotated[str | None, Form()] = None,
) -> ImportCommitResponse:
    _get_dataset_or_404(db, dataset_id)
    columns = _columns_of(db, dataset_id)
    source = await _read_upload(file)

    parsed_mapping = _parse_json_form(mapping, "mapping")
    if not isinstance(parsed_mapping, dict):
        raise HTTPException(status_code=422, detail="mapping: expected a JSON object")
    try:
        importer.validate_mapping(parsed_mapping, columns)
    except importer.ImportRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    parsed_skip = _parse_json_form(skip, "skip")
    if parsed_skip is None:
        parsed_skip = []
    if not isinstance(parsed_skip, list) or not all(isinstance(i, int) for i in parsed_skip):
        raise HTTPException(status_code=422, detail="skip: expected a JSON array of row indices")

    parsed_fixes = _parse_json_form(fixes, "fixes")
    if parsed_fixes is None:
        parsed_fixes = {}
    if not isinstance(parsed_fixes, dict):
        raise HTTPException(status_code=422, detail="fixes: expected a JSON object")
    try:
        fixes_by_row = {int(k): v for k, v in parsed_fixes.items()}
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="fixes: keys must be row indices")

    created, imported, skipped, errors = importer.commit(
        source, columns, parsed_mapping, set(parsed_skip), fixes_by_row
    )
    for data in created:
        db.add(DatasetRow(dataset_id=dataset_id, data=data, updated_by=user.id))
    db.commit()

    return ImportCommitResponse(imported=imported, skipped=skipped, errors=errors)
