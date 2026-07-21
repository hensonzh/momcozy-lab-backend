from importlib import import_module


MIGRATION = import_module("migrations.versions.20260721_0040_create_agent_image_accesses")


def test_agent_image_access_migration_follows_context_ledger() -> None:
    assert MIGRATION.revision == "20260721_0040"
    assert MIGRATION.down_revision == "20260720_0039"


def test_agent_image_access_migration_creates_thread_asset_binding(monkeypatch) -> None:
    calls: list[tuple[str, tuple, dict]] = []

    class FakeOp:
        def __getattr__(self, name):
            return lambda *args, **kwargs: calls.append((name, args, kwargs))

    monkeypatch.setattr(MIGRATION, "op", FakeOp())
    MIGRATION.upgrade()

    create_table = next(call for call in calls if call[0] == "create_table")
    assert create_table[1][0] == "agent_image_accesses"
    definitions = create_table[1][1:]
    assert any(getattr(item, "name", "") == "uq_agent_image_accesses_thread_asset" for item in definitions)
    assert not any(call[0] == "execute" for call in calls)

    calls.clear()
    MIGRATION.downgrade()
    assert ("drop_table", ("agent_image_accesses",), {}) in calls
