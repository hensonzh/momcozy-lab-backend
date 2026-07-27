from pathlib import Path

from app.agents.cozymate.actions import COZYMATE_ACTION_RULES
from app.agents.cozymate.tools import default_tool_registry
from app.infrastructure.db.base import Base
from app.modules.diary.models import DiaryEntry


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "20260726_0048_generalize_diary.py"


def test_generic_diary_tools_replace_pregnancy_specific_tools() -> None:
    registry = default_tool_registry()
    visible_tools = set(registry.names_for_sdk())

    assert {"diary_read", "diary_mutate"} <= visible_tools
    assert {"pregnancy_diary_read", "pregnancy_diary_mutate"}.isdisjoint(visible_tools)
    assert registry.get("diary_read").domain == "diary"
    assert registry.get("diary_mutate").domain == "diary"
    assert registry.get("diary_mutate").action_types == (
        "diary.entry.save",
        "diary.entry.delete",
    )
    assert {"diary.entry.save", "diary.entry.delete"} <= set(COZYMATE_ACTION_RULES)


def test_generic_diary_tools_do_not_partition_entries_by_type() -> None:
    registry = default_tool_registry()
    read_contract = registry.get("diary_read")
    mutate_contract = registry.get("diary_mutate")

    assert read_contract.input_schema["anyOf"][1].get("required", []) == []
    assert all(
        "operation" in variant["required"]
        for variant in mutate_contract.input_schema["anyOf"]
    )
    for contract in (read_contract, mutate_contract):
        assert all(
            "diary_type" not in variant["properties"]
            for variant in contract.input_schema["anyOf"]
        )
        assert "diary_type" not in contract.output_schema["properties"]
        assert "diary_type" not in contract.output_schema.get("required", [])


def test_generic_diary_table_is_scoped_by_owner_and_date() -> None:
    table = Base.metadata.tables["diary_entries"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert DiaryEntry.__table__ is table
    assert {"owner_user_id", "entry_date", "attributes_json"} <= set(table.columns.keys())
    assert "diary_type" not in table.columns
    assert "uq_diary_entries_owner_date" in constraint_names
    assert "uq_diary_entries_owner_type_date" not in constraint_names
    assert "ck_diary_entries_type" not in constraint_names


def test_generic_diary_migration_preserves_pregnancy_entries() -> None:
    source = MIGRATION.read_text()

    assert 'revision = "20260726_0048"' in source
    assert 'down_revision = "20260723_0047"' in source
    assert 'op.rename_table("pregnancy_diary_entries", "diary_entries")' in source
    assert "diary_type" not in source
    assert "jsonb_build_object" in source
    assert '"gestational_week",' in source
    assert 'op.drop_column("diary_entries", column_name)' in source
    assert "uq_diary_entries_owner_date" in source
    assert "ix_diary_entries_owner_date" in source
