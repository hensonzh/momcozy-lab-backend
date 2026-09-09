"""Versioned intake snapshots and explicit episode-scoped consent history."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260908_0006"
down_revision = "20260908_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("care_appointments", sa.Column("intake_version", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("care_appointments", "intake_version", server_default=None)
    op.create_table("care_intake_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_appointments.id"), nullable=False),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_episodes.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("symptoms", postgresql.JSONB(), nullable=False),
        sa.Column("feeding_goal", sa.Text(), nullable=False),
        sa.Column("support_needed", sa.Text(), nullable=False),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("appointment_id", "version", name=op.f("uq_care_intake_revisions_appointment_id")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_care_intake_revisions_intake_version")))
    op.create_index("ix_care_intake_episode_submitted", "care_intake_revisions", ["episode_id", "submitted_at"])
    op.create_table("care_consent_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_episodes.id"), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("episode_id", "scope", "version", name=op.f("uq_care_consent_revisions_episode_id")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_care_consent_revisions_consent_version")),
        sa.CheckConstraint("scope IN ('ibclc_case','video','ai_context','notifications')", name=op.f("ck_care_consent_revisions_consent_scope")))


def downgrade() -> None:
    op.drop_table("care_consent_revisions")
    op.drop_table("care_intake_revisions")
    op.drop_column("care_appointments", "intake_version")
