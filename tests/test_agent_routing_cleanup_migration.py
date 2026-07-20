from importlib import import_module


MIGRATION = import_module("migrations.versions.20260720_0037_drop_unused_agent_routing")


def test_agent_routing_cleanup_migration_follows_current_head() -> None:
    assert MIGRATION.revision == "20260720_0037"
    assert MIGRATION.down_revision == "20260716_0036"


def test_agent_routing_cleanup_migration_drops_and_can_restore_schema(monkeypatch) -> None:
    calls: list[tuple[str, tuple, dict]] = []

    class FakeOp:
        def __getattr__(self, name):
            return lambda *args, **kwargs: calls.append((name, args, kwargs))

    monkeypatch.setattr(MIGRATION, "op", FakeOp())
    MIGRATION.upgrade()

    assert ("drop_table", ("agent_routing_decisions",), {}) in calls
    assert [call[1][1] for call in calls if call[0] == "drop_column"] == [
        "routing_summary_json",
        "routing_confidence_score",
        "routing_source",
    ]

    calls.clear()
    MIGRATION.downgrade()
    assert any(call[0] == "create_table" and call[1][0] == "agent_routing_decisions" for call in calls)
    assert len([call for call in calls if call[0] == "add_column" and call[1][0] == "agent_runs"]) == 3
