from importlib import import_module
from pathlib import Path


def test_canonical_tool_output_migration_extends_profile_contract_head() -> None:
    migration = import_module(
        "migrations.versions.20260726_0050_canonicalize_agent_tool_outputs"
    )
    source = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/20260726_0050_canonicalize_agent_tool_outputs.py"
    ).read_text()

    assert migration.revision == "20260726_0050"
    assert migration.down_revision == "20260726_0049"
    assert '"safe_output_json"' in source
    assert 'new_column_name="output_json"' in source
    assert '"raw_output_ref"' in source
    assert 'new_column_name="output_ref"' in source
