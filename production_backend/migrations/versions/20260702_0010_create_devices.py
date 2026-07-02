"""create pump devices and telemetry

Revision ID: 20260702_0010
Revises: 20260702_0009
Create Date: 2026-07-02 02:05:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0010"
down_revision = "20260702_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pump_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("firmware_version", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_pump_devices_owner_user_id_users"),
        sa.UniqueConstraint("owner_user_id", "device_id", name="uq_pump_devices_owner_device"),
    )
    op.create_index("ix_pump_devices_owner_status", "pump_devices", ["owner_user_id", "status"])

    op.create_table(
        "pump_telemetry_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_pump_telemetry_events_owner_user_id_users"),
    )
    op.create_index(
        "ix_pump_telemetry_owner_device_time",
        "pump_telemetry_events",
        ["owner_user_id", "device_id", "occurred_at"],
    )
    op.create_index(
        "ix_pump_telemetry_owner_event_time",
        "pump_telemetry_events",
        ["owner_user_id", "event_type", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pump_telemetry_owner_event_time", table_name="pump_telemetry_events")
    op.drop_index("ix_pump_telemetry_owner_device_time", table_name="pump_telemetry_events")
    op.drop_table("pump_telemetry_events")
    op.drop_index("ix_pump_devices_owner_status", table_name="pump_devices")
    op.drop_table("pump_devices")
