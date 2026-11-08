"""`datasets` and `dataset_columns` tables (§3).

    datasets         id, project_id, name, description, feature_id?, created_by,
                     collection_id?, folder_id?, store_schema_id? (Phase 4)
    dataset_columns  dataset_id, key, label, type, options, required, order, archived,
                     json_schema?, is_key
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from groundline_schema import ColumnType
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from groundline_api.db import Base

# Native Postgres enum over the §3 column types ('text', 'long_text', ...).
column_type_enum = sa.Enum(
    ColumnType,
    name="column_type",
    values_callable=lambda enum: [member.value for member in enum],
)


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        # A name is unique within its parent (D2, C1): among unfiled datasets,
        # or within one folder (or the root) of one collection.
        sa.Index(
            "uq_datasets_unfiled_name",
            "name",
            unique=True,
            postgresql_where=sa.text("collection_id IS NULL"),
        ),
        sa.Index(
            "uq_datasets_collection_folder_name",
            "collection_id",
            "folder_id",
            "name",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=sa.text("collection_id IS NOT NULL"),
        ),
        sa.Index("ix_datasets_collection_folder", "collection_id", "folder_id"),
        sa.Index("ix_datasets_store_schema_id", "store_schema_id"),
        sa.CheckConstraint(
            "folder_id IS NULL OR collection_id IS NOT NULL",
            name="ck_datasets_folder_needs_collection",
        ),
        # A dataset in a collection always uses exactly one store entry (D5).
        sa.CheckConstraint(
            "(collection_id IS NULL) = (store_schema_id IS NULL)",
            name="ck_datasets_store_schema_iff_collection",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    # Nullable for backward compatibility with pre-GL-1-5 rows; no projects
    # table exists, so this is an external identifier, not a foreign key.
    project_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    # Unique within its parent (Phase 4, D2); addressed by path
    # (`collection/folder/…/name`, or the bare name when unfiled).
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
    # Phase 4 (D1): NULL collection_id means an unfiled dataset. Folder/collection
    # consistency is enforced by the API, not by a foreign key.
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("collections.id", ondelete="RESTRICT"),
        nullable=True,
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("folders.id", ondelete="RESTRICT"),
        nullable=True,
    )
    store_schema_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("collection_schemas.id", ondelete="RESTRICT"),
        nullable=True,
    )


class DatasetColumn(Base):
    __tablename__ = "dataset_columns"
    __table_args__ = (
        # At most one active key column per dataset (GL-3.5-12).
        sa.Index(
            "uq_dataset_columns_one_key",
            "dataset_id",
            unique=True,
            postgresql_where=sa.text("is_key AND NOT archived"),
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
    # Optional JSON Schema constraining a `json` column's values; null otherwise.
    json_schema: Mapped[dict | None] = mapped_column(JSONB(), nullable=True)
    # The text column whose value identifies a row (CLI sync matches on it).
    is_key: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, server_default=sa.false()
    )
