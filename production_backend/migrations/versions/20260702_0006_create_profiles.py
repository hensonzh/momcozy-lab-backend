"""create user and infant profiles

Revision ID: 20260702_0006
Revises: 20260702_0005
Create Date: 2026-07-02 00:50:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0006"
down_revision = "20260702_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("delivery_date", sa.Date(), nullable=True),
        sa.Column("lactation_advice", sa.Text(), nullable=False, server_default=""),
        sa.Column("feeding_advice", sa.Text(), nullable=False, server_default=""),
        sa.Column("daily_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("profile_onboarding_skipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profile_onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_profiles_user_id_users"),
        sa.UniqueConstraint("user_id", name="uq_user_profiles_user_id"),
    )
    op.create_index("ix_user_profiles_delivery_date", "user_profiles", ["delivery_date"])

    op.create_table(
        "infant_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("infant_name", sa.String(length=120), nullable=False),
        sa.Column("sex", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_infant_profiles_owner_user_id_users"),
    )
    op.create_index("ix_infant_profiles_owner_status", "infant_profiles", ["owner_user_id", "status"])
    op.create_index("ix_infant_profiles_birth_date", "infant_profiles", ["birth_date"])


def downgrade() -> None:
    op.drop_index("ix_infant_profiles_birth_date", table_name="infant_profiles")
    op.drop_index("ix_infant_profiles_owner_status", table_name="infant_profiles")
    op.drop_table("infant_profiles")
    op.drop_index("ix_user_profiles_delivery_date", table_name="user_profiles")
    op.drop_table("user_profiles")
