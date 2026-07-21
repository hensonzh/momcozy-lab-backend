from importlib import import_module


MIGRATION = import_module("migrations.versions.20260720_0039_create_agent_context_items")


def test_agent_context_item_migration_follows_current_head() -> None:
    assert MIGRATION.revision == "20260720_0039"
    assert MIGRATION.down_revision == "20260720_0038"


def test_agent_context_item_migration_creates_ledger_without_backfilling_legacy_messages(monkeypatch) -> None:
    calls: list[tuple[str, tuple, dict]] = []

    class FakeOp:
        def __getattr__(self, name):
            return lambda *args, **kwargs: calls.append((name, args, kwargs))

    monkeypatch.setattr(MIGRATION, "op", FakeOp())
    MIGRATION.upgrade()

    create_table = next(call for call in calls if call[0] == "create_table")
    assert create_table[1][0] == "agent_context_items"
    constraints = create_table[1][1:]
    assert any(getattr(item, "name", "") == "uq_agent_context_items_thread_sequence" for item in constraints)
    assert any(getattr(item, "name", "") == "uq_agent_context_items_thread_item_key" for item in constraints)
    assert not any(call[0] == "execute" for call in calls)
    assert ("drop_table", ("agent_context_projections",), {}) in calls

    calls.clear()
    MIGRATION.downgrade()
    assert any(call[0] == "create_table" and call[1][0] == "agent_context_projections" for call in calls)
    assert ("drop_table", ("agent_context_items",), {}) in calls
