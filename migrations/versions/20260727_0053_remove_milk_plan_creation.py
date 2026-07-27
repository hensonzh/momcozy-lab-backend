"""remove milk-plan creation actions

Revision ID: 20260727_0053
Revises: 20260727_0052
Create Date: 2026-07-27 14:00:00
"""

from __future__ import annotations

from alembic import op


revision = "20260727_0053"
down_revision = "20260727_0052"
branch_labels = None
depends_on = None


REMOVED_ACTION_TYPE = "plans.milk_plan.create"
REMOVAL_ERROR_CODE = "milk_plan_creation_removed"


UPGRADE_STATEMENTS = (
    f"""
UPDATE agent_actions
SET status = 'failed',
    failed_at = COALESCE(failed_at, now()),
    error_code = '{REMOVAL_ERROR_CODE}',
    updated_at = now()
WHERE action_type = '{REMOVED_ACTION_TYPE}'
  AND status IN ('proposed', 'confirmation_required', 'confirmed', 'applying')
""",
    f"""
UPDATE agent_runs AS run
SET status = 'failed',
    completed_at = COALESCE(completed_at, now()),
    error_code = '{REMOVAL_ERROR_CODE}',
    error_details_json = jsonb_build_object(
        'reason', '{REMOVAL_ERROR_CODE}',
        'action_type', '{REMOVED_ACTION_TYPE}'
    )
FROM agent_actions AS action
WHERE action.run_id = run.id
  AND action.action_type = '{REMOVED_ACTION_TYPE}'
  AND action.status = 'failed'
  AND action.error_code = '{REMOVAL_ERROR_CODE}'
  AND run.status IN ('queued', 'running', 'waiting_for_confirmation')
""",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Retired actions cannot be made executable again after the code path is gone.
    pass
