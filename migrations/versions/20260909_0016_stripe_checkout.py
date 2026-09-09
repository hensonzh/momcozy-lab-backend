"""Persist the Stripe Checkout identity and isolate test payments."""
from alembic import op
import sqlalchemy as sa

revision = "20260909_0016"
down_revision = "20260909_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("care_orders", sa.Column("stripe_session_id", sa.String(255), nullable=True))
    op.add_column("care_orders", sa.Column("stripe_livemode", sa.Boolean(), nullable=True))
    op.create_unique_constraint("uq_care_orders_stripe_session_id", "care_orders", ["stripe_session_id"])


def downgrade() -> None:
    op.drop_constraint("uq_care_orders_stripe_session_id", "care_orders", type_="unique")
    op.drop_column("care_orders", "stripe_livemode")
    op.drop_column("care_orders", "stripe_session_id")
