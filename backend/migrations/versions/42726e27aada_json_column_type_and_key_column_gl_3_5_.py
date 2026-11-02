"""json column type and key column (GL-3.5-12)

Adds the `json` column type (a JSON value, optionally constrained by a
per-column JSON Schema) and a per-dataset key column: `dataset_columns.is_key`
(at most one active per dataset) plus `dataset_rows.row_key`, the mirror of the
key column's value that makes key uniqueness race-free.

Revision ID: 42726e27aada
Revises: 37024be68b69
Create Date: 2026-10-10 13:17:17.079078
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '42726e27aada'
down_revision = '37024be68b69'
branch_labels = None
depends_on = None

_TYPES_WITHOUT_JSON = "'text', 'long_text', 'select', 'multi_select', 'number', 'boolean'"


def upgrade() -> None:
    # Postgres cannot use a new enum value in the transaction that added it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'json'")

    op.add_column(
        'dataset_columns',
        sa.Column('json_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        'dataset_columns',
        sa.Column('is_key', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index(
        'uq_dataset_columns_one_key',
        'dataset_columns',
        ['dataset_id'],
        unique=True,
        postgresql_where=sa.text('is_key AND NOT archived'),
    )
    op.add_column('dataset_rows', sa.Column('row_key', sa.Text(), nullable=True))
    op.create_index(
        'uq_dataset_rows_row_key',
        'dataset_rows',
        ['dataset_id', 'row_key'],
        unique=True,
        postgresql_where=sa.text('row_key IS NOT NULL'),
    )


def downgrade() -> None:
    # Postgres cannot drop an enum value, so the type is recreated without
    # 'json' -- which is only possible if no column still uses it.
    bind = op.get_bind()
    in_use = bind.execute(
        sa.text("SELECT count(*) FROM dataset_columns WHERE type = 'json'")
    ).scalar_one()
    if in_use:
        raise RuntimeError(
            f"Cannot downgrade: {in_use} dataset column(s) use the 'json' type. "
            "Change or remove them first."
        )

    op.drop_index('uq_dataset_rows_row_key', table_name='dataset_rows')
    op.drop_column('dataset_rows', 'row_key')
    op.drop_index('uq_dataset_columns_one_key', table_name='dataset_columns')
    op.drop_column('dataset_columns', 'is_key')
    op.drop_column('dataset_columns', 'json_schema')

    op.execute("ALTER TYPE column_type RENAME TO column_type_old")
    op.execute(f"CREATE TYPE column_type AS ENUM ({_TYPES_WITHOUT_JSON})")
    op.execute(
        "ALTER TABLE dataset_columns ALTER COLUMN type TYPE column_type "
        "USING type::text::column_type"
    )
    op.execute("DROP TYPE column_type_old")
