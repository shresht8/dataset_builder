"""Import routes (§4, §8, GL-2-8, GL-3.5-5): two-step import with column mapping.

Accepts CSV, XLSX, JSON, JSONL/NDJSON and YAML. Uploads over IMPORT_MAX_BYTES
are refused with 413.

    POST /datasets/{id}/import/preview   multipart file [+ mapping] [+ records_key]
        Parses the upload only; persists nothing. Returns detected source
        columns (dotted paths for nested JSON/YAML), a suggested auto-mapping
        (case-insensitive source column -> schema key), a sample of the first
        20 parsed rows, the total row count, the records key used and the
        top-level keys ignored (JSON/YAML), and -- if a `mapping` form field is
        supplied -- the full per-row validation report under that mapping.

    POST /datasets/{id}/import           multipart file + mapping [+ skip] [+ fixes] [+ records_key]
        `mapping` (JSON object: source_column -> schema_key), `skip` (JSON
        array of row indices to skip, optional), `fixes` (JSON object:
        {row_index: {schema_key: value}} applied over parsed values before
        validation, optional). Valid rows are created as draft rows; invalid
        rows are not created. Every source row is accounted for as imported,
        explicitly skipped, or reported in `errors` -- never silently dropped.

`records_key` picks the list of records in a JSON/YAML object when it can't be
found automatically; the 422 that asks for it carries `records_key_candidates`.

Row index convention: 1-based, counting only rows of data (the header row is
not numbered; for JSON/JSONL/YAML it's the record's position).

Import is editor+ (§5: Editor imports; annotators do not).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from groundline_api.config import settings
from groundline_api.deps import get_db, require_role
from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.row import DatasetRow
from groundline_api.models.user import Role, User
from groundline_api.schemas.import_ import ImportCommitResponse, ImportPreviewResponse
from groundline_api.services import importer
from groundline_api.services.validation import key_column

router = APIRouter(prefix="/datasets", tags=["import"])


class ImportProblem(Exception):
    """A 422 with an optional list of records_key candidates."""

    def __init__(self, detail: str, candidates: list[str] | None) -> None:
        self.detail = detail
        self.candidates = candidates


def problem_response(exc: ImportProblem) -> JSONResponse:
    body: dict[str, Any] = {"detail": exc.detail}
    if exc.candidates is not None:
        body["records_key_candidates"] = exc.candidates
    return JSONResponse(status_code=422, content=body)


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


def _existing_keys(db: Session, dataset_id: uuid.UUID) -> set[str]:
    return set(
        db.scalars(
            select(DatasetRow.row_key).where(
                DatasetRow.dataset_id == dataset_id, DatasetRow.row_key.is_not(None)
            )
        )
    )


def _parse_json_form(raw: str | None, field: str) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail=f"{field}: invalid JSON")


async def read_upload(
    file: UploadFile, columns: list[DatasetColumn], records_key: str | None
) -> importer.ParsedSource:
    limit = settings.import_max_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than the import limit of {limit // (1024 * 1024)} MB",
        )
    try:
        return importer.parse_upload(file.filename or "", content, columns, records_key)
    except importer.ImportRequestError as exc:
        raise ImportProblem(str(exc), exc.candidates) from exc


@router.post("/{dataset_id}/import/preview", response_model=ImportPreviewResponse)
async def preview_import(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
    file: Annotated[UploadFile, File()],
    mapping: Annotated[str | None, Form()] = None,
    records_key: Annotated[str | None, Form()] = None,
) -> ImportPreviewResponse | JSONResponse:
    _get_dataset_or_404(db, dataset_id)
    columns = _columns_of(db, dataset_id)
    try:
        source = await read_upload(file, columns, records_key)
    except ImportProblem as exc:
        return problem_response(exc)

    parsed_mapping = _parse_json_form(mapping, "mapping")
    if parsed_mapping is not None:
        if not isinstance(parsed_mapping, dict):
            raise HTTPException(status_code=422, detail="mapping: expected a JSON object")
        try:
            importer.validate_mapping(parsed_mapping, columns)
        except importer.ImportRequestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    existing = _existing_keys(db, dataset_id) if key_column(columns) else set()
    sample, total, validation = importer.preview(source, columns, parsed_mapping, existing)
    return ImportPreviewResponse(
        columns=source.header,
        suggested_mapping=importer.suggest_mapping(source.header, columns),
        sample_rows=sample,
        total_rows=total,
        validation=validation,
        records_key=source.records_key,
        ignored_keys=source.ignored_keys,
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
    records_key: Annotated[str | None, Form()] = None,
) -> ImportCommitResponse | JSONResponse:
    _get_dataset_or_404(db, dataset_id)
    columns = _columns_of(db, dataset_id)
    try:
        source = await read_upload(file, columns, records_key)
    except ImportProblem as exc:
        return problem_response(exc)

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

    key_col = key_column(columns)
    existing = _existing_keys(db, dataset_id) if key_col else set()
    created, imported, skipped, errors = importer.commit(
        source, columns, parsed_mapping, set(parsed_skip), fixes_by_row, existing
    )
    # Rows are listed and versioned in created_at order; one transaction would
    # give them all the same now(), so space them out to keep the file's order.
    started = datetime.now(timezone.utc)
    for position, data in enumerate(created):
        row_key = data[key_col.key] if key_col is not None else None
        db.add(
            DatasetRow(
                dataset_id=dataset_id,
                data=data,
                updated_by=user.id,
                row_key=row_key,
                created_at=started + timedelta(microseconds=position),
            )
        )
    try:
        db.commit()
    except IntegrityError:
        # A concurrent edit or import took one of these keys after the check.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="a key in this file was just used by another change; preview again",
        ) from None

    return ImportCommitResponse(imported=imported, skipped=skipped, errors=errors)
