"""baby records

Revision ID: 20260908_0013
Revises: 20260908_0012
Create Date: 2026-09-08 15:00:01.956541+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '20260908_0013'
down_revision = '20260908_0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('baby_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('baby_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('recorded_on', sa.Date(), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("ended_at IS NULL OR (kind = 'sleep' AND ended_at > occurred_at)", name=op.f('ck_baby_records_baby_record_sleep_end')),
    sa.CheckConstraint("kind IN ('feeding','sleep','diaper','growth','development')", name=op.f('ck_baby_records_baby_record_kind')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_baby_records_baby_record_version')),
    sa.CheckConstraint("(kind IN ('growth','development') AND recorded_on IS NOT NULL AND occurred_at IS NULL) OR (kind IN ('feeding','sleep','diaper') AND occurred_at IS NOT NULL AND recorded_on IS NULL)", name=op.f('ck_baby_records_baby_record_time_kind')),
    sa.ForeignKeyConstraint(['baby_id'], ['infant_profiles.id'], name=op.f('fk_baby_records_baby_id_infant_profiles')),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_baby_records_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_baby_records'))
    )
    op.create_index('ix_baby_records_scope_time', 'baby_records', ['owner_user_id', 'baby_id', 'occurred_at', 'id'], unique=False)
    op.create_index('ix_baby_records_scope_date', 'baby_records', ['owner_user_id', 'baby_id', 'recorded_on', 'id'], unique=False)
    op.create_index('uq_baby_records_active_sleep', 'baby_records', ['baby_id'], unique=True, postgresql_where=sa.text("kind = 'sleep' AND ended_at IS NULL AND deleted_at IS NULL"))


def downgrade() -> None:
    op.drop_index('uq_baby_records_active_sleep', table_name='baby_records', postgresql_where=sa.text("kind = 'sleep' AND ended_at IS NULL AND deleted_at IS NULL"))
    op.drop_index('ix_baby_records_scope_time', table_name='baby_records')
    op.drop_index('ix_baby_records_scope_date', table_name='baby_records')
    op.drop_table('baby_records')
