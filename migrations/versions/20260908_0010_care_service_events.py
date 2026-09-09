"""care service events

Revision ID: 20260908_0010
Revises: 20260908_0009
Create Date: 2026-09-08 13:20:59.059111+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '20260908_0010'
down_revision = '20260908_0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('care_service_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('aggregate_id', sa.UUID(), nullable=False),
    sa.Column('aggregate_version', sa.Integer(), nullable=False),
    sa.Column('appointment_id', sa.UUID(), nullable=True),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('workbench_recipient_id', sa.UUID(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_care_service_events_actor_user_id_users')),
    sa.ForeignKeyConstraint(['appointment_id'], ['care_appointments.id'], name=op.f('fk_care_service_events_appointment_id_care_appointments')),
    sa.ForeignKeyConstraint(['episode_id'], ['care_episodes.id'], name=op.f('fk_care_service_events_episode_id_care_episodes')),
    sa.ForeignKeyConstraint(['workbench_recipient_id'], ['care_providers.user_id'], name=op.f('fk_care_service_events_workbench_recipient_id_care_providers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_service_events')),
    sa.UniqueConstraint('episode_id', 'kind', 'aggregate_id', 'aggregate_version', name='uq_care_events_aggregate_version')
    )
    op.create_index('ix_care_events_episode_created', 'care_service_events', ['episode_id', 'occurred_at', 'id'], unique=False)
    op.create_index('ix_care_events_recipient_created', 'care_service_events', ['workbench_recipient_id', 'occurred_at', 'id'], unique=False)
    op.create_table('care_service_event_reads',
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['care_service_events.id'], name=op.f('fk_care_service_event_reads_event_id_care_service_events'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_care_service_event_reads_user_id_users')),
    sa.PrimaryKeyConstraint('event_id', 'user_id', name=op.f('pk_care_service_event_reads'))
    )


def downgrade() -> None:
    op.drop_table('care_service_event_reads')
    op.drop_index('ix_care_events_recipient_created', table_name='care_service_events')
    op.drop_index('ix_care_events_episode_created', table_name='care_service_events')
    op.drop_table('care_service_events')
