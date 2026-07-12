import importlib


MIGRATION = importlib.import_module(
    "production_backend.migrations.versions.20260712_0031_migrate_agent_action_outbox_to_run_queue"
)


def test_agent_action_outbox_upgrade_fails_closed_before_recovering_or_terminating_jobs() -> None:
    sql = MIGRATION.UPGRADE_SQL

    assert MIGRATION.down_revision == "20260712_0030"
    assert sql.startswith("\nDO $migration$")
    assert "LEFT JOIN agent_actions AS a" in sql
    assert "LEFT JOIN agent_runs AS r ON r.id = a.run_id" in sql
    assert "COUNT(DISTINCT j.id)" in sql
    assert "a.id IS NULL" in sql
    assert "a.status NOT IN ('confirmed', 'applying', 'applied', 'failed')" in sql
    assert "a.status IN ('confirmed', 'applying')" in sql
    assert "r.id IS NULL" in sql
    assert "r.status NOT IN ('waiting_for_confirmation', 'running', 'queued')" in sql
    assert "RAISE EXCEPTION 'agent action outbox migration found % orphan, terminal-run, or unrecoverable active job(s)'" in sql
    assert "j.job_type = 'agent.action.apply'" in sql
    assert "a.status IN ('confirmed', 'applying')" in sql
    assert "a.status IN ('confirmed', 'applied', 'failed')" in sql
    assert "SET status = 'confirmed'" in sql
    assert "SET status = 'queued'" in sql
    assert "r.status IN ('waiting_for_confirmation', 'running', 'queued')" in sql
    assert sql.index("RAISE EXCEPTION") < sql.index("UPDATE agent_actions")
    assert sql.index("UPDATE agent_runs") < sql.index("UPDATE outbox_jobs AS j")
    assert "error_code = 'migrated_to_agent_run_executor'" in sql
    assert "status IN ('queued', 'locked', 'processing', 'retry')" in sql
    assert "a.status IN ('confirmed', 'applying', 'applied', 'failed')" in sql
    assert "agent action outbox migration left % active job(s) behind" in sql
    assert len(MIGRATION.UPGRADE_STATEMENTS) == 5


def test_agent_action_outbox_downgrade_only_requeues_unapplied_migrated_jobs() -> None:
    sql = MIGRATION.DOWNGRADE_SQL

    assert "j.error_code = 'migrated_to_agent_run_executor'" in sql
    assert "a.status IN ('confirmed', 'applying')" in sql
    assert "SET status = 'waiting_for_confirmation'" in sql
    assert "SET status = 'queued'" in sql
    assert "next_attempt_at = now()" in sql
    assert "a.status = 'applied'" not in sql


def test_agent_action_outbox_downgrade_fails_closed_until_applied_or_failed_runs_are_terminal() -> None:
    sql = MIGRATION.DOWNGRADE_SQL

    assert sql.startswith("\nDO $migration$")
    assert "a.status IN ('applied', 'failed')" in sql
    assert "r.status IN ('queued', 'running', 'waiting_for_confirmation')" in sql
    assert "RAISE EXCEPTION 'agent action executor downgrade requires % applied/failed run(s) to drain first'" in sql
    assert sql.index("RAISE EXCEPTION") < sql.index("UPDATE agent_runs")
    assert len(MIGRATION.DOWNGRADE_STATEMENTS) == 3
