import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.audit.models import OutboxJob
from production_backend.app.modules.agent_runtime.models import AgentAction
from production_backend.app.modules.agent_runtime.service import AGENT_ACTION_APPLY_JOB, AgentRuntimeService
from production_backend.tests.test_agent_runtime_service import FakeAgentRuntimeRepository


def test_agent_runtime_actions_confirm_to_action_queued_without_queued_status() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))

    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type="support.ticket.create",
            target_type="support_ticket",
            preview_payload={"summary": "Pump does not turn on"},
            apply_payload={"issue_summary": "Pump does not turn on"},
        )
    )
    run.status = "waiting_for_confirmation"
    confirmed = asyncio.run(
        service.confirm_action(
            owner_user_id=owner_user_id,
            action_id=action.id,
            edited_apply_payload={"issue_summary": "Pump does not turn on after charging"},
            idempotency_key="idem-action",
        )
    )

    assert confirmed.status == "confirmed"
    assert confirmed.status != "queued"
    assert confirmed.idempotency_key == "idem-action"
    assert confirmed.apply_payload["issue_summary"] == "Pump does not turn on after charging"
    proposal_event = repository.events[-3]
    assert proposal_event.event_type == "action.confirmation_required"
    assert proposal_event.payload["action_id"] == str(action.id)
    assert proposal_event.payload["action_status"] == "confirmation_required"
    assert proposal_event.payload["action_type"] == "support.ticket.create"
    assert proposal_event.payload["target_type"] == "support_ticket"
    assert proposal_event.payload["side_effect_level"] == "medium"
    assert proposal_event.payload["preview_payload"] == {"summary": "Pump does not turn on"}
    assert "apply_payload" not in proposal_event.payload
    queued_event = repository.events[-2]
    assert queued_event.event_type == "action.queued"
    assert queued_event.payload["action_status"] == "confirmed"
    assert queued_event.payload["action_type"] == "support.ticket.create"
    assert queued_event.payload["target_type"] == "support_ticket"
    assert queued_event.payload["outbox_status"] == "queued"
    assert queued_event.payload["outbox_job_id"] == str(outbox_service.job.id)
    assert repository.run.status == "completed"
    assert repository.events[-1].event_type == "run.completed"
    assert repository.events[-1].payload == {"reason": "action_confirmed", "action_id": str(action.id)}
    assert outbox_service.enqueue_kwargs["job_type"] == AGENT_ACTION_APPLY_JOB
    assert outbox_service.enqueue_kwargs["payload"]["action_id"] == str(action.id)
    assert outbox_service.enqueue_kwargs["idempotency_key"] == f"agent-action:{action.id}:apply"
    assert outbox_service.enqueue_kwargs["action_id"] == action.id


def test_agent_runtime_actions_reject_confirmation_required_action() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="support.ticket.create")
    )
    run.status = "waiting_for_confirmation"

    rejected = asyncio.run(service.reject_action(owner_user_id=owner_user_id, action_id=action.id, reason="not now"))

    assert rejected.status == "rejected"
    assert rejected.error_code == "rejected_by_user"
    rejected_event = repository.events[-2]
    assert rejected_event.event_type == "action.rejected"
    assert rejected_event.payload["action_status"] == "rejected"
    assert rejected_event.payload["action_type"] == "support.ticket.create"
    assert rejected_event.payload["reason"] == "not now"
    assert repository.run.status == "completed"
    assert repository.events[-1].event_type == "run.completed"
    assert repository.events[-1].payload == {"reason": "action_rejected", "action_id": str(action.id)}


def test_agent_runtime_actions_do_not_reject_confirmed_action() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="support.ticket.create")
    )
    confirmed = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))
    event_count = len(repository.events)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.reject_action(owner_user_id=owner_user_id, action_id=action.id, reason="too late"))

    assert exc_info.value.code == "conflict"
    assert confirmed.status == "confirmed"
    assert repository.action.status == "confirmed"
    assert len(repository.events) == event_count


def test_agent_runtime_actions_do_not_overwrite_terminal_rejection_states() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="support.ticket.create")
    )
    action.status = "expired"
    action.error_code = "action_expired"
    event_count = len(repository.events)

    rejected = asyncio.run(service.reject_action(owner_user_id=owner_user_id, action_id=action.id, reason="too late"))

    assert rejected.status == "expired"
    assert rejected.error_code == "action_expired"
    assert len(repository.events) == event_count


def test_agent_runtime_actions_generate_action_idempotency_key_for_confirmation() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="support.ticket.create")
    )

    confirmed = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))

    generated_key = f"agent-action:{action.id}"
    outbox_key = f"agent-action:{action.id}:apply"
    assert confirmed.idempotency_key == generated_key
    assert outbox_service.enqueue_kwargs["idempotency_key"] == outbox_key


