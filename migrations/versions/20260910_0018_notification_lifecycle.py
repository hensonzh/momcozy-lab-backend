"""Add push lifecycle to the existing notification inbox."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20260910_0018'
down_revision = '20260910_0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, size, default in [('category', 32, 'service_updates'), ('send_status', 24, 'in_app'), ('trigger_type', 16, 'immediate')]:
        op.add_column('notifications', sa.Column(name, sa.String(size), nullable=False, server_default=default))
    for name in ['trigger_at', 'available_at', 'expires_at', 'sent_at', 'canceled_at']:
        op.add_column('notifications', sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column('notifications', sa.Column('related_resource_type', sa.String(32)))
    op.add_column('notifications', sa.Column('related_resource_id', postgresql.UUID(as_uuid=True)))
    op.add_column('notifications', sa.Column('resource_version', sa.Integer()))
    op.add_column('notifications', sa.Column('route', sa.String(400)))
    op.add_column('notifications', sa.Column('idempotency_key', sa.String(255)))
    op.create_index('ix_notifications_send_due', 'notifications', ['send_status', 'trigger_at'])
    op.create_unique_constraint('uq_notifications_idempotency_key', 'notifications', ['idempotency_key'])
    op.create_table('push_installations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('secret_hash', sa.String(64), nullable=False),
        sa.Column('owner_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id')),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('device_sessions.id')),
        sa.Column('binding_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False),
        sa.Column('permission', sa.String(24), nullable=False, server_default='not_determined'),
        sa.Column('locale', sa.String(16), nullable=False, server_default='en'),
        sa.Column('encrypted_token', sa.Text()),
        sa.Column('token_hash', sa.String(64), unique=True),
        sa.Column('client_revision', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('invalidated_at', sa.DateTime(timezone=True)),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index('ix_push_installations_owner_session', 'push_installations', ['owner_user_id', 'session_id'])
    op.create_table('notification_deliveries',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('notification_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('notifications.id'), nullable=False),
        sa.Column('installation_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('push_installations.id'), nullable=False),
        sa.Column('binding_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(24), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True)),
        sa.Column('provider_message_id', sa.String(255)),
        sa.Column('error_code', sa.String(64)),
        sa.UniqueConstraint('notification_id', 'installation_id', 'binding_id', name='uq_notification_delivery_binding'))
    op.create_index('ix_notification_deliveries_status_available', 'notification_deliveries', ['status', 'available_at'])
    op.create_table('notification_preferences',
        sa.Column('owner_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('category', sa.String(32), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('notification_event_receipts',
        sa.Column('event_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('care_service_events.id'), primary_key=True),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False))
    # Activating this consumer must not send a historical backlog of care events.
    op.execute('INSERT INTO notification_event_receipts (event_id, processed_at) SELECT id, CURRENT_TIMESTAMP FROM care_service_events')


def downgrade() -> None:
    for table in ['notification_event_receipts', 'notification_preferences', 'notification_deliveries', 'push_installations']:
        op.drop_table(table)
    op.drop_constraint('uq_notifications_idempotency_key', 'notifications', type_='unique')
    op.drop_index('ix_notifications_send_due', 'notifications')
    for name in ['idempotency_key', 'route', 'resource_version', 'related_resource_id', 'related_resource_type',
                 'canceled_at', 'sent_at', 'expires_at', 'available_at', 'trigger_at', 'trigger_type', 'send_status', 'category']:
        op.drop_column('notifications', name)
