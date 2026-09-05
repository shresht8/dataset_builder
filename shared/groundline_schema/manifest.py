"""Version snapshot manifest (§6 snapshot format).

Mirrors manifest.json stored alongside schema.json and rows.jsonl for each
published version. Used to write manifests when cutting, and to read/verify
them on `groundline pull`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Manifest(BaseModel):
    dataset: str
    version: int
    content_hash: str  # "sha256:..."
    row_count: int
    feature_id: str | None = None
    created_at: datetime
    created_by: str
    notes: str = ""