def test_agent_runtime_actions_scope_outbox_idempotency_key_to_each_action() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    first_run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create first ticket"))
    first_action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=first_run.id, action_type="support.ticket.create")
    )
    first_run.status = "waiting_for_confirmation"
    asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=first_action.id, idempotency_key="shared-key"))
    second_run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=repository.thread.id, message="Create second ticket"))
    second_action = asyncio.run(
        service.propose_action(owner_user_id=owner_user_id, run_id=second_run.id, action_type="support.ticket.create")
    )
    second_run.status = "waiting_for_confirmation"

    asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=second_action.id, idempotency_key="shared-key"))

    assert first_action.idempotency_key == "shared-key"
    assert second_action.idempotency_key == "shared-key"
    assert [call["idempotency_key"] for call in outbox_service.enqueue_calls] == [
        f"agent-action:{first_action.id}:apply",
        f"agent-action:{second_action.id}:apply",
    ]


def test_agent_runtime_actions_expire_past_confirmation_without_enqueueing() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    outbox_service = FakeOutboxService()
    service = AgentRuntimeService(repository=repository, outbox_service=outbox_service)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Create ticket"))
    action = asyncio.run(
        service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type="support.ticket.create",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    run.status = "waiting_for_confirmation"

    expired = asyncio.run(service.confirm_action(owner_user_id=owner_user_id, action_id=action.id))

    assert expired.status == "expired"
    assert expired.error_code == "action_expired"
    assert outbox_service.enqueue_kwargs == {}
    assert repository.events[-2].event_type == "action.expired"
    assert repository.events[-2].payload["action_status"] == "expired"
    assert repository.events[-1].event_type == "run.completed"
    assert repository.events[-1].payload == {"reason": "action_expired", "action_id": str(action.id)}


def test_agent_runtime_actions_reject_unsupported_action_type_before_persisting() -> None:
    owner_user_id = uuid4()
    repository = FakeActionRepository()
    service = AgentRuntimeService(repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Delete my device"))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.propose_action(owner_user_id=owner_user_id, run_id=run.id, action_type="device.delete"))

    assert exc_info.value.code == "unsupported_agent_action"
    assert repository.action is None


class FakeActionRepository(FakeAgentRuntimeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.action = None
        self.actions = []

    async def create_action(self, **kwargs):
        self.action = AgentAction(
            id=uuid4(),
            run_id=kwargs["run_id"],
            actor_user_id=kwargs["actor_user_id"],
            action_type=kwargs["action_type"],
            target_type=kwargs["target_type"],
            target_id=kwargs["target_id"],
            status=kwargs["status"],
            side_effect_level=kwargs["side_effect_level"],
            preview_payload=kwargs["preview_payload"],
            apply_payload=kwargs["apply_payload"],
            idempotency_key=kwargs["idempotency_key"],
            expires_at=kwargs["expires_at"],
            error_code="",
        )
        self.actions.append(self.action)
        return self.action

    async def get_action_for_owner(self, *, action_id: UUID, owner_user_id: UUID):
        return next((action for action in self.actions if action.id == action_id and action.actor_user_id == owner_user_id), None)

    async def mark_action_confirmed(self, **kwargs):
        action = kwargs["action"]
        action.status = "confirmed"
        action.confirmed_at = kwargs["confirmed_at"]
        if kwargs["apply_payload"] is not None:
            action.apply_payload = kwargs["apply_payload"]
        action.idempotency_key = kwargs["idempotency_key"]
        return action

    async def mark_action_rejected(self, **kwargs):
        action = kwargs["action"]
        action.status = "rejected"
        action.failed_at = kwargs["failed_at"]
        action.error_code = kwargs["error_code"]
        return action

    async def mark_action_expired(self, **kwargs):
        action = kwargs["action"]
        action.status = "expired"
        action.failed_at = kwargs["failed_at"]
        action.error_code = kwargs["error_code"]
        return action


class FakeOutboxService:
    def __init__(self) -> None:
        self.job = OutboxJob(
            id=uuid4(),
            job_type=AGENT_ACTION_APPLY_JOB,
            status="queued",
            payload={},
            idempotency_key="",
            request_id="",
            trace_id="",
        )
        self.enqueue_kwargs = {}
        self.enqueue_calls = []

    async def enqueue(self, **kwargs):
        self.job = OutboxJob(
            id=uuid4(),
            job_type=AGENT_ACTION_APPLY_JOB,
            status="queued",
            payload={},
            idempotency_key="",
            request_id="",
            trace_id="",
        )
        self.enqueue_kwargs = kwargs
        self.enqueue_calls.append(kwargs)
        self.job.action_id = kwargs["action_id"]
        self.job.payload = kwargs["payload"]
        self.job.idempotency_key = kwargs["idempotency_key"]
        return self.job
