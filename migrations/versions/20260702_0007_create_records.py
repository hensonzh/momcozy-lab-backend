"""create feeding pumping and growth records

Revision ID: 20260702_0007
Revises: 20260702_0006
Create Date: 2026-07-02 01:05:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0007"
down_revision = "20260702_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feeding_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("infant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("feed_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feed_type", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("feed_action", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("volume_ml", sa.Float(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_feeding_records_owner_user_id_users"),
        sa.ForeignKeyConstraint(["infant_id"], ["infant_profiles.id"], name="fk_feeding_records_infant_id_infant_profiles"),
    )
    op.create_index("ix_feeding_records_owner_time", "feeding_records", ["owner_user_id", "feed_time"])
    op.create_index("ix_feeding_records_owner_infant_time", "feeding_records", ["owner_user_id", "infant_id", "feed_time"])

    op.create_table(
        "pumping_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pump_start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pump_end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("milk_volume_ml", sa.Float(), nullable=True),
        sa.Column("pump_type", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_pumping_records_owner_user_id_users"),
    )
    op.create_index("ix_pumping_records_owner_start", "pumping_records", ["owner_user_id", "pump_start_time"])
    op.create_index("ix_pumping_records_owner_status", "pumping_records", ["owner_user_id", "status"])

    op.create_table(
        "growth_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("infant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("head_cm", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_growth_records_owner_user_id_users"),
        sa.ForeignKeyConstraint(["infant_id"], ["infant_profiles.id"], name="fk_growth_records_infant_id_infant_profiles"),
    )
    op.create_index("ix_growth_records_owner_measured", "growth_records", ["owner_user_id", "measured_at"])
    op.create_index("ix_growth_records_owner_infant_measured", "growth_records", ["owner_user_id", "infant_id", "measured_at"])


def downgrade() -> None:
    op.drop_index("ix_growth_records_owner_infant_measured", table_name="growth_records")
    op.drop_index("ix_growth_records_owner_measured", table_name="growth_records")
    op.drop_table("growth_records")
    op.drop_index("ix_pumping_records_owner_status", table_name="pumping_records")
    op.drop_index("ix_pumping_records_owner_start", table_name="pumping_records")
    op.drop_table("pumping_records")
    op.drop_index("ix_feeding_records_owner_infant_time", table_name="feeding_records")
    op.drop_index("ix_feeding_records_owner_time", table_name="feeding_records")
    op.drop_table("feeding_records")
