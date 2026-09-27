"""`datasets` and `dataset_columns` tables (§3).

    datasets         id, project_id, name, description, feature_id?, created_by
    dataset_columns  dataset_id, key, label, type, options, required, order, archived
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from groundline_api.db import Base
from groundline_schema import ColumnType

# Native Postgres enum over the six §3 column types ('text', 'long_text', ...).
column_type_enum = sa.Enum(
    ColumnType,
    name="column_type",
    values_callable=lambda enum: [member.value for member in enum],
)


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (sa.UniqueConstraint("name", name="uq_datasets_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    # Nullable for backward compatibility with pre-GL-1-5 rows; no projects
    # table exists, so this is an external identifier, not a foreign key.
    project_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    # Unique (GL-3-15): a name identifies exactly one dataset, and is used
    # unchanged as the snapshot key segment and the CLI's `name@vN` (design §6/§7).
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    feature_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class DatasetColumn(Base):
    __tablename__ = "dataset_columns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    label: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    type: Mapped[ColumnType] = mapped_column(column_type_enum, nullable=False)
    # List of allowed values for select / multi_select; null for other types.
    options: Mapped[list | None] = mapped_column(JSONB(), nullable=True)
    required: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, server_default=sa.false()
    )
    order: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    archived: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, server_default=sa.false()
    )
