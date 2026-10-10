"""Row sync route (GL-3.5-15): push a JSON/JSONL/YAML file matched on the key column.

    POST /datasets/{id}/sync   multipart file [+ records_key] [+ revs] [+ bases]
                               [+ force] [+ dry_run] [+ ignore_unknown]

`revs` / `bases` are JSON objects `{key value: rev}` / `{key value: row_hash}`
from the CLI's last pull or push. The file is parsed exactly as import
(including IMPORT_MAX_BYTES), and each flattened path maps to the column with
the same key. Always 200 when the request is well-formed -- applied, dry run
and refused alike; `applied` says which, and the CLI picks its exit code.
See `services/sync.py` for the semantics. Editor+ (same as import).
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from groundline_schema.records import STRUCTURED_SUFFIXES
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.api.v1.imports import ImportProblem, problem_response, read_upload
from groundline_api.deps import get_db, require_role
from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.user import Role, User
from groundline_api.services.sync import SyncOptions, SyncRequestError, sync

router = APIRouter(prefix="/datasets", tags=["sync"])


def _json_object(raw: str | None, field: str) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail=f"{field}: invalid JSON") from None
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail=f"{field}: expected a JSON object")
    return value


@router.post("/{dataset_id}/sync", response_model=None)
async def sync_rows(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
    file: Annotated[UploadFile, File()],
    records_key: Annotated[str | None, Form()] = None,
    revs: Annotated[str | None, Form()] = None,
    bases: Annotated[str | None, Form()] = None,
    force: Annotated[bool, Form()] = False,
    dry_run: Annotated[bool, Form()] = False,
    ignore_unknown: Annotated[bool, Form()] = False,
) -> dict[str, Any] | JSONResponse:
    if db.get(Dataset, dataset_id) is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    if not (file.filename or "").lower().endswith(STRUCTURED_SUFFIXES):
        raise HTTPException(
            status_code=422, detail="sync accepts .json, .jsonl, .ndjson, .yaml or .yml files"
        )
    options = SyncOptions(
        revs=_json_object(revs, "revs"),
        bases=_json_object(bases, "bases"),
        force=force,
        dry_run=dry_run,
        ignore_unknown=ignore_unknown,
    )
    columns = list(
        db.scalars(
            select(DatasetColumn)
            .where(DatasetColumn.dataset_id == dataset_id)
            .order_by(DatasetColumn.order, DatasetColumn.key)
        )
    )
    try:
        source = await read_upload(file, columns, records_key)
    except ImportProblem as exc:
        return problem_response(exc)
    try:
        return sync(db, dataset_id, user, source, columns, options)
    except SyncRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
