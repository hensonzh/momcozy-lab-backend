"""Durable receipts for replay-safe Agent batch writes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "20260927_0024"
down_revision = "20260926_0023"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "agent_batch_receipts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("result", pg.JSONB(), nullable=False),
    )

def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM agent_batch_receipts)")).scalar():
        raise RuntimeError("Agent batch receipts exist; preserve them and roll forward instead.")
    op.drop_table("agent_batch_receipts")
