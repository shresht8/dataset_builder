"""Schema inference (Phase 4, GL-117).

    POST /schema/infer   multipart file [+ records_key] [+ key] [+ delimiter]   editor+
        -> {columns, warnings, record_count, delimiter}

`delimiter` is CSV only, as on import: given, or detected and returned.

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
from groundline_schema.infer import NO_KEY, InferError, humanise, infer_columns
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

WEB_NO_KEY = (
    "No key column yet. A key gives each row a unique name, such as an id, so later"
    " imports can tell new rows from existing ones. Tick Key column on a column whose"
    " values are all different and never empty."
)
HEADER_ONLY = (
    "This file has a header but no rows, so every column is drafted as text."
    " Set the types before creating."
)


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


def _records(
    filename: str, content: bytes, records_key: str | None, delimiter: str | None
) -> tuple[list[dict], str | None, list[str]]:
    """The file's records, the CSV delimiter used (None for other formats), and the
    CSV/XLSX column names (empty for other formats)."""
    if filename.lower().endswith(STRUCTURED_SUFFIXES):
        try:
            parsed = parse_records(filename, content, records_key, settings.import_max_nodes)
        except RecordsError as exc:
            raise ImportProblem(str(exc), exc.candidates) from None
        return [r for r in parsed.records if not isinstance(r, RecordProblem)], None, []
    try:
        source = importer.parse_upload(filename, content, None, records_key, delimiter)
    except importer.ImportRequestError as exc:
        raise ImportProblem(str(exc), exc.candidates) from None
    # Blank lines are skipped, as import skips them (GL-146).
    records = [
        {k: _cell(v) for k, v in row.items()}
        for row in source.rows
        if not isinstance(row, RecordProblem) and not importer.is_blank_line(source, row)
    ]
    # Nameless header cells after the last name (GL-147): data under them means
    # a missing name, or title rows above the header; otherwise drop them.
    named = [name for name in source.header if name.strip()]
    blank = {name for name in source.header if not name.strip()}
    if blank:
        if any(record.get(name) is not None for record in records for name in blank):
            last = max(i for i, name in enumerate(source.header) if name.strip())
            raise ImportProblem(importer.unnamed_column(last + 2), None)
        records = [{k: v for k, v in record.items() if k not in blank} for record in records]
    return records, source.delimiter, named


@router.post("/infer", response_model=None)
async def infer_schema(
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
    file: Annotated[UploadFile, File()],
    records_key: Annotated[str | None, Form()] = None,
    key: Annotated[str | None, Form()] = None,
    delimiter: Annotated[str | None, Form()] = None,
) -> dict[str, Any] | JSONResponse:
    limit = settings.import_max_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than the import limit of {limit // (1024 * 1024)} MB",
        )
    try:
        records, used_delimiter, header = _records(
            file.filename or "", content, records_key, delimiter
        )
    except ImportProblem as exc:
        return problem_response(exc)
    if not records and header:
        # Header only (GL-147): text columns from the header, so an empty
        # dataset can still be created.
        return {
            "columns": [
                {"key": name, "label": humanise(name), "type": "text", "required": False}
                for name in header
            ],
            "warnings": [HEADER_ONLY, WEB_NO_KEY],
            "record_count": 0,
            "delimiter": used_delimiter,
        }
    try:
        drafted, warnings = infer_columns(records, key or None)
    except (InferError, PathError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    # The CLI's NO_KEY names `--key`; the web picks the key in the schema step (GL-148).
    warnings = [WEB_NO_KEY if w == NO_KEY else w for w in warnings]
    columns = []
    for column in drafted:
        comment = column.pop("_comment", None)
        if comment:
            warnings.append(f"column '{column['key']}': {comment}")
        columns.append(column)
    return {
        "columns": columns,
        "warnings": warnings,
        "record_count": len(records),
        "delimiter": used_delimiter,
    }
