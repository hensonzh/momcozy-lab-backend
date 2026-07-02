import asyncio
from uuid import uuid4

import pytest

from production_backend.app.modules.agent_runtime.action_outbox import AgentActionApplyResult, AgentActionOutboxHandler
from production_backend.app.modules.agent_runtime.models import AgentAction, AgentEvent, AgentRun
from production_backend.app.modules.agent_runtime.service import AGENT_ACTION_APPLY_JOB
from production_backend.app.modules.audit.models import OutboxJob
from production_backend.app.workers.errors import PermanentJobError


def test_agent_action_outbox_handler_applies_action_and_emits_event() -> None:
    repository = FakeAgentActionRepository()
    calls = []

    async def apply(action: AgentAction) -> AgentActionApplyResult:
        calls.append(action.id)
        return AgentActionApplyResult(resource_type="support_ticket", resource_id="ticket_1", details={"status": "submitted"})

    handler = AgentActionOutboxHandler(repository=repository, handlers={"support.ticket.create": apply})

    asyncio.run(handler(_job(repository.action.id)))

    assert calls == [repository.action.id]
    assert repository.action.status == "applied"
    assert [event.event_type for event in repository.events] == ["action.applied"]
    assert repository.events[0].thread_id == repository.run.thread_id
    assert repository.events[0].payload["action_id"] == str(repository.action.id)
    assert repository.events[0].payload["action_status"] == "applied"
    assert repository.events[0].payload["action_type"] == "support.ticket.create"
    assert repository.events[0].payload["target_type"] == "support_ticket"
    assert repository.events[0].payload["resource_id"] == "ticket_1"


def test_agent_action_outbox_handler_marks_failed_when_handler_missing() -> None:
    repository = FakeAgentActionRepository()
    handler = AgentActionOutboxHandler(repository=repository, handlers={})

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(handler(_job(repository.action.id)))

    assert exc_info.value.code == "agent_action_handler_not_found"
    assert repository.action.status == "failed"
    assert repository.action.error_code == "agent_action_handler_not_found"
    assert repository.events[-1].event_type == "action.failed"
    assert repository.events[-1].payload["action_id"] == str(repository.action.id)
    assert repository.events[-1].payload["action_status"] == "failed"
    assert repository.events[-1].payload["action_type"] == "support.ticket.create"
    assert repository.events[-1].payload["target_type"] == "support_ticket"
    assert repository.events[-1].payload["code"] == "agent_action_handler_not_found"


def test_agent_action_outbox_handler_is_idempotent_for_applied_action() -> None:
    repository = FakeAgentActionRepository(action_status="applied")
    handler = AgentActionOutboxHandler(repository=repository, handlers={})

    asyncio.run(handler(_job(repository.action.id)))

    assert repository.events == []


class FakeAgentActionRepository:
    def __init__(self, *, action_status: str = "confirmed") -> None:
        self.run = AgentRun(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            status="completed",
            runtime_pattern="langgraph_sdk",
            graph_version="momcozy-agent-v1",
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
            action_type="support.ticket.create",
            target_type="support_ticket",
            target_id="",
            status=action_status,
            side_effect_level="medium",
            preview_payload={},
            apply_payload={},
            idempotency_key="idem-action",
            error_code="",
        )
        self.events = []

    async def get_action(self, *, action_id):
        return self.action if action_id == self.action.id else None

    async def get_run(self, *, run_id):
        return self.run if run_id == self.run.id else None

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


def _job(action_id) -> OutboxJob:
    return OutboxJob(
        id=uuid4(),
        action_id=action_id,
        job_type=AGENT_ACTION_APPLY_JOB,
        status="locked",
        payload={"action_id": str(action_id)},
        idempotency_key=f"agent-action:{action_id}",
        request_id="req",
        trace_id="trace",
    )
