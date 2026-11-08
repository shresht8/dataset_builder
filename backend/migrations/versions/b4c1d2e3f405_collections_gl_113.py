"""collections, folders, schema store (GL-113, Phase 4)

Adds `collections`, `folders` and `collection_schemas`, and gives `datasets`
`collection_id` / `folder_id` / `store_schema_id`. Dataset names become unique
within their parent (D2, C1) instead of across the app: `uq_datasets_name` is
replaced by a partial unique index over unfiled datasets plus a unique index
over (collection_id, folder_id, name) for collection datasets. Every existing
dataset stays unfiled.

Downgrade restores `uq_datasets_name`, and refuses (never auto-renames) when
two datasets share a name, as GL-3-15's migration does.

Revision ID: b4c1d2e3f405
Revises: 724089577f90
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'b4c1d2e3f405'
down_revision = '724089577f90'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'collections',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_collections_name'),
    )
    op.create_table(
        'folders',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('collection_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('parent_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['collection_id'], ['collections.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_id'], ['folders.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'collection_id', 'parent_id', 'name',
            name='uq_folders_collection_parent_name',
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        'collection_schemas',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('collection_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('columns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('content_hash', sa.String(length=71), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['collection_id'], ['collections.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('collection_id', 'name', name='uq_collection_schemas_collection_name'),
    )

    op.add_column('datasets', sa.Column('collection_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('datasets', sa.Column('folder_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('datasets', sa.Column('store_schema_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'datasets_collection_id_fkey', 'datasets', 'collections', ['collection_id'], ['id'],
        ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'datasets_folder_id_fkey', 'datasets', 'folders', ['folder_id'], ['id'],
        ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'datasets_store_schema_id_fkey', 'datasets', 'collection_schemas', ['store_schema_id'], ['id'],
        ondelete='RESTRICT',
    )
    op.create_check_constraint(
        'ck_datasets_folder_needs_collection', 'datasets',
        'folder_id IS NULL OR collection_id IS NOT NULL',
    )
    op.create_check_constraint(
        'ck_datasets_store_schema_iff_collection', 'datasets',
        '(collection_id IS NULL) = (store_schema_id IS NULL)',
    )

    op.drop_constraint('uq_datasets_name', 'datasets', type_='unique')
    op.create_index(
        'uq_datasets_unfiled_name', 'datasets', ['name'], unique=True,
        postgresql_where=sa.text('collection_id IS NULL'),
    )
    op.create_index(
        'uq_datasets_collection_folder_name', 'datasets', ['collection_id', 'folder_id', 'name'],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text('collection_id IS NOT NULL'),
    )
    op.create_index('ix_datasets_collection_folder', 'datasets', ['collection_id', 'folder_id'])
    op.create_index('ix_datasets_store_schema_id', 'datasets', ['store_schema_id'])


def downgrade() -> None:
    # Every dataset becomes unfiled again; its name must then be unique on its
    # own. Refuse rather than rename.
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text("SELECT name FROM datasets GROUP BY name HAVING count(*) > 1")
    ).scalars().all()
    if duplicates:
        raise RuntimeError(
            "Cannot restore uq_datasets_name: duplicate dataset name(s) exist "
            "across collections: " + ", ".join(sorted(duplicates)) + ". Rename "
            "or delete the duplicates by hand, then re-run the downgrade."
        )

    op.drop_index('ix_datasets_store_schema_id', table_name='datasets')
    op.drop_index('ix_datasets_collection_folder', table_name='datasets')
    op.drop_index('uq_datasets_collection_folder_name', table_name='datasets')
    op.drop_index('uq_datasets_unfiled_name', table_name='datasets')
    op.create_unique_constraint('uq_datasets_name', 'datasets', ['name'])

    op.drop_constraint('ck_datasets_store_schema_iff_collection', 'datasets', type_='check')
    op.drop_constraint('ck_datasets_folder_needs_collection', 'datasets', type_='check')
    op.drop_constraint('datasets_store_schema_id_fkey', 'datasets', type_='foreignkey')
    op.drop_constraint('datasets_folder_id_fkey', 'datasets', type_='foreignkey')
    op.drop_constraint('datasets_collection_id_fkey', 'datasets', type_='foreignkey')
    op.drop_column('datasets', 'store_schema_id')
    op.drop_column('datasets', 'folder_id')
    op.drop_column('datasets', 'collection_id')

    op.drop_table('collection_schemas')
    op.drop_table('folders')
    op.drop_table('collections')
