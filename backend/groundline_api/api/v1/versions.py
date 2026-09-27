"""Version routes (§8). Cutting is Editor-only (§5).

    GET  /datasets/{id}/versions
    POST /datasets/{id}/versions              cut
    GET  /datasets/{id}/versions/{v}/manifest
    GET  /datasets/{id}/versions/{v}/jsonschema
    GET  /datasets/{id}/versions/{v}?format=jsonl|json|yaml
    GET  /datasets/{id}/versions/diff?from=&to=
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from groundline_schema import Column
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.config import settings
from groundline_api.deps import get_db, require_role
from groundline_api.models.dataset import Dataset
from groundline_api.models.user import Role, User
from groundline_api.models.version import DatasetVersion
from groundline_api.schemas.version import VersionCut, VersionRead
from groundline_api.services import export, storage
from groundline_api.services.versioning import EmptySelectionError, cut_version

router = APIRouter(prefix="/datasets", tags=["versions"])


def _get_dataset_or_404(db: Session, dataset_id: uuid.UUID) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


def _get_version_or_404(db: Session, dataset_id: uuid.UUID, v: int) -> DatasetVersion:
    version = db.execute(
        select(DatasetVersion).where(
            DatasetVersion.dataset_id == dataset_id, DatasetVersion.version == v
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    return version


def _to_read(version: DatasetVersion, created_by_email: str | None) -> VersionRead:
    return VersionRead(
        id=version.id,
        dataset_id=version.dataset_id,
        version=version.version,
        content_hash=f"sha256:{version.content_hash}",
        row_count=version.row_count,
        notes=version.notes,
        created_by=version.created_by,
        created_by_email=created_by_email,
        created_at=version.created_at,
    )


@router.post("/{dataset_id}/versions", response_model=VersionRead, status_code=201)
def cut(
    dataset_id: uuid.UUID,
    payload: VersionCut,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(Role.EDITOR))],
) -> VersionRead:
    _get_dataset_or_404(db, dataset_id)
    try:
        version = cut_version(
            db,
            dataset_id,
            user,
            notes=payload.notes,
            include_unapproved=payload.include_unapproved,
        )
    except EmptySelectionError:
        raise HTTPException(status_code=422, detail="no rows selected for this version")
    return _to_read(version, user.email)


@router.get("/{dataset_id}/versions", response_model=list[VersionRead])
def list_versions(
    dataset_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> list[VersionRead]:
    _get_dataset_or_404(db, dataset_id)
    rows = db.execute(
        select(DatasetVersion, User.email)
        .outerjoin(User, DatasetVersion.created_by == User.id)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version.desc())
    ).all()
    return [_to_read(version, email) for version, email in rows]


# NOTE: GL-3-6 must declare GET /{dataset_id}/versions/diff above this route,
# or "diff" matches the {v}: int path converter and 422s (C3 route order).
@router.get("/{dataset_id}/versions/{v}/manifest")
def get_manifest(
    dataset_id: uuid.UUID,
    v: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> Response:
    dataset = _get_dataset_or_404(db, dataset_id)
    _get_version_or_404(db, dataset_id, v)
    key = f"datasets/{dataset.name}/v{v}/manifest.json"
    manifest_bytes = storage.get_object(settings.storage_bucket, key)
    return Response(content=manifest_bytes, media_type="application/json")


@router.get("/{dataset_id}/versions/{v}/jsonschema")
def get_jsonschema(
    dataset_id: uuid.UUID,
    v: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
) -> Response:
    _get_dataset_or_404(db, dataset_id)
    version = _get_version_or_404(db, dataset_id, v)
    columns = [Column(**c) for c in version.schema_snapshot["columns"]]
    schema = export.sidecar_schema(columns)
    return Response(
        content=json.dumps(schema).encode("utf-8"), media_type="application/schema+json"
    )


@router.get("/{dataset_id}/versions/{v}")
def export_version(
    dataset_id: uuid.UUID,
    v: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_role(Role.VIEWER))],
    format: Literal["jsonl", "json", "yaml"] = Query("jsonl"),
) -> Response:
    """Export a version's rows (C3). Always reads from object storage."""
    dataset = _get_dataset_or_404(db, dataset_id)
    _get_version_or_404(db, dataset_id, v)
    prefix = f"datasets/{dataset.name}/v{v}/"
    rows_bytes = storage.get_object(settings.storage_bucket, prefix + "rows.jsonl")
    if format == "jsonl":
        return Response(content=rows_bytes, media_type="application/x-ndjson")

    manifest = json.loads(storage.get_object(settings.storage_bucket, prefix + "manifest.json"))
    rows = export.parse_rows_jsonl(rows_bytes)
    if format == "json":
        return Response(content=export.export_json(manifest, rows), media_type="application/json")
    return Response(content=export.export_yaml(manifest, rows), media_type="application/yaml")


# TODO GL-3-6: diff_versions
