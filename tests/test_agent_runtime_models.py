from app.infrastructure.db.base import Base
from app.infrastructure.db import models as _models
from app.agent_runtime.runs.models import (
    ACTION_STATUSES,
    ACTIVE_RUN_STATUSES,
    MEMORY_STATUSES,
    MEMORY_TYPES,
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
        "agent_workflow_states",
        "agent_workflow_events",
        "agent_model_context_snapshots",
        "agent_context_items",
        "agent_run_summaries",
        "agent_memories",
        "agent_memory_settings",
        "agent_memory_snapshots",
        "agent_memory_consolidation_runs",
        "user_facts",
        "user_fact_extraction_runs",
    }

    assert expected_tables.issubset(Base.metadata.tables)


def test_agent_tool_outputs_store_canonical_output_without_legacy_safe_fields() -> None:
    tool_outputs = Base.metadata.tables["agent_tool_outputs"]

    assert "output_json" in tool_outputs.columns
    assert "output_ref" in tool_outputs.columns
    assert "safe" + "_output_json" not in tool_outputs.columns
    assert "raw" + "_output_ref" not in tool_outputs.columns


def test_agent_runtime_run_and_action_statuses_include_waiting_and_no_queued_action() -> None:
    assert "waiting_for_confirmation" in RUN_STATUSES
    assert "queued" in RUN_STATUSES
    assert ACTIVE_RUN_STATUSES == ("queued", "running", "waiting_for_confirmation")
    assert "queued" not in ACTION_STATUSES
    assert "confirmation_required" in ACTION_STATUSES
    assert "timed_out" in TOOL_CALL_STATUSES
    assert "collecting" in WORKFLOW_STATE_STATUSES
    assert "completed" in WORKFLOW_STATE_STATUSES
    assert "user_preference" in MEMORY_TYPES
    assert "communication_preference" in MEMORY_TYPES
    assert MEMORY_STATUSES == ("active", "archived", "deleted", "expired")


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
        "agent_workflow_states",
        "agent_context_items",
        "agent_run_summaries",
        "agent_memories",
    ):
        columns = set(Base.metadata.tables[table_name].columns.keys())
        assert columns.isdisjoint(forbidden)


def test_agent_runtime_state_tables_have_required_indexes() -> None:
    runs = Base.metadata.tables["agent_runs"]
    actions = Base.metadata.tables["agent_actions"]
    workflow_states = Base.metadata.tables["agent_workflow_states"]
    workflow_events = Base.metadata.tables["agent_workflow_events"]
    model_context_snapshots = Base.metadata.tables["agent_model_context_snapshots"]
    context_items = Base.metadata.tables["agent_context_items"]
    run_summaries = Base.metadata.tables["agent_run_summaries"]
    memories = Base.metadata.tables["agent_memories"]
    memory_snapshots = Base.metadata.tables["agent_memory_snapshots"]
    memory_consolidation_runs = Base.metadata.tables["agent_memory_consolidation_runs"]
    user_facts = Base.metadata.tables["user_facts"]
    fact_extraction_runs = Base.metadata.tables["user_fact_extraction_runs"]

    assert "uq_agent_runs_thread_active" in {index.name for index in runs.indexes}
    assert "service_skill_id" in runs.columns
    assert "routing_source" not in runs.columns
    assert "routing_confidence_score" not in runs.columns
    assert "routing_summary_json" not in runs.columns
    assert "ix_agent_runs_service_skill_started" in {index.name for index in runs.indexes}
    assert "ix_agent_runs_runnable_created" in {index.name for index in runs.indexes}
    assert "ix_agent_actions_run_status" in {index.name for index in actions.indexes}
    assert "ix_agent_actions_idempotency_key" in {index.name for index in actions.indexes}
    assert "ix_agent_workflow_states_owner_type_status" in {index.name for index in workflow_states.indexes}
    assert "uq_agent_workflow_states_owner_type_active" in {index.name for index in workflow_states.indexes}
    assert "uq_agent_workflow_events_state_sequence" in {
        constraint.name for constraint in workflow_events.constraints
    }
    assert "ix_agent_workflow_events_owner_type_created" in {
        index.name for index in workflow_events.indexes
    }
    assert "uq_agent_model_context_snapshots_run_sequence" in {
        constraint.name for constraint in model_context_snapshots.constraints
    }
    assert "ix_agent_model_context_snapshots_owner_created" in {
        index.name for index in model_context_snapshots.indexes
    }
    assert "ix_agent_context_items_thread_sequence" in {index.name for index in context_items.indexes}
    assert "ix_agent_context_items_run_sequence" in {index.name for index in context_items.indexes}
    assert "uq_agent_run_summaries_run_type" in {constraint.name for constraint in run_summaries.constraints}
    assert "ix_agent_run_summaries_thread_created" in {index.name for index in run_summaries.indexes}
    assert "ix_agent_run_summaries_owner_skill_created" in {index.name for index in run_summaries.indexes}
    assert "ix_agent_memories_owner_type_status" in {index.name for index in memories.indexes}
    assert "ix_agent_memories_expires_at" in {index.name for index in memories.indexes}
    assert "memory_key" in memories.columns
    assert "uq_agent_memories_owner_memory_key" in {constraint.name for constraint in memories.constraints}
    assert set(memory_snapshots.columns.keys()) == {
        "owner_user_id",
        "schema_version",
        "items_json",
        "source_date",
        "extractor_version",
        "updated_at",
    }
    assert "uq_agent_memory_consolidation_source" in {
        constraint.name for constraint in memory_consolidation_runs.constraints
    }
    assert "ix_agent_memory_consolidation_date_status" in {
        index.name for index in memory_consolidation_runs.indexes
    }
    assert "uq_user_facts_owner_key_kind" in {constraint.name for constraint in user_facts.constraints}
    assert {"fact_kind", "status", "value_json", "sensitivity", "expires_at", "deleted_at", "version"}.issubset(
        user_facts.columns.keys()
    )
    assert "uq_user_fact_extractions_source_version" in {
        constraint.name for constraint in fact_extraction_runs.constraints
    }
    assert {
        "status",
        "stage",
        "attempts",
        "max_attempts",
        "next_attempt_at",
        "locked_until",
        "lease_token",
        "candidates_json",
    }.issubset(fact_extraction_runs.columns.keys())
    assert "ix_user_fact_extractions_status_next_attempt" in {
        index.name for index in fact_extraction_runs.indexes
    }
