"""Version cut/list/diff payloads (§8).

Reuses `groundline_schema.Manifest` for snapshot metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class VersionCut(BaseModel):
    notes: str | None = None
    include_unapproved: bool = False


class VersionRead(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    version: int
    content_hash: str  # "sha256:<hex>" (C2), unlike the DB column
    row_count: int
    notes: str | None
    created_by: uuid.UUID | None
    # UI can't resolve user ids: GET /v1/users is admin-only (C3).
    created_by_email: str | None
    created_at: datetime


# TODO GL-3-6: VersionDiff
