import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_HEAD_REVISION = "20260727_0001"


def test_alembic_has_one_fresh_product_baseline() -> None:
    script = ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    )

    head_revision = script.get_revision(PRODUCT_HEAD_REVISION)

    assert head_revision is not None
    assert head_revision.down_revision is None
    assert script.get_current_head() == PRODUCT_HEAD_REVISION
    assert [revision.revision for revision in script.walk_revisions()] == [
        PRODUCT_HEAD_REVISION
    ]


def test_alembic_offline_upgrade_head_creates_final_product_schema() -> None:
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
        "CREATE TABLE diary_entries",
        "attributes_json JSONB",
        "starts_on DATE",
        "ends_on DATE",
        "CREATE UNIQUE INDEX uq_plans_owner_active_pregnancy",
        "CONSTRAINT ck_maternal_profiles_cesarean_history",
        "CREATE TABLE maternal_current_delivery_infants",
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
        assert f"CREATE TABLE {retired_table}" not in sql
    for legacy_fragment in [
        "pregnancy_diary_entries",
        "gestational_week",
        "safe_output_json",
        "ALTER TABLE",
        "DROP TABLE",
        "RENAME",
    ]:
        assert legacy_fragment not in sql
