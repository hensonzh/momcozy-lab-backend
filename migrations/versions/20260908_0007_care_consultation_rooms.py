"""care consultation rooms

Revision ID: 20260908_0007
Revises: 20260908_0006
Create Date: 2026-09-08 10:28:18.636908+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '20260908_0007'
down_revision = '20260908_0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('care_location_checks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('appointment_id', sa.UUID(), nullable=False),
    sa.Column('region', sa.String(length=2), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['appointment_id'], ['care_appointments.id'], name=op.f('fk_care_location_checks_appointment_id_care_appointments')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_location_checks'))
    )
    op.create_index('ix_care_location_appointment_created', 'care_location_checks', ['appointment_id', 'created_at'], unique=False)
    op.create_table('care_consultations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('appointment_id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('intake_revision_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('video_provider', sa.String(length=16), nullable=False),
    sa.Column('room_name', sa.String(length=120), nullable=False),
    sa.Column('room_generation', sa.Integer(), nullable=False),
    sa.Column('room_status', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('end_reason', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("room_status IN ('creating','ready','closing','closed','failed')", name=op.f('ck_care_consultations_consultation_room_status')),
    sa.CheckConstraint("status IN ('waiting_room','in_progress','note_pending','no_show','failed','cancelled','closed')", name=op.f('ck_care_consultations_consultation_status')),
    sa.ForeignKeyConstraint(['appointment_id'], ['care_appointments.id'], name=op.f('fk_care_consultations_appointment_id_care_appointments')),
    sa.ForeignKeyConstraint(['episode_id'], ['care_episodes.id'], name=op.f('fk_care_consultations_episode_id_care_episodes')),
    sa.ForeignKeyConstraint(['intake_revision_id'], ['care_intake_revisions.id'], name=op.f('fk_care_consultations_intake_revision_id_care_intake_revisions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_consultations')),
    sa.UniqueConstraint('appointment_id', name=op.f('uq_care_consultations_appointment_id')),
    sa.UniqueConstraint('room_name', name=op.f('uq_care_consultations_room_name'))
    )
    op.create_index(op.f('ix_care_consultations_episode_id'), 'care_consultations', ['episode_id'], unique=False)
    op.create_table('care_room_participants',
    sa.Column('consultation_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=False),
    sa.Column('presence', sa.String(length=16), nullable=False),
    sa.Column('connection_id', sa.UUID(), nullable=True),
    sa.Column('connection_version', sa.Integer(), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("presence IN ('not_joined','joining','joined','reconnecting','left')", name=op.f('ck_care_room_participants_room_participant_presence')),
    sa.CheckConstraint("role IN ('mom','ibclc')", name=op.f('ck_care_room_participants_room_participant_role')),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_care_room_participants_actor_user_id_users')),
    sa.ForeignKeyConstraint(['consultation_id'], ['care_consultations.id'], name=op.f('fk_care_room_participants_consultation_id_care_consultations')),
    sa.PrimaryKeyConstraint('consultation_id', 'role', name=op.f('pk_care_room_participants')),
    sa.UniqueConstraint('connection_id', name=op.f('uq_care_room_participants_connection_id'))
    )
    op.create_table('care_session_consumptions',
    sa.Column('consultation_id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['consultation_id'], ['care_consultations.id'], name=op.f('fk_care_session_consumptions_consultation_id_care_consultations')),
    sa.ForeignKeyConstraint(['episode_id'], ['care_episodes.id'], name=op.f('fk_care_session_consumptions_episode_id_care_episodes')),
    sa.PrimaryKeyConstraint('consultation_id', name=op.f('pk_care_session_consumptions'))
    )
    op.create_index(op.f('ix_care_session_consumptions_episode_id'), 'care_session_consumptions', ['episode_id'], unique=False)
    op.create_table('care_video_commands',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('consultation_id', sa.UUID(), nullable=False),
    sa.Column('room_name', sa.String(length=120), nullable=False),
    sa.Column('operation', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.String(length=80), nullable=True),
    sa.CheckConstraint("operation IN ('create','close')", name=op.f('ck_care_video_commands_video_command_operation')),
    sa.CheckConstraint("status IN ('pending','done')", name=op.f('ck_care_video_commands_video_command_status')),
    sa.ForeignKeyConstraint(['consultation_id'], ['care_consultations.id'], name=op.f('fk_care_video_commands_consultation_id_care_consultations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_video_commands')),
    sa.UniqueConstraint('room_name', 'operation', name=op.f('uq_care_video_commands_room_name'))
    )
    op.create_index('ix_care_video_commands_due', 'care_video_commands', ['status', 'available_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_care_video_commands_due', table_name='care_video_commands')
    op.drop_table('care_video_commands')
    op.drop_index(op.f('ix_care_session_consumptions_episode_id'), table_name='care_session_consumptions')
    op.drop_table('care_session_consumptions')
    op.drop_table('care_room_participants')
    op.drop_index(op.f('ix_care_consultations_episode_id'), table_name='care_consultations')
    op.drop_table('care_consultations')
    op.drop_index('ix_care_location_appointment_created', table_name='care_location_checks')
    op.drop_table('care_location_checks')
