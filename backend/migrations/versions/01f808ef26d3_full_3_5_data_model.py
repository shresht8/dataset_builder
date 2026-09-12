"""full §3 + §5 data model

Revision ID: 01f808ef26d3
Revises: 1b36afbb3997
Create Date: 2026-09-05 16:45:53.613685
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '01f808ef26d3'
down_revision = '1b36afbb3997'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # autogenerate misses this: create the new enum types explicitly.
    # create_table handles user_role, but add_column does not handle row_status.
    sa.Enum('draft', 'needs_review', 'approved', name='row_status').create(op.get_bind())
    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('admin', 'editor', 'annotator', 'viewer', name='user_role'), server_default='viewer', nullable=False),
    sa.Column('entra_oid', sa.String(length=255), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('personal_access_tokens',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('token_hash', sa.String(length=255), nullable=False),
    sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_personal_access_tokens_user_id'), 'personal_access_tokens', ['user_id'], unique=False)
    op.create_table('dataset_versions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('dataset_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('snapshot_uri', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('schema_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dataset_id', 'version', name='uq_dataset_versions_dataset_id_version')
    )
    op.create_table('row_edits',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('row_id', sa.UUID(), nullable=False),
    sa.Column('field', sa.String(length=255), nullable=False),
    sa.Column('old_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('new_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['row_id'], ['dataset_rows.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_row_edits_row_id'), 'row_edits', ['row_id'], unique=False)
    op.add_column('dataset_columns', sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('dataset_columns', sa.Column('required', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('dataset_columns', sa.Column('archived', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('dataset_rows', sa.Column('status', postgresql.ENUM('draft', 'needs_review', 'approved', name='row_status', create_type=False), server_default='draft', nullable=False))
    op.add_column('dataset_rows', sa.Column('assignee', sa.UUID(), nullable=True))
    op.add_column('dataset_rows', sa.Column('source_trace_id', sa.String(length=255), nullable=True))
    op.add_column('dataset_rows', sa.Column('rev', sa.Integer(), server_default=sa.text('1'), nullable=False))
    op.add_column('dataset_rows', sa.Column('updated_by', sa.UUID(), nullable=True))
    op.add_column('dataset_rows', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.create_index('ix_dataset_rows_dataset_id_assignee', 'dataset_rows', ['dataset_id', 'assignee'], unique=False)
    op.create_index('ix_dataset_rows_dataset_id_status', 'dataset_rows', ['dataset_id', 'status'], unique=False)
    # autogenerate misses this: FK constraints must be named so downgrade can drop them
    op.create_foreign_key('dataset_rows_assignee_fkey', 'dataset_rows', 'users', ['assignee'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('dataset_rows_updated_by_fkey', 'dataset_rows', 'users', ['updated_by'], ['id'], ondelete='SET NULL')
    op.add_column('datasets', sa.Column('project_id', sa.String(length=255), nullable=True))
    op.add_column('datasets', sa.Column('feature_id', sa.String(length=255), nullable=True))
    op.add_column('datasets', sa.Column('created_by', sa.UUID(), nullable=True))
    op.create_foreign_key('datasets_created_by_fkey', 'datasets', 'users', ['created_by'], ['id'], ondelete='SET NULL')
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_constraint('datasets_created_by_fkey', 'datasets', type_='foreignkey')
    op.drop_column('datasets', 'created_by')
    op.drop_column('datasets', 'feature_id')
    op.drop_column('datasets', 'project_id')
    op.drop_constraint('dataset_rows_updated_by_fkey', 'dataset_rows', type_='foreignkey')
    op.drop_constraint('dataset_rows_assignee_fkey', 'dataset_rows', type_='foreignkey')
    op.drop_index('ix_dataset_rows_dataset_id_status', table_name='dataset_rows')
    op.drop_index('ix_dataset_rows_dataset_id_assignee', table_name='dataset_rows')
    op.drop_column('dataset_rows', 'updated_at')
    op.drop_column('dataset_rows', 'updated_by')
    op.drop_column('dataset_rows', 'rev')
    op.drop_column('dataset_rows', 'source_trace_id')
    op.drop_column('dataset_rows', 'assignee')
    op.drop_column('dataset_rows', 'status')
    op.drop_column('dataset_columns', 'archived')
    op.drop_column('dataset_columns', 'required')
    op.drop_column('dataset_columns', 'options')
    op.drop_index(op.f('ix_row_edits_row_id'), table_name='row_edits')
    op.drop_table('row_edits')
    op.drop_table('dataset_versions')
    op.drop_index(op.f('ix_personal_access_tokens_user_id'), table_name='personal_access_tokens')
    op.drop_table('personal_access_tokens')
    op.drop_table('users')
    # autogenerate misses this: drop the enum types created by this revision
    sa.Enum(name='row_status').drop(op.get_bind(), checkfirst=False)
    sa.Enum(name='user_role').drop(op.get_bind(), checkfirst=False)
    # ### end Alembic commands ###
