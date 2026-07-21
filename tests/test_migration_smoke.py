import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_ARCHIVE_MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260716_0035_archive_agent_context_checkpoints.py"
)
RUNTIME_CONTRACT_MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260716_0036_rename_agent_runtime_contract.py"
)


def test_alembic_offline_upgrade_head_generates_empty_database_sql() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
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
        "CREATE TABLE agent_context_items",
        "CREATE TABLE agent_image_accesses",
        "CREATE TABLE agent_eval_cases",
        "CREATE TABLE agent_workflow_states",
        "CREATE TABLE agent_context_projections",
        "CREATE TABLE agent_run_summaries",
        "CREATE TABLE agent_memories",
        "CREATE TABLE agent_routing_decisions",
        "CREATE TABLE user_facts",
        "CREATE TABLE user_fact_extraction_runs",
        "CONSTRAINT uq_user_facts_owner_key_kind UNIQUE",
        "CREATE INDEX ix_user_fact_extractions_status_next_attempt",
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
    assert "DROP TABLE agent_context_projections" in sql
    assert "DROP TABLE outbox_jobs" in sql


def test_checkpoint_retirement_archives_existing_rows_without_dropping_them() -> None:
    source = CHECKPOINT_ARCHIVE_MIGRATION.read_text()

    assert 'op.rename_table("agent_context_checkpoints", "legacy_agent_context_checkpoints")' in source
    assert 'op.rename_table("legacy_agent_context_checkpoints", "agent_context_checkpoints")' in source
    assert "op.drop_table" not in source


def test_runtime_contract_migration_is_data_preserving_and_reversible() -> None:
    source = RUNTIME_CONTRACT_MIGRATION.read_text()

    assert '"graph_version",\n        new_column_name="runtime_version"' in source
    assert '"runtime_version",\n        new_column_name="graph_version"' in source
    assert "SET runtime_pattern = 'sdk_only' WHERE runtime_pattern = 'langgraph_sdk'" in source
    assert "SET runtime_pattern = 'langgraph_sdk' WHERE runtime_pattern = 'sdk_only'" in source
    assert "op.drop_column" not in source
    assert "op.drop_table" not in source
