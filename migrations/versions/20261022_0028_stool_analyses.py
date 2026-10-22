"""Private final-result ledger for a future stool specialty service.

No public submission or clinical result writer is enabled by this migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "20261022_0028"
down_revision = "20261020_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stool_analyses",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("baby_id", pg.UUID(as_uuid=True), sa.ForeignKey("baby_profiles.id"), nullable=False),
        sa.Column("thread_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result_schema_version", sa.String(80)),
        sa.Column("final_result", sa.JSON()),
        sa.Column("result_validated_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("context_expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('queued','running','completed','failed','cancelled')", name="ck_stool_analyses_status"),
    )
    op.create_index("ix_stool_analyses_owner_baby", "stool_analyses", ["owner_user_id", "baby_id", "created_at"])
    op.create_table(
        "stool_analysis_photos",
        sa.Column("analysis_id", pg.UUID(as_uuid=True), sa.ForeignKey("stool_analyses.id"), primary_key=True),
        sa.Column("photo_id", pg.UUID(as_uuid=True), sa.ForeignKey("stool_photos.id"), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("original_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("analysis_id", "position", name="uq_stool_analysis_photos_position"),
        sa.CheckConstraint("position >= 0 AND position <= 2", name="ck_stool_analysis_photos_position"),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM stool_analyses)")).scalar():
        raise RuntimeError("Stool analyses exist; preserve private results and roll forward instead.")
    op.drop_table("stool_analysis_photos")
    op.drop_index("ix_stool_analyses_owner_baby", table_name="stool_analyses")
    op.drop_table("stool_analyses")
