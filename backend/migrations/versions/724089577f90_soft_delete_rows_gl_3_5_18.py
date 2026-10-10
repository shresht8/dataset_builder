"""soft-delete rows (GL-3.5-18)

`dataset_rows.deleted_at` / `deleted_by`: a deleted row keeps its data and
audit trail but drops out of the grid, versions, sync and pull. The key
uniqueness index now ignores deleted rows, so a deleted row's key can be
reused.

Revision ID: 724089577f90
Revises: 05bf8a875f6f
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '724089577f90'
down_revision = '05bf8a875f6f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('dataset_rows', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'dataset_rows',
        sa.Column('deleted_by', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'dataset_rows_deleted_by_fkey', 'dataset_rows', 'users', ['deleted_by'], ['id'],
        ondelete='SET NULL',
    )
    op.drop_index('uq_dataset_rows_row_key', table_name='dataset_rows')
    op.create_index(
        'uq_dataset_rows_row_key',
        'dataset_rows',
        ['dataset_id', 'row_key'],
        unique=True,
        postgresql_where=sa.text('row_key IS NOT NULL AND deleted_at IS NULL'),
    )


def downgrade() -> None:
    # Restoring the GL-3.5-12 index counts deleted rows again, so a key reused
    # after a delete would collide: refuse rather than fail halfway.
    bind = op.get_bind()
    clashes = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT dataset_id, row_key FROM dataset_rows "
            "WHERE row_key IS NOT NULL GROUP BY dataset_id, row_key HAVING count(*) > 1) c"
        )
    ).scalar_one()
    if clashes:
        raise RuntimeError(
            f"Cannot downgrade: {clashes} key value(s) are shared by a deleted and a live "
            "row. Remove the deleted rows first."
        )
    op.drop_index('uq_dataset_rows_row_key', table_name='dataset_rows')
    op.create_index(
        'uq_dataset_rows_row_key',
        'dataset_rows',
        ['dataset_id', 'row_key'],
        unique=True,
        postgresql_where=sa.text('row_key IS NOT NULL'),
    )
    op.drop_constraint('dataset_rows_deleted_by_fkey', 'dataset_rows', type_='foreignkey')
    op.drop_column('dataset_rows', 'deleted_by')
    op.drop_column('dataset_rows', 'deleted_at')
