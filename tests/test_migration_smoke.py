import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_BASE_REVISION = "20260727_0001"
PRODUCT_HEAD_REVISION = "20260910_0018"


def test_alembic_has_one_linear_product_migration_chain() -> None:
    script = ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    )

    head_revision = script.get_revision(PRODUCT_BASE_REVISION)

    assert head_revision is not None
    assert head_revision.down_revision is None
    assert script.get_current_head() == PRODUCT_HEAD_REVISION
    assert [revision.revision for revision in script.walk_revisions()] == [
        PRODUCT_HEAD_REVISION, "20260910_0017", "20260909_0016", "20260909_0015", "20260908_0014", "20260908_0013", "20260908_0012", "20260908_0011", "20260908_0010", "20260908_0009", "20260908_0008", "20260908_0007", "20260908_0006", "20260908_0005", "20260908_0004", "20260908_0003", "20260908_0002", PRODUCT_BASE_REVISION
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
        "CREATE TABLE push_installations",
        "CREATE TABLE notification_deliveries",
        "CREATE TABLE notification_preferences",
        "CREATE TABLE notification_event_receipts",
        "CREATE TABLE support_tickets",
        "CREATE TABLE pumping_records",
        "CREATE TABLE mother_diary_entries",
        "CREATE TABLE lactation_records",
        "CREATE TABLE care_episodes",
        "CREATE TABLE care_orders",
        "CREATE TABLE care_appointments",
        "CREATE TABLE care_intake_revisions",
        "CREATE TABLE care_consent_revisions",
        "CREATE TABLE care_consultations",
        "CREATE TABLE care_video_commands",
        "CREATE TABLE care_clinical_notes",
        "CREATE TABLE care_plan_publications",
        "CREATE TABLE care_task_progress",
        "CREATE TABLE care_conversation_links",
        "CREATE TABLE care_reports",
        "CREATE TABLE baby_records",
        "ALTER TABLE infant_profiles RENAME TO baby_profiles",
        "DROP COLUMN birth_weight_kg",
        "DROP COLUMN gestational_age_at_birth_days",
        "CREATE TABLE care_report_reviews",
        "CREATE TABLE care_service_events",
        "CREATE TABLE care_service_event_reads",
        "CREATE TABLE workbench_mfa_credentials",
        "CREATE TABLE workbench_login_challenges",
        "ADD COLUMN mfa_verified_at",
        "CREATE TABLE care_provider_availability",
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
        "DROP TABLE",
    ]:
        assert legacy_fragment not in sql
