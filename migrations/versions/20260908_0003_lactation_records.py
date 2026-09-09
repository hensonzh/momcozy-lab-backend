"""Single-side lactation events with measured pump volume or nursing duration."""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0003"
down_revision = "20260908_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("lactation_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("volume_ml", sa.Float()), sa.Column("duration_minutes", sa.Integer()),
        sa.Column("feeling", sa.String(16)), sa.Column("note", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("side IN ('left', 'right')", name="lactation_side"),
        sa.CheckConstraint("(method = 'pump' AND duration_minutes IS NULL) OR (method = 'nurse' AND volume_ml IS NULL)", name="lactation_measurement_kind"),
        sa.CheckConstraint("volume_ml IS NULL OR (volume_ml >= 0 AND volume_ml <= 2000)", name="lactation_volume"),
        sa.CheckConstraint("duration_minutes IS NULL OR (duration_minutes >= 0 AND duration_minutes <= 240)", name="lactation_duration"))
    op.create_index("ix_lactation_owner_time", "lactation_records", ["owner_user_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("lactation_records")
