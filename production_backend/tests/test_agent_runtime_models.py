from production_backend.app.infrastructure.db.base import Base
from production_backend.app.infrastructure.db import models as _models
from production_backend.app.modules.agent_runtime.models import (
    ACTION_STATUSES,
    ACTIVE_RUN_STATUSES,
    RUN_STATUSES,
    TOOL_CALL_STATUSES,
    WORKFLOW_STATE_STATUSES,
)


def test_agent_runtime_ledger_tables_are_registered() -> None:
    expected_tables = {
        "agent_threads",
        "agent_runs",
        "agent_messages",
        "agent_tool_calls",
        "agent_tool_outputs",
        "agent_events",
        "agent_artifacts",
        "agent_actions",
        "agent_context_checkpoints",
        "agent_workflow_states",
        "agent_context_projections",
    }

    assert expected_tables.issubset(Base.metadata.tables)


def test_agent_runtime_run_and_action_statuses_include_waiting_and_no_queued_action() -> None:
    assert "waiting_for_confirmation" in RUN_STATUSES
    assert "queued" in RUN_STATUSES
    assert ACTIVE_RUN_STATUSES == ("queued", "running", "waiting_for_confirmation")
    assert "queued" not in ACTION_STATUSES
    assert "confirmation_required" in ACTION_STATUSES
    assert "timed_out" in TOOL_CALL_STATUSES
    assert "collecting" in WORKFLOW_STATE_STATUSES
    assert "completed" in WORKFLOW_STATE_STATUSES


def test_agent_events_have_replay_envelope_columns_and_sequence_constraint() -> None:
    table = Base.metadata.tables["agent_events"]
    constraint_names = {constraint.name for constraint in table.constraints}
    index_names = {index.name for index in table.indexes}

    assert "event_id" in table.columns
    assert "thread_id" in table.columns
    assert "run_id" in table.columns
    assert "sequence" in table.columns
    assert "event_type" in table.columns
    assert "payload_json" in table.columns
    assert "uq_agent_events_run_sequence" in constraint_names
    assert "ix_agent_events_run_sequence" in index_names


def test_agent_ledger_has_no_provider_state_dependency_columns() -> None:
    forbidden = {
        "previous" + "_response_id",
        "provider" + "_session_id",
        "chat" + "_session_id",
    }
    for table_name in (
        "agent_threads",
        "agent_runs",
        "agent_messages",
        "agent_tool_calls",
        "agent_events",
        "agent_context_checkpoints",
        "agent_workflow_states",
        "agent_context_projections",
    ):
        columns = set(Base.metadata.tables[table_name].columns.keys())
        assert columns.isdisjoint(forbidden)


def test_agent_actions_and_checkpoints_have_runtime_indexes() -> None:
    runs = Base.metadata.tables["agent_runs"]
    actions = Base.metadata.tables["agent_actions"]
    checkpoints = Base.metadata.tables["agent_context_checkpoints"]
    workflow_states = Base.metadata.tables["agent_workflow_states"]
    context_projections = Base.metadata.tables["agent_context_projections"]

    assert "uq_agent_runs_thread_active" in {index.name for index in runs.indexes}
    assert "ix_agent_actions_run_status" in {index.name for index in actions.indexes}
    assert "ix_agent_actions_idempotency_key" in {index.name for index in actions.indexes}
    assert "uq_agent_context_checkpoints_namespace_id" in {constraint.name for constraint in checkpoints.constraints}
    assert "ix_agent_workflow_states_owner_type_status" in {index.name for index in workflow_states.indexes}
    assert "ix_agent_context_projections_run_created" in {index.name for index in context_projections.indexes}
