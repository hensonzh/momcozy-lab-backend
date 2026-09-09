"""Shared care providers, eligibility, orders and service episodes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260908_0004"
down_revision = "20260908_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("care_providers",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("regions", postgresql.JSONB(), nullable=False),
        sa.Column("languages", postgresql.JSONB(), nullable=False),
        sa.Column("bio", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sandbox", sa.Boolean(), nullable=False))
    op.create_table("care_eligibility_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("package_id", sa.String(64), nullable=False),
        sa.Column("region", sa.String(2), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_care_eligibility_checks_owner_user_id", "care_eligibility_checks", ["owner_user_id"])
    op.create_table("care_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("eligibility_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_eligibility_checks.id"), nullable=False),
        sa.Column("package_id", sa.String(64), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("total_sessions", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("payment_mode", sa.String(16), nullable=False),
        sa.Column("region", sa.String(2), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("price_minor >= 0", name=op.f("ck_care_orders_care_order_price")),
        sa.CheckConstraint("status IN ('pending','processing','requires_action','reconciling','paid','failed','cancelled')", name=op.f("ck_care_orders_care_order_status")))
    op.create_index("ix_care_orders_owner_created", "care_orders", ["owner_user_id", "created_at"])
    op.create_table("care_episodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_orders.id"), nullable=False),
        sa.Column("package_id", sa.String(64), nullable=False),
        sa.Column("baby_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("infant_profiles.id")),
        sa.Column("assigned_ibclc_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("care_providers.user_id")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("total_sessions", sa.Integer(), nullable=False),
        sa.Column("remaining_sessions", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("order_id", name=op.f("uq_care_episodes_order_id")),
        sa.CheckConstraint("remaining_sessions >= 0 AND remaining_sessions <= total_sessions", name=op.f("ck_care_episodes_care_episode_sessions")),
        sa.CheckConstraint("status IN ('provisioning_pending','active','paused','completed','cancelled')", name=op.f("ck_care_episodes_care_episode_status")))
    op.create_index("ix_care_episodes_owner_status", "care_episodes", ["owner_user_id", "status"])
    op.create_index("ix_care_episodes_assigned_ibclc_id", "care_episodes", ["assigned_ibclc_id"])


def downgrade() -> None:
    op.drop_table("care_episodes")
    op.drop_table("care_orders")
    op.drop_table("care_eligibility_checks")
    op.drop_table("care_providers")
