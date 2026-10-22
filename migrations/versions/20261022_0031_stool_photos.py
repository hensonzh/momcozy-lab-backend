"""Private stool original and durable, fail-closed image review queue."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "20261022_0031"
down_revision = "20261022_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stool_photos",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("object_key", sa.String(512), unique=True, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("review_status", sa.String(24), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(80)),
        sa.Column("review_sha256", sa.String(64)),
        sa.Column("review_attempt", sa.Integer(), nullable=False),
        sa.Column("review_started_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_stool_photos_review_queue", "stool_photos", ["review_status", "review_started_at"])
    op.create_index("ix_stool_photos_owner", "stool_photos", ["owner_user_id", "created_at"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM stool_photos)")).scalar():
        raise RuntimeError("Stool photos exist; preserve private data and roll forward instead.")
    op.drop_index("ix_stool_photos_owner", table_name="stool_photos")
    op.drop_index("ix_stool_photos_review_queue", table_name="stool_photos")
    op.drop_table("stool_photos")
