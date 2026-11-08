"""Schema inference (Phase 4, GL-117).

    POST /schema/infer   multipart file [+ records_key] [+ key]   editor+
        -> {columns, warnings, record_count}

Drafts a column schema from a data file with the same rules as
`groundline schema infer` (`groundline_schema.infer`). Creates nothing.
`columns` is in the wire format `PUT /datasets/{id}/schema` accepts.

JSON / JSONL / YAML are read with the shared record reader, as the CLI does.
CSV and XLSX values reach inference as the importer parses them: every CSV
cell is a string (an empty cell counts as null), so a CSV can only infer
text, long_text or date columns; XLSX date cells become `YYYY-MM-DD` (or an
ISO datetime) and every other cell its text, as on import.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from groundline_schema.infer import InferError, infer_columns
from groundline_schema.paths import PathError
from groundline_schema.records import (
    STRUCTURED_SUFFIXES,
    RecordProblem,
    RecordsError,
    parse_records,
)

from groundline_api.api.v1.imports import ImportProblem, problem_response
from groundline_api.config import settings
from groundline_api.deps import require_role
from groundline_api.models.user import Role, User
from groundline_api.services import importer

router = APIRouter(prefix="/schema", tags=["schema"])


def _cell(value: Any) -> Any:
    if value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time() else value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (dt.time, dt.timedelta)):
        return str(value)
    return value


def _records(filename: str, content: bytes, records_key: str | None) -> list[dict]:
    if filename.lower().endswith(STRUCTURED_SUFFIXES):
        try:
            parsed = parse_records(filename, content, records_key, settings.import_max_nodes)
        except RecordsError as exc:
            raise ImportProblem(str(exc), exc.candidates) from None
        return [r for r in parsed.records if not isinstance(r, RecordProblem)]
    try:
        source = importer.parse_upload(filename, content, None, records_key)
    except importer.ImportRequestError as exc:
        raise ImportProblem(str(exc), exc.candidates) from None
    return [
        {k: _cell(v) for k, v in row.items()}
        for row in source.rows
        if not isinstance(row, RecordProblem)
    ]


@router.post("/infer", response_model=None)
async def infer_schema(
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
    file: Annotated[UploadFile, File()],
    records_key: Annotated[str | None, Form()] = None,
    key: Annotated[str | None, Form()] = None,
) -> dict[str, Any] | JSONResponse:
    limit = settings.import_max_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than the import limit of {limit // (1024 * 1024)} MB",
        )
    try:
        records = _records(file.filename or "", content, records_key)
    except ImportProblem as exc:
        return problem_response(exc)
    try:
        drafted, warnings = infer_columns(records, key or None)
    except (InferError, PathError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    columns = []
    for column in drafted:
        comment = column.pop("_comment", None)
        if comment:
            warnings.append(f"column '{column['key']}': {comment}")
        columns.append(column)
    return {"columns": columns, "warnings": warnings, "record_count": len(records)}
