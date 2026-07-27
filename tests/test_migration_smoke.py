import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
DEPLOYED_PRODUCT_REVISION = "20260723_0047"
EXTRACTION_REVISION = "20260727_0048"
PRODUCT_HEAD_REVISION = "20260727_0049"
EXTRACTION_MIGRATION = (
    ROOT
    / "migrations"
    / "versions"
    / "20260727_0048_extract_agent_runtime.py"
)


def test_alembic_preserves_deployed_product_revision_chain() -> None:
    script = ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    )

    deployed_revision = script.get_revision(DEPLOYED_PRODUCT_REVISION)
    extraction_revision = script.get_revision(EXTRACTION_REVISION)
    head_revision = script.get_revision(PRODUCT_HEAD_REVISION)

    assert deployed_revision is not None
    assert extraction_revision is not None
    assert head_revision is not None
    assert extraction_revision.down_revision == DEPLOYED_PRODUCT_REVISION
    assert head_revision.down_revision == EXTRACTION_REVISION
    assert script.get_current_head() == PRODUCT_HEAD_REVISION
    assert not (
        ROOT
        / "migrations"
        / "versions"
        / "20260726_0001_create_product_backend_baseline.py"
    ).exists()


def test_alembic_offline_upgrade_head_preserves_product_schema() -> None:
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
        "CREATE TABLE audit_logs",
        "CREATE TABLE infant_profiles",
        "CREATE TABLE plans",
        "CREATE TABLE notifications",
        "CREATE TABLE support_tickets",
        "CREATE TABLE pumping_records",
        "CREATE TABLE feeding_records",
    ]:
        assert phrase in sql
    for retired_table in [
        "agent_runs",
        "agent_actions",
        "agent_eval_cases",
        "agent_context_items",
        "agent_memories",
        "user_facts",
    ]:
        assert f"DROP TABLE {retired_table}" in sql


def test_bridge_upgrade_retires_runtime_tables_without_dropping_product_tables() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            f"{DEPLOYED_PRODUCT_REVISION}:head",
            "--sql",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    sql = result.stdout

    for retired_table in [
        "agent_actions",
        "agent_artifacts",
        "agent_context_items",
        "agent_eval_cases",
        "agent_events",
        "agent_image_accesses",
        "agent_memories",
        "agent_memory_consolidation_runs",
        "agent_memory_settings",
        "agent_memory_snapshots",
        "agent_messages",
        "agent_model_context_snapshots",
        "agent_run_summaries",
        "agent_runs",
        "agent_threads",
        "agent_tool_calls",
        "agent_tool_outputs",
        "agent_workflow_events",
        "agent_workflow_states",
        "legacy_agent_context_checkpoints",
        "user_fact_extraction_runs",
        "user_facts",
    ]:
        assert f"DROP TABLE {retired_table}" in sql

    for product_table in [
        "users",
        "files",
        "audit_logs",
        "idempotency_keys",
        "plans",
        "notifications",
        "support_tickets",
    ]:
        assert f"DROP TABLE {product_table}" not in sql

    assert EXTRACTION_MIGRATION.exists()
