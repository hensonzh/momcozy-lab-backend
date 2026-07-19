"""create owner-scoped files

Revision ID: 20260702_0002
Revises: 20260702_0001
Create Date: 2026-07-02 00:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260702_0002"
down_revision = "20260702_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_files_owner_user_id_users"),
    )
    op.create_index("ix_files_owner_status_created", "files", ["owner_user_id", "status", "created_at"])
    op.create_index("ix_files_object_key", "files", ["object_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_files_object_key", table_name="files")
    op.drop_index("ix_files_owner_status_created", table_name="files")
    op.drop_table("files")
