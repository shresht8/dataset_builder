"""Collection bundle routes (Phase 4, GL-122: D12, D13, C4).

    GET  /collections/{cid}/export?source=draft|latest   -> application/zip   viewer+
    POST /collections/import/preview   multipart file (zip) [+ name]          editor+
    POST /collections/import           multipart file (zip) [+ name]          editor+

Export: `draft` (default) is the live, non-deleted rows; `latest` is each
dataset's latest version, and a dataset with no version is listed under
`skipped` in collection.yaml (and in the `X-Groundline-Skipped` header, a
JSON list of paths).

Import always creates a new collection, in one step and one transaction: the
bundle already carries every schema. `name` overrides the bundle's name. The
zip is capped at IMPORT_MAX_BYTES (413) and its contents at
BUNDLE_MAX_UNCOMPRESSED_BYTES; an unsafe or malformed bundle is 422. Preview
reports everything (including `name_taken`) and creates nothing; commit is 409
when the name is taken, and 422 (with the preview report) on any error.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from groundline_schema.bundle import BundleError, read_zip, write_zip
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from groundline_api.api.v1.collections import get_collection_or_404
from groundline_api.config import settings
from groundline_api.deps import get_db, require_role
from groundline_api.models.user import Role, User
from groundline_api.schemas.collection import CollectionRead
from groundline_api.services import bundles
from groundline_api.services.paths import NAME_RE

router = APIRouter(prefix="/collections", tags=["bundles"])


@router.get("/{collection_id}/export")
def export_collection(
    collection_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
    source: Literal["draft", "latest"] = Query("draft"),
) -> Response:
    collection = get_collection_or_404(db, collection_id)
    bundle = bundles.export_bundle(db, collection, source)
    try:
        content = write_zip(bundle)
    except BundleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{collection.name}.zip"',
            "X-Groundline-Skipped": json.dumps([s["path"] for s in bundle.skipped]),
        },
    )


async def _read(file: UploadFile, name: str | None) -> bundles.Bundle:
    limit = settings.import_max_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than the import limit of {limit // (1024 * 1024)} MB",
        )
    try:
        bundle = read_zip(content, settings.bundle_max_uncompressed_bytes)
    except BundleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if name:
        if not NAME_RE.match(name):
            raise HTTPException(status_code=422, detail=f"invalid collection name '{name}'")
        bundle.name = name
    return bundle


@router.post("/import/preview")
async def preview_import(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.EDITOR))],
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> dict:
    return bundles.plan(db, await _read(file, name)).report()


@router.post("/import", status_code=201, response_model=None)
async def commit_import(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> CollectionRead | JSONResponse:
    bundle = await _read(file, name)
    import_plan = bundles.plan(db, bundle)
    if import_plan.name_taken:
        raise HTTPException(
            status_code=409,
            detail=f"collection '{bundle.name}' already exists — pass a new name",
        )
    if not import_plan.ok:
        return JSONResponse(
            status_code=422,
            content={"detail": "the bundle has errors; nothing was imported", **import_plan.report()},
        )
    try:
        collection = bundles.commit(db, import_plan, user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"collection '{bundle.name}' already exists — pass a new name",
        ) from None
    except HTTPException:
        db.rollback()
        raise
    db.refresh(collection)
    read = CollectionRead.model_validate(collection)
    read.dataset_count = len(import_plan.datasets)
    return read
