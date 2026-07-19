from importlib import import_module


MIGRATION = import_module("migrations.versions.20260713_0033_add_agent_workflow_reply_cursor")


def test_agent_workflow_reply_cursor_migration_follows_current_head() -> None:
    assert MIGRATION.revision == "20260713_0033"
    assert MIGRATION.down_revision == "20260712_0032"
