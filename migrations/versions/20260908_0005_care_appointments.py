"""Provider calendars, booking prechecks and versioned appointment holds."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260908_0005"
down_revision = "20260908_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("care_provider_availability",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_providers.user_id"), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=False),
        sa.Column("end_minute", sa.Integer(), nullable=False),
        sa.Column("slot_minutes", sa.Integer(), nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name=op.f("ck_care_provider_availability_availability_weekday")),
        sa.CheckConstraint("start_minute >= 0 AND end_minute <= 1440 AND start_minute < end_minute", name=op.f("ck_care_provider_availability_availability_range")),
        sa.CheckConstraint("slot_minutes IN (30,60)", name=op.f("ck_care_provider_availability_availability_duration")))
    op.create_index("ix_care_availability_provider_weekday", "care_provider_availability", ["provider_id", "weekday"])
    op.create_table("care_provider_calendar_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_providers.user_id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("starts_at < ends_at", name=op.f("ck_care_provider_calendar_blocks_calendar_block_range")))
    op.create_index("ix_care_calendar_blocks_provider_start", "care_provider_calendar_blocks", ["provider_id", "starts_at"])
    op.create_table("care_booking_eligibility",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_episodes.id"), nullable=False),
        sa.Column("region", sa.String(2), nullable=False),
        sa.Column("service_suitable", sa.Boolean(), nullable=False),
        sa.Column("emergency_status", sa.String(16), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_care_booking_eligibility_episode_created", "care_booking_eligibility", ["episode_id", "created_at"])
    op.create_table("care_appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_episodes.id"), nullable=False),
        sa.Column("eligibility_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_booking_eligibility.id"), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_providers.user_id"), nullable=False),
        sa.Column("provider_name", sa.String(120), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("region", sa.String(2), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("starts_at < ends_at", name=op.f("ck_care_appointments_appointment_range")),
        sa.CheckConstraint("status IN ('held','confirmed','in_progress','completed','cancelled','expired')", name=op.f("ck_care_appointments_appointment_status")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_care_appointments_appointment_version")))
    op.create_index("ix_care_appointments_provider_start", "care_appointments", ["provider_id", "starts_at"])
    op.create_index("ix_care_appointments_owner_start", "care_appointments", ["owner_user_id", "starts_at"])
    op.create_index("uq_care_appointments_active_episode", "care_appointments", ["episode_id"], unique=True, postgresql_where=sa.text("status IN ('held','confirmed','in_progress')"))


def downgrade() -> None:
    op.drop_table("care_appointments")
    op.drop_table("care_booking_eligibility")
    op.drop_table("care_provider_calendar_blocks")
    op.drop_table("care_provider_availability")
