"""`dataset_versions` table (§3, §6).

    dataset_versions  dataset_id, version, snapshot_uri, content_hash,
                      schema_snapshot(jsonb), row_count, notes,
                      created_by, created_at

Each version embeds its own schema snapshot (§2.2) so an export is
self-describing. Versions are immutable.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from groundline_api.db import Base


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        # Also serves lookups by dataset_id (leading column of the unique index).
        sa.UniqueConstraint(
            "dataset_id", "version", name="uq_dataset_versions_dataset_id_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    snapshot_uri: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    # sha256 hex digest of the snapshot (§6).
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    schema_snapshot: Mapped[dict] = mapped_column(JSONB(), nullable=False)
    row_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    notes: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
