import asyncio
from datetime import date
from uuid import uuid4

from app.agent_runtime.runs.models import AgentWorkflowState
from app.agent_runtime.runs.state_store import AgentRuntimeStateStore


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
    assert workflow_state.revision == 1
    assert workflow_state.step_token
    assert workflow_state.state == {
        "current_field": "daily_volume",
        "record_ids": [str(nested_id)],
        "as_of": "2026-07-02",
    }


def test_state_store_upserts_one_active_workflow_per_thread_and_type() -> None:
    repository = FakeStateRepository()
    store = AgentRuntimeStateStore(repository=repository)
    owner_user_id = uuid4()
    thread_id = uuid4()

    created = asyncio.run(
        store.upsert_active_workflow(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            run_id=uuid4(),
            workflow_type="device_unboxing",
            status="collecting",
            state={"phase": "guiding", "device_model": "Air1"},
            active_step="guide.parts",
        )
    )
    initial_step_token = created.step_token
    updated_run_id = uuid4()
    updated = asyncio.run(
        store.upsert_active_workflow(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            run_id=updated_run_id,
            workflow_type="device_unboxing",
            status="waiting",
            state={"phase": "guiding", "device_model": "Air1", "completed_steps": ["guide.parts"]},
            active_step="guide.controls",
        )
    )

    assert updated is created
    assert updated.run_id == updated_run_id
    assert updated.status == "waiting"
    assert updated.active_step == "guide.controls"
    assert updated.state["completed_steps"] == ["guide.parts"]
    assert updated.revision == 2
    assert updated.step_token != initial_step_token
    assert len(repository.workflow_states) == 1


def test_state_store_lists_only_repository_selected_active_workflows() -> None:
    repository = FakeStateRepository()
    store = AgentRuntimeStateStore(repository=repository)
    workflow = AgentWorkflowState(
        id=uuid4(),
        thread_id=uuid4(),
        owner_user_id=uuid4(),
        run_id=uuid4(),
        workflow_type="hospital_bag",
        status="collecting",
        schema_version="v1",
        state={"phase": "collecting_intake"},
        active_step="collecting_intake",
    )
    repository.workflow_states.append(workflow)

    result = asyncio.run(
        store.list_active_workflows(
            thread_id=workflow.thread_id,
            owner_user_id=workflow.owner_user_id,
            limit=3,
        )
    )

    assert result == [workflow]
    assert repository.list_active_kwargs["limit"] == 3


def test_state_store_owner_scoped_workflow_resumes_across_threads_and_appends_events() -> None:
    repository = FakeStateRepository()
    store = AgentRuntimeStateStore(repository=repository)
    owner_user_id = uuid4()
    first_thread_id = uuid4()
    second_thread_id = uuid4()

    created = asyncio.run(
        store.upsert_active_workflow(
            thread_id=first_thread_id,
            owner_user_id=owner_user_id,
            run_id=uuid4(),
            workflow_type="pregnancy_plan",
            status="waiting",
            state={"phase": "checkup_done_question"},
            active_step="checkup_done_question",
            lookup_scope="owner",
            transition_metadata={"event_type": "workflow.started", "command": "start_or_resume"},
        )
    )
    updated = asyncio.run(
        store.upsert_active_workflow(
            thread_id=second_thread_id,
            owner_user_id=owner_user_id,
            run_id=uuid4(),
            workflow_type="pregnancy_plan",
            status="paused",
            state={"phase": "checkup_done_question", "paused": True},
            active_step="checkup_done_question",
            lookup_scope="owner",
            transition_metadata={"event_type": "workflow.paused", "command": "pause"},
        )
    )

    assert updated is created
    assert updated.thread_id == first_thread_id
    assert len(repository.workflow_states) == 1
    assert [event["event_type"] for event in repository.workflow_events] == [
        "workflow.started",
        "workflow.paused",
    ]
    assert repository.workflow_events[-1]["payload"]["before"]["status"] == "waiting"
    assert repository.workflow_events[-1]["payload"]["after"]["status"] == "paused"
    assert repository.workflow_events[-1]["payload"]["command"] == "pause"


class FakeStateRepository:
    def __init__(self) -> None:
        self.workflow_states = []
        self.list_active_kwargs = {}
        self.workflow_events = []

    async def create_workflow_state(self, **kwargs):
        workflow = AgentWorkflowState(id=uuid4(), **kwargs)
        self.workflow_states.append(workflow)
        return workflow

    async def get_latest_workflow_state_for_thread(self, **kwargs):
        matches = [
            workflow
            for workflow in self.workflow_states
            if workflow.thread_id == kwargs["thread_id"]
            and workflow.owner_user_id == kwargs["owner_user_id"]
            and workflow.workflow_type == kwargs["workflow_type"]
        ]
        return matches[-1] if matches else None

    async def get_latest_workflow_state_for_owner(self, **kwargs):
        matches = [
            workflow
            for workflow in self.workflow_states
            if workflow.owner_user_id == kwargs["owner_user_id"]
            and workflow.workflow_type == kwargs["workflow_type"]
        ]
        return matches[-1] if matches else None

    async def update_workflow_state(self, *, workflow_state, **kwargs):
        for key, value in kwargs.items():
            if value is not None:
                setattr(workflow_state, key, value)
        return workflow_state

    async def list_active_workflow_states_for_thread(self, **kwargs):
        self.list_active_kwargs = kwargs
        return [
            workflow
            for workflow in self.workflow_states
            if workflow.thread_id == kwargs["thread_id"] and workflow.owner_user_id == kwargs["owner_user_id"]
        ][: kwargs["limit"]]

    async def append_workflow_event(self, **kwargs):
        self.workflow_events.append(kwargs)
        return kwargs
