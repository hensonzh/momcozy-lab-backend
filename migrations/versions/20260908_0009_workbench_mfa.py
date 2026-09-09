"""workbench mfa

Revision ID: 20260908_0009
Revises: 20260908_0008
Create Date: 2026-09-08 11:53:03.747526+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '20260908_0009'
down_revision = '20260908_0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('workbench_login_challenges',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('provider_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('credential_version', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.String(length=120), nullable=False),
    sa.Column('user_agent_hash', sa.String(length=64), nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['provider_id'], ['care_providers.user_id'], name=op.f('fk_workbench_login_challenges_provider_id_care_providers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workbench_login_challenges')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_workbench_login_challenges_token_hash'))
    )
    op.create_index('ix_workbench_login_provider_created', 'workbench_login_challenges', ['provider_id', 'created_at'], unique=False)
    op.create_table('workbench_mfa_credentials',
    sa.Column('provider_id', sa.UUID(), nullable=False),
    sa.Column('encrypted_secret', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('last_counter', sa.BigInteger(), nullable=False),
    sa.Column('failed_attempts', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('version >= 1 AND failed_attempts >= 0 AND last_counter >= -1', name=op.f('ck_workbench_mfa_credentials_workbench_mfa_counters')),
    sa.ForeignKeyConstraint(['provider_id'], ['care_providers.user_id'], name=op.f('fk_workbench_mfa_credentials_provider_id_care_providers')),
    sa.PrimaryKeyConstraint('provider_id', name=op.f('pk_workbench_mfa_credentials'))
    )
    op.add_column('device_sessions', sa.Column('mfa_verified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('device_sessions', 'mfa_verified_at')
    op.drop_table('workbench_mfa_credentials')
    op.drop_index('ix_workbench_login_provider_created', table_name='workbench_login_challenges')
    op.drop_table('workbench_login_challenges')
