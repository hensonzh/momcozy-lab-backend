from importlib import import_module
from pathlib import Path


def test_plan_lifecycle_migration_extends_action_result_head() -> None:
    migration = import_module(
        "migrations.versions.20260727_0052_add_plan_lifecycle"
    )
    source = (
        Path(__file__).parents[1]
        / "migrations/versions/20260727_0052_add_plan_lifecycle.py"
    ).read_text()

    assert migration.revision == "20260727_0052"
    assert migration.down_revision == "20260727_0051"
    assert "starts_on" in source
    assert "ends_on" in source
    assert "uq_plans_owner_active_pregnancy" in source
    assert "superseded" in source
    assert "pg_input_is_valid" in source
    assert "to_date(" not in source
