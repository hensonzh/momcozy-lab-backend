from importlib import import_module


MIGRATION = import_module("migrations.versions.20260723_0043_create_agent_workflow_events")


def test_agent_workflow_event_migration_follows_current_head() -> None:
    assert MIGRATION.revision == "20260723_0043"
    assert MIGRATION.down_revision == "20260722_0042"


def test_agent_workflow_event_migration_creates_append_only_ledger(monkeypatch) -> None:
    calls: list[tuple[str, tuple, dict]] = []

    class FakeOp:
        def __getattr__(self, name):
            return lambda *args, **kwargs: calls.append((name, args, kwargs))

    monkeypatch.setattr(MIGRATION, "op", FakeOp())
    MIGRATION.upgrade()

    create_table = next(call for call in calls if call[0] == "create_table")
    assert create_table[1][0] == "agent_workflow_events"
    definitions = create_table[1][1:]
    assert any(getattr(item, "name", "") == "uq_agent_workflow_events_state_sequence" for item in definitions)
    assert any(
        call[0] == "create_index" and call[1][0] == "ix_agent_workflow_events_owner_type_created"
        for call in calls
    )
    assert any(
        call[0] == "create_index" and call[1][0] == "uq_agent_workflow_states_owner_type_active"
        for call in calls
    )
    migration_sql = "\n".join(
        str(call[1][0])
        for call in calls
        if call[0] == "execute"
    )
    assert "requires an empty pregnancy-plan workflow state set" in migration_sql
    assert "expires_at = NULL" not in migration_sql
    assert "HAVING count(*) > 1" not in migration_sql

    calls.clear()
    MIGRATION.downgrade()
    assert ("drop_table", ("agent_workflow_events",), {}) in calls
