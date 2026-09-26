"""Clarify cesarean history as occurring before the current delivery.

A legacy true value paired with a current cesarean does not prove a previous
cesarean. Mark those non-first deliveries unknown rather than guessing.
"""

from alembic import op

revision = "20260926_0022"
down_revision = "20260920_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f("ck_maternal_profiles_cesarean_history"), "maternal_profiles", type_="check")
    op.execute(
        "UPDATE maternal_profiles SET has_cesarean_history = NULL "
        "WHERE latest_delivery_method = 'cesarean' "
        "AND has_cesarean_history IS TRUE AND (delivery_count IS NULL OR delivery_count > 1)"
    )
    op.execute(
        "UPDATE maternal_profiles SET has_cesarean_history = FALSE "
        "WHERE delivery_count = 1"
    )
    op.create_check_constraint(
        op.f("ck_maternal_profiles_first_delivery_prior_cesarean"),
        "maternal_profiles",
        "delivery_count IS NULL OR delivery_count <> 1 OR has_cesarean_history IS NOT TRUE",
    )


def downgrade():
    raise RuntimeError("Prior cesarean history semantics cannot be safely reversed; roll forward instead.")
