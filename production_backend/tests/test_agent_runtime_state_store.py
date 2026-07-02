import asyncio
from datetime import date
from uuid import uuid4

from production_backend.app.modules.agent_runtime.models import AgentContextProjection, AgentRun, AgentWorkflowState
from production_backend.app.modules.agent_runtime.state_store import AgentRuntimeStateStore


def test_state_store_creates_workflow_state_as_json_safe_process_state() -> None:
    repository = FakeStateRepository()
    owner_user_id = uuid4()
    thread_id = uuid4()
    nested_id = uuid4()

    workflow_state = asyncio.run(
        AgentRuntimeStateStore(repository=repository).create_workflow_state(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            run_id=uuid4(),
            workflow_type="milk_analysis_intake",
            active_step="collect_daily_volume",
            state={"current_field": "daily_volume", "record_ids": (nested_id,), "as_of": date(2026, 7, 2)},
        )
    )

    assert workflow_state.owner_user_id == owner_user_id
    assert workflow_state.workflow_type == "milk_analysis_intake"
    assert workflow_state.status == "collecting"
    assert workflow_state.active_step == "collect_daily_volume"
    assert workflow_state.state == {
        "current_field": "daily_volume",
        "record_ids": [str(nested_id)],
        "as_of": "2026-07-02",
    }


def test_state_store_records_context_projection_as_derived_view() -> None:
    repository = FakeStateRepository()
    run = _run()
    message_id = uuid4()
    workflow_state_id = uuid4()

    projection = asyncio.run(
        AgentRuntimeStateStore(repository=repository).record_context_projection(
            run=run,
            selected_message_ids=[message_id],
            active_workflow_state_id=workflow_state_id,
            source_refs={"workflow_state_id": workflow_state_id},
            projection_summary={"state": {"run_id": run.id}},
            tool_schema_version="tools-v1",
            token_estimate=-1,
        )
    )

    assert projection.run_id == run.id
    assert projection.thread_id == run.thread_id
    assert projection.prompt_version == "prompt-v1"
    assert projection.tool_schema_version == "tools-v1"
    assert projection.selected_message_ids == [str(message_id)]
    assert projection.active_workflow_state_id == workflow_state_id
    assert projection.source_refs == {"workflow_state_id": str(workflow_state_id)}
    assert projection.projection_summary == {"state": {"run_id": str(run.id)}}
    assert projection.token_estimate == 0


def _run() -> AgentRun:
    return AgentRun(
        id=uuid4(),
        thread_id=uuid4(),
        actor_user_id=uuid4(),
        status="running",
        runtime_pattern="langgraph_sdk",
        graph_version="momcozy-agent-v1",
        prompt_version="prompt-v1",
        request_id="req",
        trace_id="trace",
        error_code="",
        error_details={},
    )


class FakeStateRepository:
    async def create_workflow_state(self, **kwargs):
        return AgentWorkflowState(id=uuid4(), **kwargs)

    async def create_context_projection(self, **kwargs):
        return AgentContextProjection(id=uuid4(), **kwargs)
