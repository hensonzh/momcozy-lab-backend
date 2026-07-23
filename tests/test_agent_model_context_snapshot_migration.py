from importlib import import_module


MIGRATION = import_module(
    "migrations.versions.20260723_0044_create_agent_model_context_snapshots"
)


def test_agent_model_context_snapshot_migration_follows_workflow_events() -> None:
    assert MIGRATION.revision == "20260723_0044"
    assert MIGRATION.down_revision == "20260723_0043"


def test_agent_model_context_snapshot_migration_creates_replayable_refs(monkeypatch) -> None:
    calls: list[tuple[str, tuple, dict]] = []

    class FakeOp:
        def __getattr__(self, name):
            return lambda *args, **kwargs: calls.append((name, args, kwargs))

    monkeypatch.setattr(MIGRATION, "op", FakeOp())
    MIGRATION.upgrade()

    create_table = next(call for call in calls if call[0] == "create_table")
    assert create_table[1][0] == "agent_model_context_snapshots"
    definitions = create_table[1][1:]
    assert any(
        getattr(item, "name", "") == "uq_agent_model_context_snapshots_run_sequence"
        for item in definitions
    )
    assert any(
        call[0] == "create_index"
        and call[1][0] == "ix_agent_model_context_snapshots_owner_created"
        for call in calls
    )

    calls.clear()
    MIGRATION.downgrade()
    assert ("drop_table", ("agent_model_context_snapshots",), {}) in calls
