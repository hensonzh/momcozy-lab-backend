import asyncio
from uuid import uuid4

import pytest

from app.agents.cozymate.actions import cozymate_action_policy
from app.core.errors import ApiError
from app.agent_runtime.actions.executor import (
    AgentActionApplyResult,
    AgentActionExecutor,
    AgentApplicationEvent,
)
from app.agent_runtime.runs.models import AgentAction, AgentEvent, AgentRun


ACTION_POLICY = cozymate_action_policy()


def test_action_executor_applies_in_process_and_marks_direct_event_non_visible() -> None:
    repository = FakeActionRepository()
    calls: list[object] = []

    async def apply(action: AgentAction) -> AgentActionApplyResult:
        calls.append(action.id)
        return AgentActionApplyResult(
            resource_type="plan",
            resource_id="plan-1",
            application_events=(
                AgentApplicationEvent(event_type="pregnancy_plan.changed", payload={"operation": "created"}),
            ),
        )

    executor = AgentActionExecutor(action_policy=ACTION_POLICY, repository=repository, handlers={repository.action.action_type: apply})

    outcome = asyncio.run(executor.apply(repository.action))

    assert calls == [repository.action.id]
    assert outcome.action.status == "applied"
    assert outcome.replayed is False
    assert [event.event_type for event in repository.events] == ["action.applied", "pregnancy_plan.changed"]
    assert repository.events[0].payload == {
        "action_id": str(repository.action.id),
        "action_status": "applied",
        "action_type": "pregnancy.plan.create",
        "target_type": "plan",
        "target_id": "",
        "requires_confirmation": False,
        "confirmation_policy": "explicit_intent",
        "user_visible": False,
        "resource_type": "plan",
        "resource_id": "plan-1",
        "details": {},
    }
    assert repository.events[1].payload["action_id"] == str(repository.action.id)
    assert repository.events[1].payload["requires_confirmation"] is False
    assert repository.events[1].payload["user_visible"] is False


def test_action_executor_replays_applied_action_without_calling_handler_or_emitting_events() -> None:
    repository = FakeActionRepository(action_status="applied")

    async def should_not_run(_action: AgentAction) -> AgentActionApplyResult:
        raise AssertionError("applied actions must not execute twice")

    executor = AgentActionExecutor(action_policy=ACTION_POLICY, repository=repository, handlers={repository.action.action_type: should_not_run})

    outcome = asyncio.run(executor.apply(repository.action))

    assert outcome.action is repository.action
    assert outcome.replayed is True
    assert repository.events == []


def test_action_executor_rejects_corrupt_owner_even_for_applied_replay() -> None:
    repository = FakeActionRepository(action_status="applied")
    repository.owner_scope_valid = False

    executor = AgentActionExecutor(action_policy=ACTION_POLICY, repository=repository, handlers={})

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(executor.apply(repository.action))

    assert exc_info.value.code == "agent_action_scope_violation"
    assert repository.events == []


def test_action_executor_rolls_back_domain_and_success_events_before_recording_failure() -> None:
    repository = FaultInjectingActionRepository()

    async def apply(_action: AgentAction) -> AgentActionApplyResult:
        repository.domain_rows.append("plan-1")
        return AgentActionApplyResult(
            application_events=(
                AgentApplicationEvent(event_type="pregnancy_plan.changed", payload={"operation": "created"}),
            )
        )

    executor = AgentActionExecutor(action_policy=ACTION_POLICY, repository=repository, handlers={repository.action.action_type: apply})

    outcome = asyncio.run(executor.apply(repository.action))

    assert outcome.action.status == "failed"
    assert outcome.action.error_code == "agent_action_handler_error"
    assert repository.domain_rows == []
    assert [event.event_type for event in repository.events] == ["action.failed"]
    assert repository.events[0].payload["user_visible"] is False


def test_action_executor_terminally_fails_corrupt_scope_without_handler_or_cross_owner_event() -> None:
    repository = FakeActionRepository()
    repository.owner_scope_valid = False
    calls: list[object] = []

    async def apply(action: AgentAction) -> AgentActionApplyResult:
        calls.append(action.id)
        return AgentActionApplyResult()

    executor = AgentActionExecutor(action_policy=ACTION_POLICY, repository=repository, handlers={repository.action.action_type: apply})

    outcome = asyncio.run(executor.apply(repository.action))

    assert outcome.action.status == "failed"
    assert outcome.action.error_code == "agent_action_scope_violation"
    assert calls == []
    assert repository.domain_rows == []
    assert repository.events == []


class FakeActionRepository:
    def __init__(
        self,
        *,
        action_status: str = "confirmed",
        action_type: str = "pregnancy.plan.create",
    ) -> None:
        self.run = AgentRun(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            status="running",
            runtime_pattern="sdk_only",
            runtime_version="momcozy-agent-v1",
            prompt_version="",
            request_id="req",
            trace_id="trace",
            error_code="",
            error_details={},
        )
        self.action = AgentAction(
            id=uuid4(),
            run_id=self.run.id,
            actor_user_id=self.run.actor_user_id,
            action_type=action_type,
            target_type="plan",
            target_id="",
            status=action_status,
            side_effect_level="medium",
            preview_payload={},
            apply_payload={},
            idempotency_key="idem",
            error_code="",
        )
        self.events: list[AgentEvent] = []
        self.domain_rows: list[object] = []
        self.owner_scope_valid = True

    async def get_run(self, *, run_id):
        return self.run if run_id == self.run.id else None

    async def get_run_for_owner(self, *, run_id, owner_user_id):
        if not self.owner_scope_valid:
            return None
        if run_id == self.run.id and owner_user_id == self.run.actor_user_id:
            return self.run
        return None

    async def get_action(self, *, action_id):
        return self.action if action_id == self.action.id else None

    async def mark_action_applying(self, *, action):
        action.status = "applying"
        return action

    async def mark_action_applied(self, *, action, applied_at):
        action.status = "applied"
        action.applied_at = applied_at
        return action

    async def mark_action_failed(self, *, action, failed_at, error_code):
        action.status = "failed"
        action.failed_at = failed_at
        action.error_code = error_code
        return action

    async def append_event(self, **kwargs):
        event = AgentEvent(
            event_id=uuid4(),
            thread_id=kwargs["thread_id"],
            run_id=kwargs["run_id"],
            sequence=len(self.events) + 1,
            event_type=kwargs["event_type"],
            payload=kwargs["payload"],
        )
        self.events.append(event)
        return event

    def begin_nested(self):
        return FakeSavepoint(self)


class FaultInjectingActionRepository(FakeActionRepository):
    def __init__(
        self,
        *,
        action_type: str = "pregnancy.plan.create",
        failed_event_type: str = "pregnancy_plan.changed",
    ) -> None:
        super().__init__(action_type=action_type)
        self.domain_rows = []
        self.failed_event_type = failed_event_type

    async def append_event(self, **kwargs):
        if kwargs["event_type"] == self.failed_event_type:
            raise RuntimeError("event write failed")
        return await super().append_event(**kwargs)


class FakeSavepoint:
    def __init__(self, repository: FakeActionRepository) -> None:
        self.repository = repository
        self.action_status = repository.action.status
        self.action_applied_at = repository.action.applied_at
        self.events = list(repository.events)
        self.domain_rows = list(getattr(repository, "domain_rows", []))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, _exc, _traceback):
        if exc_type is not None:
            self.repository.action.status = self.action_status
            self.repository.action.applied_at = self.action_applied_at
            self.repository.events[:] = self.events
            if hasattr(self.repository, "domain_rows"):
                self.repository.domain_rows[:] = self.domain_rows
        return False
