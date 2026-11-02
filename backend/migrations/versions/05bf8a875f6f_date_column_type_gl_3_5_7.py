"""date column type (GL-3.5-7)

Adds the `date` column type: a calendar date stored as a `YYYY-MM-DD` string,
with no time and no timezone. (`datetime` and the timezone settings are the
deferred GL-3.5-17.)

Revision ID: 05bf8a875f6f
Revises: 42726e27aada
Create Date: 2026-10-10 15:02:57.743249
"""
from alembic import op
import sqlalchemy as sa


revision = '05bf8a875f6f'
down_revision = '42726e27aada'
branch_labels = None
depends_on = None

_TYPES_WITHOUT_DATE = (
    "'text', 'long_text', 'select', 'multi_select', 'number', 'boolean', 'json'"
)


def upgrade() -> None:
    # Postgres cannot use a new enum value in the transaction that added it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE column_type ADD VALUE IF NOT EXISTS 'date'")


def downgrade() -> None:
    # Postgres cannot drop an enum value, so the type is recreated without
    # 'date' -- which is only possible if no column still uses it.
    bind = op.get_bind()
    in_use = bind.execute(
        sa.text("SELECT count(*) FROM dataset_columns WHERE type = 'date'")
    ).scalar_one()
    if in_use:
        raise RuntimeError(
            f"Cannot downgrade: {in_use} dataset column(s) use the 'date' type. "
            "Change or remove them first."
        )
    op.execute("ALTER TYPE column_type RENAME TO column_type_old")
    op.execute(f"CREATE TYPE column_type AS ENUM ({_TYPES_WITHOUT_DATE})")
    op.execute(
        "ALTER TABLE dataset_columns ALTER COLUMN type TYPE column_type "
        "USING type::text::column_type"
    )
    op.execute("DROP TYPE column_type_old")
