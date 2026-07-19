"""migrate agent action outbox work to the agent run queue

Revision ID: 20260712_0031
Revises: 20260712_0030
Create Date: 2026-07-12 18:00:00
"""

from __future__ import annotations

from alembic import op


revision = "20260712_0031"
down_revision = "20260712_0030"
branch_labels = None
depends_on = None


LEGACY_AGENT_ACTION_JOB_TYPE = "agent.action.apply"
MIGRATION_MARKER = "migrated_to_agent_run_executor"


UPGRADE_STATEMENTS = (
    f"""
DO $migration$
DECLARE
    invalid_job_count bigint;
BEGIN
    SELECT COUNT(DISTINCT j.id)
    INTO invalid_job_count
    FROM outbox_jobs AS j
    LEFT JOIN agent_actions AS a
      ON j.action_id = a.id
      OR (j.action_id IS NULL AND j.payload_json ->> 'action_id' = a.id::text)
    LEFT JOIN agent_runs AS r ON r.id = a.run_id
    WHERE j.job_type = '{LEGACY_AGENT_ACTION_JOB_TYPE}'
      AND j.status IN ('queued', 'locked', 'processing', 'retry')
      AND (
        a.id IS NULL
        OR a.status NOT IN ('confirmed', 'applying', 'applied', 'failed')
        OR (
          a.status IN ('confirmed', 'applying')
          AND (
            r.id IS NULL
            OR r.status NOT IN ('waiting_for_confirmation', 'running', 'queued')
          )
        )
      );

    IF invalid_job_count > 0 THEN
        RAISE EXCEPTION 'agent action outbox migration found % orphan, terminal-run, or unrecoverable active job(s)', invalid_job_count
            USING ERRCODE = '23514';
    END IF;
END
$migration$;
""",
    f"""
WITH recoverable AS (
    SELECT DISTINCT a.id AS action_id, a.run_id
    FROM agent_actions AS a
    JOIN outbox_jobs AS j
      ON j.action_id = a.id
      OR (j.action_id IS NULL AND j.payload_json ->> 'action_id' = a.id::text)
    WHERE j.job_type = '{LEGACY_AGENT_ACTION_JOB_TYPE}'
      AND j.status IN ('queued', 'locked', 'processing', 'retry')
      AND a.status IN ('confirmed', 'applying')
)
UPDATE agent_actions AS a
SET status = 'confirmed',
    error_code = '',
    failed_at = NULL,
    updated_at = now()
FROM recoverable
WHERE a.id = recoverable.action_id;
""",
    f"""
WITH recoverable AS (
    SELECT DISTINCT a.run_id
    FROM agent_actions AS a
    JOIN outbox_jobs AS j
      ON j.action_id = a.id
      OR (j.action_id IS NULL AND j.payload_json ->> 'action_id' = a.id::text)
    WHERE j.job_type = '{LEGACY_AGENT_ACTION_JOB_TYPE}'
      AND j.status IN ('queued', 'locked', 'processing', 'retry')
      AND a.status IN ('confirmed', 'applied', 'failed')
)
UPDATE agent_runs AS r
SET status = 'queued',
    started_at = NULL,
    completed_at = NULL,
    error_code = '',
    error_details_json = '{{}}'::jsonb
FROM recoverable
WHERE r.id = recoverable.run_id
  AND r.status IN ('waiting_for_confirmation', 'running', 'queued');
""",
    f"""
UPDATE outbox_jobs AS j
SET status = 'completed',
    locked_until = NULL,
    error_code = '{MIGRATION_MARKER}',
    updated_at = now()
FROM agent_actions AS a
WHERE j.job_type = '{LEGACY_AGENT_ACTION_JOB_TYPE}'
  AND j.status IN ('queued', 'locked', 'processing', 'retry')
  AND (j.action_id = a.id OR (j.action_id IS NULL AND j.payload_json ->> 'action_id' = a.id::text))
  AND a.status IN ('confirmed', 'applying', 'applied', 'failed');
""",
    f"""
DO $migration$
DECLARE
    leftover_job_count bigint;
BEGIN
    SELECT COUNT(*)
    INTO leftover_job_count
    FROM outbox_jobs AS j
    WHERE j.job_type = '{LEGACY_AGENT_ACTION_JOB_TYPE}'
      AND j.status IN ('queued', 'locked', 'processing', 'retry');

    IF leftover_job_count > 0 THEN
        RAISE EXCEPTION 'agent action outbox migration left % active job(s) behind', leftover_job_count
            USING ERRCODE = '23514';
    END IF;
END
$migration$;
""",
)
UPGRADE_SQL = "\n".join(UPGRADE_STATEMENTS)


DOWNGRADE_STATEMENTS = (
    f"""
DO $migration$
DECLARE
    undrained_run_count bigint;
BEGIN
    SELECT COUNT(DISTINCT r.id)
    INTO undrained_run_count
    FROM outbox_jobs AS j
    JOIN agent_actions AS a
      ON j.action_id = a.id
      OR (j.action_id IS NULL AND j.payload_json ->> 'action_id' = a.id::text)
    JOIN agent_runs AS r ON r.id = a.run_id
    WHERE j.job_type = '{LEGACY_AGENT_ACTION_JOB_TYPE}'
      AND j.status = 'completed'
      AND j.error_code = '{MIGRATION_MARKER}'
      AND a.status IN ('applied', 'failed')
      AND r.status IN ('queued', 'running', 'waiting_for_confirmation');

    IF undrained_run_count > 0 THEN
        RAISE EXCEPTION 'agent action executor downgrade requires % applied/failed run(s) to drain first', undrained_run_count
            USING ERRCODE = '23514';
    END IF;
END
$migration$;
""",
    f"""
WITH recoverable AS (
    SELECT DISTINCT a.run_id
    FROM agent_actions AS a
    JOIN outbox_jobs AS j
      ON j.action_id = a.id
      OR (j.action_id IS NULL AND j.payload_json ->> 'action_id' = a.id::text)
    WHERE j.job_type = '{LEGACY_AGENT_ACTION_JOB_TYPE}'
      AND j.status = 'completed'
      AND j.error_code = '{MIGRATION_MARKER}'
      AND a.status IN ('confirmed', 'applying')
)
UPDATE agent_runs AS r
SET status = 'waiting_for_confirmation',
    started_at = NULL,
    completed_at = NULL,
    error_code = '',
    error_details_json = '{{}}'::jsonb
FROM recoverable
WHERE r.id = recoverable.run_id
  AND r.status IN ('queued', 'running', 'waiting_for_confirmation');
""",
    f"""
UPDATE outbox_jobs AS j
SET status = 'queued',
    locked_until = NULL,
    next_attempt_at = now(),
    error_code = '',
    updated_at = now()
FROM agent_actions AS a
WHERE j.job_type = '{LEGACY_AGENT_ACTION_JOB_TYPE}'
  AND j.status = 'completed'
  AND j.error_code = '{MIGRATION_MARKER}'
  AND (j.action_id = a.id OR (j.action_id IS NULL AND j.payload_json ->> 'action_id' = a.id::text))
  AND a.status IN ('confirmed', 'applying');
""",
)
DOWNGRADE_SQL = "\n".join(DOWNGRADE_STATEMENTS)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
