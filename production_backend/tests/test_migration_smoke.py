import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_alembic_offline_upgrade_head_generates_empty_database_sql() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "production_backend/alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    sql = result.stdout

    for phrase in [
        "CREATE TABLE users",
        "CREATE TABLE files",
        "CREATE TABLE agent_runs",
        "CREATE TABLE agent_eval_cases",
        "CREATE TABLE agent_workflow_states",
        "CREATE TABLE agent_context_projections",
        "CREATE TABLE agent_run_summaries",
        "CREATE TABLE agent_memories",
        "CREATE TABLE agent_routing_decisions",
        "CREATE UNIQUE INDEX uq_agent_runs_thread_active",
        "CREATE INDEX ix_agent_runs_service_skill_started",
        "CREATE INDEX ix_agent_runs_runnable_created",
        "ADD COLUMN password_hash",
        "ADD COLUMN actor_service",
        "UPDATE alembic_version SET version_num='20260702_0020'",
        "UPDATE alembic_version SET version_num='20260702_0025'",
        "UPDATE alembic_version SET version_num='20260702_0026'",
    ]:
        assert phrase in sql
