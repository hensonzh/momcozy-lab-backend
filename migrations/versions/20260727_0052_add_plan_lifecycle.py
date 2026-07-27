"""add plan effective dates and active-pregnancy uniqueness

Revision ID: 20260727_0052
Revises: 20260727_0051
Create Date: 2026-07-27 11:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260727_0052"
down_revision = "20260727_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("starts_on", sa.Date(), nullable=True))
    op.add_column("plans", sa.Column("ends_on", sa.Date(), nullable=True))

    op.execute(
        """
        UPDATE plans
        SET starts_on = CASE
            WHEN pg_input_is_valid(payload_json ->> 'start_date', 'date')
            THEN (payload_json ->> 'start_date')::date
            ELSE NULL
        END
        WHERE payload_json ->> 'start_date'
              ~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$'
        """
    )
    op.execute(
        """
        UPDATE plans
        SET ends_on = CASE
            WHEN pg_input_is_valid(payload_json ->> 'end_date', 'date')
            THEN (payload_json ->> 'end_date')::date
            ELSE NULL
        END
        WHERE payload_json ->> 'end_date'
              ~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$'
        """
    )
    op.execute(
        """
        UPDATE plans
        SET ends_on = starts_on + ((payload_json ->> 'days')::integer - 1)
        WHERE starts_on IS NOT NULL
          AND ends_on IS NULL
          AND payload_json ->> 'days' ~ '^[0-9]{1,4}$'
          AND (payload_json ->> 'days')::integer BETWEEN 1 AND 3660
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY owner_user_id
                       ORDER BY updated_at DESC, id DESC
                   ) AS position
            FROM plans
            WHERE plan_type = 'pregnancy'
              AND status = 'active'
              AND deleted_at IS NULL
        )
        UPDATE plans AS plan
        SET status = 'superseded',
            updated_at = now()
        FROM ranked
        WHERE plan.id = ranked.id
          AND ranked.position > 1
        """
    )
    op.create_index(
        "uq_plans_owner_active_pregnancy",
        "plans",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text(
            "plan_type = 'pregnancy' AND status = 'active' AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_plans_owner_active_pregnancy", table_name="plans")
    op.drop_column("plans", "ends_on")
    op.drop_column("plans", "starts_on")
