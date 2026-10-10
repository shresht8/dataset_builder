"""`dataset_rows` and `row_edits` tables (§3, §4).

    dataset_rows  id, dataset_id, data(jsonb), status, assignee,
                  source_trace_id?, row_key?, rev, updated_by, updated_at,
                  deleted_at?, deleted_by?
    row_edits     id, row_id, field, old_value, new_value, user_id, at

`rev` backs optimistic locking (§4 concurrency). `row_edits` is append-only.
Row status flows draft -> needs_review -> approved.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from groundline_api.db import Base


class RowStatus(str, enum.Enum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


# Native Postgres enum over the §4 row statuses ('draft', 'needs_review', 'approved').
row_status_enum = sa.Enum(
    RowStatus,
    name="row_status",
    values_callable=lambda enum: [member.value for member in enum],
)


class DatasetRow(Base):
    __tablename__ = "dataset_rows"
    __table_args__ = (
        sa.Index("ix_dataset_rows_dataset_id_status", "dataset_id", "status"),
        sa.Index("ix_dataset_rows_dataset_id_assignee", "dataset_id", "assignee"),
        # A key value identifies one live row per dataset (GL-3.5-12); the index
        # is what makes uniqueness race-free. Deleted rows free their key (GL-3.5-18).
        sa.Index(
            "uq_dataset_rows_row_key",
            "dataset_id",
            "row_key",
            unique=True,
            postgresql_where=sa.text("row_key IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    data: Mapped[dict] = mapped_column(JSONB(), nullable=False)
    status: Mapped[RowStatus] = mapped_column(
        row_status_enum, nullable=False, server_default=RowStatus.DRAFT.value
    )
    assignee: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_trace_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    # Mirror of data[<key column>] when the dataset has a key column (GL-3.5-12).
    row_key: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    # Optimistic-locking revision (§4): updates send the expected rev, 409 on mismatch.
    rev: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    # Soft delete (GL-3.5-18): a deleted row keeps its data and audit trail but
    # is gone from the grid, versions, sync and pull.
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class RowEdit(Base):
    """Append-only audit log: who changed which field, and when (§3)."""

    __tablename__ = "row_edits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    row_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("dataset_rows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    # JSONB so any cell value (string, number, bool, list) round-trips.
    old_value: Mapped[dict | list | str | int | bool | None] = mapped_column(
        JSONB(), nullable=True
    )
    new_value: Mapped[dict | list | str | int | bool | None] = mapped_column(
        JSONB(), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
