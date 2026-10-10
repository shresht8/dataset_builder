"""Cutting a version (§6).

Steps: select rows (approved-only by default), serialise to JSONL in canonical
order, compute content_hash = sha256(snapshot), store snapshot + current schema
in object storage, assign the next integer version. Versions are immutable.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from groundline_schema import Column
from groundline_schema.manifest import Manifest
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.config import settings
from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.row import DatasetRow, RowStatus
from groundline_api.models.user import User
from groundline_api.models.version import DatasetVersion
from groundline_api.services import storage


class EmptySelectionError(Exception):
    """No rows matched the selection; a version can never be deleted, so an
    empty cut would be permanent (C3: the route turns this into a 422)."""


def _schema_columns(db: Session, dataset_id: uuid.UUID) -> list[Column]:
    """Non-archived columns, in schema order (C2)."""
    orm_columns = list(
        db.scalars(
            select(DatasetColumn)
            .where(DatasetColumn.dataset_id == dataset_id, DatasetColumn.archived.is_(False))
            .order_by(DatasetColumn.order, DatasetColumn.key)
        )
    )
    return [
        Column(
            key=c.key,
            label=c.label,
            type=c.type,
            options=c.options,
            required=c.required,
            order=c.order,
            archived=c.archived,
            json_schema=c.json_schema,
            is_key=c.is_key,
        )
        for c in orm_columns
    ]


def _serialise_line(row: DatasetRow, allowed_keys: set[str]) -> bytes:
    """One canonical rows.jsonl line (C2): sorted keys, compact separators."""
    data = {key: value for key, value in row.data.items() if key in allowed_keys}
    obj = {"id": str(row.id), "status": row.status.value, "data": data}
    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    return line.encode("utf-8")


def cut_version(
    db: Session,
    dataset_id: uuid.UUID,
    user: User,
    notes: str | None,
    include_unapproved: bool = False,
) -> DatasetVersion:
    # Row lock on the dataset (C2): serialises concurrent cuts before either
    # computes the next version number or writes any object.
    dataset = db.execute(
        select(Dataset).where(Dataset.id == dataset_id).with_for_update()
    ).scalar_one()

    columns = _schema_columns(db, dataset_id)
    allowed_keys = {c.key for c in columns}

    row_stmt = select(DatasetRow).where(
        DatasetRow.dataset_id == dataset_id, DatasetRow.deleted_at.is_(None)
    )
    if not include_unapproved:
        row_stmt = row_stmt.where(DatasetRow.status == RowStatus.APPROVED)
    row_stmt = row_stmt.order_by(DatasetRow.created_at, DatasetRow.id)
    rows = list(db.scalars(row_stmt))

    if not rows:
        raise EmptySelectionError()

    rows_bytes = b"".join(_serialise_line(row, allowed_keys) for row in rows)
    content_hash = hashlib.sha256(rows_bytes).hexdigest()

    schema_obj = {"columns": [c.model_dump(mode="json") for c in columns]}
    schema_bytes = json.dumps(schema_obj, ensure_ascii=False).encode("utf-8")

    next_version = (
        db.scalar(
            select(sa.func.max(DatasetVersion.version)).where(
                DatasetVersion.dataset_id == dataset_id
            )
        )
        or 0
    ) + 1

    manifest = Manifest(
        dataset=dataset.name,
        version=next_version,
        content_hash=f"sha256:{content_hash}",
        row_count=len(rows),
        feature_id=dataset.feature_id,
        created_at=datetime.now(timezone.utc),
        created_by=user.email,
        notes=notes or "",
    )
    manifest_bytes = manifest.model_dump_json().encode("utf-8")

    prefix = f"datasets/{dataset.name}/v{next_version}/"
    storage.put_snapshot(
        settings.storage_bucket,
        prefix,
        {
            "rows.jsonl": rows_bytes,
            "schema.json": schema_bytes,
            "manifest.json": manifest_bytes,
        },
    )

    version = DatasetVersion(
        dataset_id=dataset_id,
        version=next_version,
        snapshot_uri=f"s3://{settings.storage_bucket}/{prefix}",
        content_hash=content_hash,
        schema_snapshot=schema_obj,
        row_count=len(rows),
        notes=notes,
        created_by=user.id,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version
