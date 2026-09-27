"""Version routes (§8). Cutting is Editor-only (§5).

    GET  /datasets/{id}/versions
    POST /datasets/{id}/versions              cut
    GET  /datasets/{id}/versions/{v}?format=jsonl|json|yaml
    GET  /datasets/{id}/versions/diff?from=&to=
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from groundline_api.deps import get_db, require_role
from groundline_api.models.dataset import Dataset
from groundline_api.models.user import Role, User
from groundline_api.schemas.version import VersionCut, VersionRead
from groundline_api.services.versioning import EmptySelectionError, cut_version

router = APIRouter(prefix="/datasets", tags=["versions"])


def _get_dataset_or_404(db: Session, dataset_id: uuid.UUID) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


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
    return VersionRead(
        id=version.id,
        dataset_id=version.dataset_id,
        version=version.version,
        content_hash=f"sha256:{version.content_hash}",
        row_count=version.row_count,
        notes=version.notes,
        created_by=version.created_by,
        created_by_email=user.email,
        created_at=version.created_at,
    )


# TODO GL-3-3: list_versions, get_version (export)
# TODO GL-3-6: diff_versions
