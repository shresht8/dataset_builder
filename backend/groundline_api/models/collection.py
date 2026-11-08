"""`collections`, `folders` and `collection_schemas` tables (Phase 4, D1/D2/D5).

    collections         id, name (unique), description, created_by, created_at
    folders             id, collection_id, parent_id?, name, created_by, created_at
    collection_schemas  id, collection_id, name, columns(jsonb), content_hash,
                        created_by, created_at, updated_at

A collection groups datasets into a tree of folders and owns a schema store:
named column sets its datasets use. A dataset in a collection references one
store entry (`datasets.store_schema_id`) and its columns equal that entry's.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from groundline_api.db import Base


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (sa.UniqueConstraint("name", name="uq_collections_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class Folder(Base):
    __tablename__ = "folders"
    __table_args__ = (
        # A root folder has parent_id NULL; NULLS NOT DISTINCT makes two root
        # folders with the same name a clash too (C1).
        sa.UniqueConstraint(
            "collection_id",
            "parent_id",
            "name",
            name="uq_folders_collection_parent_name",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    # NO ACTION rather than RESTRICT: deleting a collection cascades to all of
    # its folders in one statement, and NO ACTION checks the parent reference
    # at the end of that statement. A direct delete of a folder that still has
    # subfolders is refused either way.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("folders.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class CollectionSchema(Base):
    __tablename__ = "collection_schemas"
    __table_args__ = (
        sa.UniqueConstraint("collection_id", "name", name="uq_collection_schemas_collection_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    # Shared `Column` wire dicts, in order (the C3 canonical form).
    columns: Mapped[list] = mapped_column(JSONB(), nullable=False)
    # groundline_schema.schema_hash(columns): "sha256:<hex>" (C3).
    content_hash: Mapped[str] = mapped_column(sa.String(71), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
