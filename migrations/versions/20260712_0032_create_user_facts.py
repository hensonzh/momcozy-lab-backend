"""create user facts and per-message extraction ledger

Revision ID: 20260712_0032
Revises: 20260712_0031
Create Date: 2026-07-12 20:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260712_0032"
down_revision = "20260712_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_key", sa.String(length=120), nullable=False),
        sa.Column("fact_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("value_json", postgresql.JSONB(), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("sensitivity", sa.String(length=32), nullable=False, server_default="personal"),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_reason", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("fact_kind IN ('verified', 'conversation_candidate')", name="ck_user_facts_kind"),
        sa.CheckConstraint("status IN ('active', 'tombstoned')", name="ck_user_facts_status"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_user_facts_owner_user_id_users"),
        sa.UniqueConstraint("owner_user_id", "fact_key", "fact_kind", name="uq_user_facts_owner_key_kind"),
    )
    op.create_index(
        "ix_user_facts_owner_status_kind_updated",
        "user_facts",
        ["owner_user_id", "status", "fact_kind", "updated_at"],
    )
    op.create_index("ix_user_facts_expires_at", "user_facts", ["expires_at"])
    op.create_table(
        "user_fact_extraction_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("extractor_version", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(length=16), nullable=False, server_default="extract"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("request_id", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("candidates_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("extracted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'locked', 'ready_to_apply', 'completed', 'dead_lettered', "
            "'skipped_disabled', 'cancelled')",
            name="ck_user_fact_extractions_status",
        ),
        sa.CheckConstraint("stage IN ('extract', 'apply')", name="ck_user_fact_extractions_stage"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_user_fact_extractions_owner_user_id_users"),
        sa.ForeignKeyConstraint(["source_message_id"], ["agent_messages.id"], name="fk_user_fact_extractions_source_message_id_agent_messages"),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"], name="fk_user_fact_extractions_source_run_id_agent_runs"),
        sa.UniqueConstraint(
            "owner_user_id",
            "source_message_id",
            "catalog_version",
            "extractor_version",
            name="uq_user_fact_extractions_source_version",
        ),
    )
    op.create_index(
        "ix_user_fact_extractions_owner_created",
        "user_fact_extraction_runs",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_user_fact_extractions_status_next_attempt",
        "user_fact_extraction_runs",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_user_fact_extractions_locked_until",
        "user_fact_extraction_runs",
        ["locked_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_fact_extractions_locked_until", table_name="user_fact_extraction_runs")
    op.drop_index("ix_user_fact_extractions_status_next_attempt", table_name="user_fact_extraction_runs")
    op.drop_index("ix_user_fact_extractions_owner_created", table_name="user_fact_extraction_runs")
    op.drop_table("user_fact_extraction_runs")
    op.drop_index("ix_user_facts_expires_at", table_name="user_facts")
    op.drop_index("ix_user_facts_owner_status_kind_updated", table_name="user_facts")
    op.drop_table("user_facts")
