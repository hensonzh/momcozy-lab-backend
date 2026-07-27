from importlib import import_module
from pathlib import Path


def test_agent_action_result_migration_extends_canonical_tool_output_head() -> None:
    migration = import_module(
        "migrations.versions.20260727_0051_persist_agent_action_results"
    )
    source = (
        Path(__file__).parents[1]
        / "migrations/versions/20260727_0051_persist_agent_action_results.py"
    ).read_text()

    assert migration.revision == "20260727_0051"
    assert migration.down_revision == "20260726_0050"
    assert "result_payload_json" in source
    assert "agent_actions" in source
