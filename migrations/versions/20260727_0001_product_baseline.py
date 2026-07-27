"""Create the final Product Backend schema on an empty database."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260727_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index(op.f('ix_users_status'), 'users', ['status'], unique=False)
    op.create_table('audit_logs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('actor_type', sa.String(length=32), server_default='user', nullable=False),
    sa.Column('actor_service', sa.String(length=120), server_default='', nullable=False),
    sa.Column('action', sa.String(length=120), nullable=False),
    sa.Column('resource_type', sa.String(length=120), nullable=False),
    sa.Column('resource_id', sa.String(length=120), server_default='', nullable=False),
    sa.Column('request_id', sa.String(length=80), server_default='', nullable=False),
    sa.Column('outcome', sa.String(length=32), server_default='succeeded', nullable=False),
    sa.Column('details_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_audit_logs_actor_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index('ix_audit_logs_actor_created', 'audit_logs', ['actor_user_id', 'created_at'], unique=False)
    op.create_index('ix_audit_logs_actor_service', 'audit_logs', ['actor_service', 'created_at'], unique=False)
    op.create_index('ix_audit_logs_request_id', 'audit_logs', ['request_id'], unique=False)
    op.create_index('ix_audit_logs_resource', 'audit_logs', ['resource_type', 'resource_id'], unique=False)
    op.create_table('auth_identities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=320), server_default='', nullable=False),
    sa.Column('phone', sa.String(length=32), server_default='', nullable=False),
    sa.Column('device_id', sa.String(length=120), server_default='', nullable=False),
    sa.Column('password_hash', sa.String(length=255), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_identities_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_identities')),
    sa.UniqueConstraint('provider', 'subject', name='uq_auth_identities_provider_subject')
    )
    op.create_index('ix_auth_identities_user_id', 'auth_identities', ['user_id'], unique=False)
    op.create_table('device_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('device_id', sa.String(length=120), server_default='', nullable=False),
    sa.Column('user_agent_hash', sa.String(length=128), server_default='', nullable=False),
    sa.Column('ip_hash', sa.String(length=128), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_device_sessions_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_device_sessions'))
    )
    op.create_index('ix_device_sessions_device_id', 'device_sessions', ['device_id'], unique=False)
    op.create_index('ix_device_sessions_user_status', 'device_sessions', ['user_id', 'status'], unique=False)
    op.create_table('diary_entries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('entry_date', sa.Date(), nullable=False),
    sa.Column('content', sa.Text(), server_default='', nullable=False),
    sa.Column('attributes_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('attachments_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_diary_entries_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_diary_entries')),
    sa.UniqueConstraint('owner_user_id', 'entry_date', name='uq_diary_entries_owner_date')
    )
    op.create_index('ix_diary_entries_owner_date', 'diary_entries', ['owner_user_id', 'entry_date'], unique=False)
    op.create_table('files',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('object_key', sa.String(length=512), nullable=False),
    sa.Column('original_filename', sa.String(length=255), server_default='', nullable=False),
    sa.Column('content_type', sa.String(length=255), server_default='', nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_files_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_files'))
    )
    op.create_index('ix_files_object_key', 'files', ['object_key'], unique=True)
    op.create_index('ix_files_owner_status_created', 'files', ['owner_user_id', 'status', 'created_at'], unique=False)
    op.create_table('idempotency_keys',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=False),
    sa.Column('scope', sa.String(length=120), nullable=False),
    sa.Column('key', sa.String(length=255), nullable=False),
    sa.Column('request_hash', sa.String(length=128), nullable=False),
    sa.Column('response_ref', sa.String(length=512), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='in_progress', nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_idempotency_keys_actor_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_idempotency_keys')),
    sa.UniqueConstraint('actor_user_id', 'scope', 'key', name='uq_idempotency_actor_scope_key')
    )
    op.create_index('ix_idempotency_keys_expires_at', 'idempotency_keys', ['expires_at'], unique=False)
    op.create_table('infant_profiles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('sex_at_birth', sa.String(length=32), nullable=True),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('birth_weight_kg', sa.Float(), nullable=True),
    sa.Column('gestational_age_at_birth_days', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('birth_weight_kg IS NULL OR (birth_weight_kg >= 0.2 AND birth_weight_kg <= 10)', name=op.f('ck_infant_profiles_ck_infant_profiles_birth_weight_kg')),
    sa.CheckConstraint('gestational_age_at_birth_days IS NULL OR (gestational_age_at_birth_days >= 140 AND gestational_age_at_birth_days <= 315)', name=op.f('ck_infant_profiles_ck_infant_profiles_gestational_age_at_birth_days')),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_infant_profiles_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_infant_profiles'))
    )
    op.create_index('ix_infant_profiles_birth_date', 'infant_profiles', ['birth_date'], unique=False)
    op.create_index('ix_infant_profiles_owner_deleted_at', 'infant_profiles', ['owner_user_id', 'deleted_at'], unique=False)
    op.create_table('invite_codes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('label', sa.String(length=120), server_default='', nullable=False),
    sa.Column('assigned_to', sa.String(length=320), server_default='', nullable=False),
    sa.Column('bound_device_id', sa.String(length=120), server_default='', nullable=False),
    sa.Column('bound_user_id', sa.UUID(), nullable=True),
    sa.Column('created_by_service', sa.String(length=120), server_default='', nullable=False),
    sa.Column('used_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['bound_user_id'], ['users.id'], name=op.f('fk_invite_codes_bound_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invite_codes'))
    )
    op.create_index('ix_invite_codes_code', 'invite_codes', ['code'], unique=True)
    op.create_index('ix_invite_codes_created_at', 'invite_codes', ['created_at'], unique=False)
    op.create_index('ix_invite_codes_status', 'invite_codes', ['status'], unique=False)
    op.create_table('lactation_profiles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('current_feeding_mode', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("current_feeding_mode IS NULL OR current_feeding_mode IN ('exclusive_breastfeeding', 'expressed_milk_feeding', 'mixed_feeding', 'formula_feeding', 'unknown')", name=op.f('ck_lactation_profiles_ck_lactation_profiles_feeding_mode')),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_lactation_profiles_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lactation_profiles')),
    sa.UniqueConstraint('owner_user_id', name='uq_lactation_profiles_owner_user_id')
    )
    op.create_table('maternal_profiles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('delivery_count', sa.Integer(), nullable=True),
    sa.Column('latest_delivery_method', sa.String(length=32), nullable=True),
    sa.Column('latest_delivery_date', sa.Date(), nullable=True),
    sa.Column('has_cesarean_history', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("latest_delivery_method <> 'cesarean' OR has_cesarean_history IS TRUE", name=op.f('ck_maternal_profiles_cesarean_history')),
    sa.CheckConstraint("latest_delivery_method IS NULL OR latest_delivery_method IN ('vaginal', 'cesarean', 'assisted_vaginal', 'other', 'unknown')", name=op.f('ck_maternal_profiles_ck_maternal_profiles_delivery_method')),
    sa.CheckConstraint('delivery_count IS NULL OR (delivery_count >= 1 AND delivery_count <= 20)', name=op.f('ck_maternal_profiles_ck_maternal_profiles_delivery_count')),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_maternal_profiles_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_maternal_profiles')),
    sa.UniqueConstraint('owner_user_id', name='uq_maternal_profiles_owner_user_id')
    )
    op.create_table('notifications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('notification_type', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=255), server_default='', nullable=False),
    sa.Column('body', sa.String(length=2000), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='unread', nullable=False),
    sa.Column('source', sa.String(length=64), server_default='system', nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_notifications_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications'))
    )
    op.create_index('ix_notifications_owner_status_created', 'notifications', ['owner_user_id', 'status', 'created_at'], unique=False)
    op.create_index('ix_notifications_owner_type_created', 'notifications', ['owner_user_id', 'notification_type', 'created_at'], unique=False)
    op.create_table('plans',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('plan_type', sa.String(length=64), server_default='', nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('summary', sa.Text(), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('source', sa.String(length=64), server_default='manual', nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('starts_on', sa.Date(), nullable=True),
    sa.Column('ends_on', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_plans_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plans'))
    )
    op.create_index('ix_plans_owner_status_updated', 'plans', ['owner_user_id', 'status', 'updated_at'], unique=False)
    op.create_index('ix_plans_owner_type', 'plans', ['owner_user_id', 'plan_type'], unique=False)
    op.create_index('uq_plans_owner_active_pregnancy', 'plans', ['owner_user_id'], unique=True, postgresql_where=sa.text("plan_type = 'pregnancy' AND status = 'active' AND deleted_at IS NULL"))
    op.create_table('pump_devices',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('device_id', sa.String(length=120), nullable=False),
    sa.Column('model', sa.String(length=120), server_default='', nullable=False),
    sa.Column('firmware_version', sa.String(length=120), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_pump_devices_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pump_devices')),
    sa.UniqueConstraint('owner_user_id', 'device_id', name='uq_pump_devices_owner_device')
    )
    op.create_index('ix_pump_devices_owner_status', 'pump_devices', ['owner_user_id', 'status'], unique=False)
    op.create_table('pump_telemetry_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('device_id', sa.String(length=120), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_pump_telemetry_events_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pump_telemetry_events'))
    )
    op.create_index('ix_pump_telemetry_owner_device_time', 'pump_telemetry_events', ['owner_user_id', 'device_id', 'occurred_at'], unique=False)
    op.create_index('ix_pump_telemetry_owner_event_time', 'pump_telemetry_events', ['owner_user_id', 'event_type', 'occurred_at'], unique=False)
    op.create_table('support_tickets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('ticket_number', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='submitted', nullable=False),
    sa.Column('issue_type', sa.String(length=120), server_default='other', nullable=False),
    sa.Column('issue_summary', sa.String(length=2000), server_default='', nullable=False),
    sa.Column('product_model', sa.String(length=120), server_default='', nullable=False),
    sa.Column('order_number', sa.String(length=120), server_default='', nullable=False),
    sa.Column('purchase_channel', sa.String(length=120), server_default='', nullable=False),
    sa.Column('user_contact', sa.String(length=255), server_default='', nullable=False),
    sa.Column('urgency', sa.String(length=32), server_default='normal', nullable=False),
    sa.Column('source', sa.String(length=64), server_default='agent', nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_support_tickets_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_support_tickets')),
    sa.UniqueConstraint('ticket_number', name='uq_support_tickets_ticket_number')
    )
    op.create_index('ix_support_tickets_owner_created', 'support_tickets', ['owner_user_id', 'created_at'], unique=False)
    op.create_index('ix_support_tickets_owner_status_updated', 'support_tickets', ['owner_user_id', 'status', 'updated_at'], unique=False)
    op.create_table('user_profiles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('preferred_name', sa.String(length=120), nullable=True),
    sa.Column('age', sa.Integer(), nullable=True),
    sa.Column('estimated_due_date', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_profiles_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_profiles')),
    sa.UniqueConstraint('user_id', name='uq_user_profiles_user_id')
    )
    op.create_index('ix_user_profiles_estimated_due_date', 'user_profiles', ['estimated_due_date'], unique=False)
    op.create_table('growth_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('infant_id', sa.UUID(), nullable=True),
    sa.Column('measured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('height_cm', sa.Float(), nullable=True),
    sa.Column('weight_kg', sa.Float(), nullable=True),
    sa.Column('head_cm', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['infant_id'], ['infant_profiles.id'], name=op.f('fk_growth_records_infant_id_infant_profiles')),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_growth_records_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_growth_records'))
    )
    op.create_index('ix_growth_records_owner_infant_measured', 'growth_records', ['owner_user_id', 'infant_id', 'measured_at'], unique=False)
    op.create_index('ix_growth_records_owner_measured', 'growth_records', ['owner_user_id', 'measured_at'], unique=False)
    op.create_table('maternal_current_delivery_infants',
    sa.Column('maternal_profile_id', sa.UUID(), nullable=False),
    sa.Column('infant_id', sa.UUID(), nullable=False),
    sa.Column('birth_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('birth_order >= 1 AND birth_order <= 10', name=op.f('ck_maternal_current_delivery_infants_ck_maternal_current_delivery_infants_birth_order')),
    sa.ForeignKeyConstraint(['infant_id'], ['infant_profiles.id'], name=op.f('fk_maternal_current_delivery_infants_infant_id_infant_profiles')),
    sa.ForeignKeyConstraint(['maternal_profile_id'], ['maternal_profiles.id'], name=op.f('fk_maternal_current_delivery_infants_maternal_profile_id_maternal_profiles')),
    sa.PrimaryKeyConstraint('maternal_profile_id', 'infant_id', name=op.f('pk_maternal_current_delivery_infants')),
    sa.UniqueConstraint('infant_id', name='uq_maternal_current_delivery_infants_infant_id'),
    sa.UniqueConstraint('maternal_profile_id', 'birth_order', name='uq_maternal_current_delivery_infants_birth_order')
    )
    op.create_table('plan_tasks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('plan_id', sa.UUID(), nullable=True),
    sa.Column('task_date', sa.Date(), nullable=True),
    sa.Column('task_time', sa.String(length=16), server_default='', nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='pending', nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_plan_tasks_owner_user_id_users')),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], name=op.f('fk_plan_tasks_plan_id_plans')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plan_tasks'))
    )
    op.create_index('ix_plan_tasks_owner_date_status', 'plan_tasks', ['owner_user_id', 'task_date', 'status'], unique=False)
    op.create_index('ix_plan_tasks_plan_id', 'plan_tasks', ['plan_id'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('family_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['device_sessions.id'], name=op.f('fk_refresh_tokens_session_id_device_sessions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
    sa.UniqueConstraint('token_hash', name='uq_refresh_tokens_token_hash')
    )
    op.create_index('ix_refresh_tokens_expires_at', 'refresh_tokens', ['expires_at'], unique=False)
    op.create_index('ix_refresh_tokens_family_status', 'refresh_tokens', ['family_id', 'status'], unique=False)
    op.create_index('ix_refresh_tokens_session_status', 'refresh_tokens', ['session_id', 'status'], unique=False)
    op.create_table('feeding_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('plan_task_id', sa.UUID(), nullable=True),
    sa.Column('infant_id', sa.UUID(), nullable=True),
    sa.Column('feed_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('feed_type', sa.String(length=32), server_default='', nullable=False),
    sa.Column('feed_action', sa.String(length=32), server_default='', nullable=False),
    sa.Column('volume_ml', sa.Float(), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=255), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['infant_id'], ['infant_profiles.id'], name=op.f('fk_feeding_records_infant_id_infant_profiles')),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_feeding_records_owner_user_id_users')),
    sa.ForeignKeyConstraint(['plan_task_id'], ['plan_tasks.id'], name=op.f('fk_feeding_records_plan_task_id_plan_tasks')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_feeding_records'))
    )
    op.create_index('ix_feeding_records_owner_infant_time', 'feeding_records', ['owner_user_id', 'infant_id', 'feed_time'], unique=False)
    op.create_index('ix_feeding_records_owner_time', 'feeding_records', ['owner_user_id', 'feed_time'], unique=False)
    op.create_index('ix_feeding_records_plan_task', 'feeding_records', ['plan_task_id'], unique=False)
    op.create_table('pumping_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('plan_task_id', sa.UUID(), nullable=True),
    sa.Column('pump_start_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('pump_end_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('milk_volume_ml', sa.Float(), nullable=True),
    sa.Column('pump_type', sa.String(length=32), server_default='', nullable=False),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('source', sa.String(length=32), server_default='manual', nullable=False),
    sa.Column('title', sa.String(length=255), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_pumping_records_owner_user_id_users')),
    sa.ForeignKeyConstraint(['plan_task_id'], ['plan_tasks.id'], name=op.f('fk_pumping_records_plan_task_id_plan_tasks')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pumping_records'))
    )
    op.create_index('ix_pumping_records_owner_start', 'pumping_records', ['owner_user_id', 'pump_start_time'], unique=False)
    op.create_index('ix_pumping_records_owner_status', 'pumping_records', ['owner_user_id', 'status'], unique=False)
    op.create_index('ix_pumping_records_plan_task', 'pumping_records', ['plan_task_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_pumping_records_plan_task', table_name='pumping_records')
    op.drop_index('ix_pumping_records_owner_status', table_name='pumping_records')
    op.drop_index('ix_pumping_records_owner_start', table_name='pumping_records')
    op.drop_table('pumping_records')
    op.drop_index('ix_feeding_records_plan_task', table_name='feeding_records')
    op.drop_index('ix_feeding_records_owner_time', table_name='feeding_records')
    op.drop_index('ix_feeding_records_owner_infant_time', table_name='feeding_records')
    op.drop_table('feeding_records')
    op.drop_index('ix_refresh_tokens_session_status', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_family_status', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_expires_at', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index('ix_plan_tasks_plan_id', table_name='plan_tasks')
    op.drop_index('ix_plan_tasks_owner_date_status', table_name='plan_tasks')
    op.drop_table('plan_tasks')
    op.drop_table('maternal_current_delivery_infants')
    op.drop_index('ix_growth_records_owner_measured', table_name='growth_records')
    op.drop_index('ix_growth_records_owner_infant_measured', table_name='growth_records')
    op.drop_table('growth_records')
    op.drop_index('ix_user_profiles_estimated_due_date', table_name='user_profiles')
    op.drop_table('user_profiles')
    op.drop_index('ix_support_tickets_owner_status_updated', table_name='support_tickets')
    op.drop_index('ix_support_tickets_owner_created', table_name='support_tickets')
    op.drop_table('support_tickets')
    op.drop_index('ix_pump_telemetry_owner_event_time', table_name='pump_telemetry_events')
    op.drop_index('ix_pump_telemetry_owner_device_time', table_name='pump_telemetry_events')
    op.drop_table('pump_telemetry_events')
    op.drop_index('ix_pump_devices_owner_status', table_name='pump_devices')
    op.drop_table('pump_devices')
    op.drop_index('uq_plans_owner_active_pregnancy', table_name='plans', postgresql_where=sa.text("plan_type = 'pregnancy' AND status = 'active' AND deleted_at IS NULL"))
    op.drop_index('ix_plans_owner_type', table_name='plans')
    op.drop_index('ix_plans_owner_status_updated', table_name='plans')
    op.drop_table('plans')
    op.drop_index('ix_notifications_owner_type_created', table_name='notifications')
    op.drop_index('ix_notifications_owner_status_created', table_name='notifications')
    op.drop_table('notifications')
    op.drop_table('maternal_profiles')
    op.drop_table('lactation_profiles')
    op.drop_index('ix_invite_codes_status', table_name='invite_codes')
    op.drop_index('ix_invite_codes_created_at', table_name='invite_codes')
    op.drop_index('ix_invite_codes_code', table_name='invite_codes')
    op.drop_table('invite_codes')
    op.drop_index('ix_infant_profiles_owner_deleted_at', table_name='infant_profiles')
    op.drop_index('ix_infant_profiles_birth_date', table_name='infant_profiles')
    op.drop_table('infant_profiles')
    op.drop_index('ix_idempotency_keys_expires_at', table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
    op.drop_index('ix_files_owner_status_created', table_name='files')
    op.drop_index('ix_files_object_key', table_name='files')
    op.drop_table('files')
    op.drop_index('ix_diary_entries_owner_date', table_name='diary_entries')
    op.drop_table('diary_entries')
    op.drop_index('ix_device_sessions_user_status', table_name='device_sessions')
    op.drop_index('ix_device_sessions_device_id', table_name='device_sessions')
    op.drop_table('device_sessions')
    op.drop_index('ix_auth_identities_user_id', table_name='auth_identities')
    op.drop_table('auth_identities')
    op.drop_index('ix_audit_logs_resource', table_name='audit_logs')
    op.drop_index('ix_audit_logs_request_id', table_name='audit_logs')
    op.drop_index('ix_audit_logs_actor_service', table_name='audit_logs')
    op.drop_index('ix_audit_logs_actor_created', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index(op.f('ix_users_status'), table_name='users')
    op.drop_table('users')
