import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from production_backend.app.modules.agent_runtime.actions.executor import AgentActionExecutor
from production_backend.app.modules.agent_runtime.models import AgentAction, AgentEvent, AgentMessage, AgentRun, AgentThread
from production_backend.app.modules.agent_runtime.service import AgentRuntimeService
from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.hospital_bag import HOSPITAL_BAG_CART_UPDATE_ACTION, HospitalBagCartUpdateActionHandler
from production_backend.app.modules.support.agent_actions import SUPPORT_TICKET_CREATE_ACTION, SupportTicketCreateActionHandler
from production_backend.app.modules.support.models import SupportTicket
from production_backend.app.modules.support.service import SupportTicketsService
from production_backend.app.workers.agent_run import AgentRunWorker


def test_agent_support_ticket_confirmation_requeues_same_run_and_worker_applies() -> None:
    owner_user_id = uuid4()
    runtime_repository = InMemoryAgentRuntimeRepository()
    support_repository = InMemorySupportTicketsRepository()
    support_audit = FlowAuditService()
    support_service = SupportTicketsService(
        repository=support_repository,
        audit_service=support_audit,
        idempotency_service=FlowIdempotencyService(),
    )
    action_executor = AgentActionExecutor(
        repository=runtime_repository,
        handlers={SUPPORT_TICKET_CREATE_ACTION: SupportTicketCreateActionHandler(service=support_service)},
    )
    runtime_service = AgentRuntimeService(repository=runtime_repository, action_executor=action_executor)

    run = asyncio.run(
        runtime_service.create_run(
            actor_user_id=owner_user_id,
            thread_id=None,
            message="My Air1 pump does not turn on after charging.",
            request_id="req_agent_support",
            trace_id="trace_agent_support",
        )
    )
    action = asyncio.run(
        runtime_service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type=SUPPORT_TICKET_CREATE_ACTION,
            target_type="support_ticket",
            side_effect_level="medium",
            preview_payload={"issue_summary": "Air1 pump does not turn on"},
            apply_payload={
                "issue_type": "device_fault",
                "issue_summary": "Air1 pump does not turn on after charging",
                "product_model": "Air1",
                "urgency": "high",
            },
        )
    )
    run.status = "waiting_for_confirmation"

    confirmed = asyncio.run(
        runtime_service.confirm_action(
            owner_user_id=owner_user_id,
            action_id=action.id,
            idempotency_key="idem-support-action",
        )
    )
    asyncio.run(AgentRunWorker(repository=runtime_repository, action_executor=action_executor).run_once(run_id=run.id))

    ticket = support_repository.tickets[0]
    event_types = [event.event_type for event in runtime_repository.events]

    assert confirmed.status == "applied"
    assert ticket.owner_user_id == owner_user_id
    assert ticket.issue_summary == "Air1 pump does not turn on after charging"
    assert ticket.source == "agent_action"
    assert ticket.payload["agent_action_id"] == str(action.id)
    assert event_types == [
        "run.queued",
        "message.completed",
        "action.confirmation_required",
        "action.confirmed",
        "run.queued",
        "run.started",
        "action.applied",
        "message.completed",
        "run.completed",
    ]
    applied_event = next(event for event in runtime_repository.events if event.event_type == "action.applied")
    assert applied_event.payload["resource_type"] == "support_ticket"
    assert applied_event.payload["resource_id"] == str(ticket.id)
    assert support_audit.entries[-1]["action"] == "support.tickets.create"


def test_agent_hospital_bag_cart_main_flow_applies_inside_current_tool_transaction() -> None:
    owner_user_id = uuid4()
    runtime_repository = InMemoryAgentRuntimeRepository()
    action_executor = AgentActionExecutor(
        repository=runtime_repository,
        handlers={HOSPITAL_BAG_CART_UPDATE_ACTION: HospitalBagCartUpdateActionHandler()},
    )
    runtime_service = AgentRuntimeService(repository=runtime_repository, action_executor=action_executor)

    run = asyncio.run(
        runtime_service.create_run(
            actor_user_id=owner_user_id,
            thread_id=None,
            message="Mark the nursing bra as packed in my hospital bag.",
            request_id="req_agent_cart",
            trace_id="trace_agent_cart",
        )
    )
    action = asyncio.run(
        runtime_service.propose_action(
            owner_user_id=owner_user_id,
            run_id=run.id,
            action_type=HOSPITAL_BAG_CART_UPDATE_ACTION,
            target_type="hospital_bag_cart",
            side_effect_level="low",
            preview_payload={
                "summary": "Mark nursing bra as packed",
                "cart_update": {"set_checked": [{"item_id": "nursing-bra", "checked": True}]},
            },
            apply_payload={
                "cart_update": {"set_checked": [{"item_id": "nursing-bra", "checked": True}]},
            },
        )
    )
    applied_event = runtime_repository.events[-2]
    changed_event = runtime_repository.events[-1]

    assert action.status == "applied"
    assert [event.event_type for event in runtime_repository.events] == [
        "run.queued",
        "message.completed",
        "action.applied",
        "hospital_bag.cart.changed",
    ]
    assert applied_event.payload["user_visible"] is False
    assert applied_event.payload["resource_type"] == "hospital_bag_cart"
    assert applied_event.payload["resource_id"] == str(action.id)
    assert applied_event.payload["details"]["cart_update"] == {"set_checked": [{"item_id": "nursing-bra", "checked": True}]}
    assert applied_event.payload["details"]["agent_action_id"] == str(action.id)
    assert applied_event.payload["details"]["agent_run_id"] == str(run.id)
    assert changed_event.payload["action_id"] == str(action.id)
    assert changed_event.payload["source"] == "agent_action"
    assert changed_event.payload["cart_update"] == {"set_checked": [{"item_id": "nursing-bra", "checked": True}]}


class InMemoryAgentRuntimeRepository:
    def __init__(self) -> None:
        self.thread: AgentThread | None = None
        self.run: AgentRun | None = None
        self.messages: list[AgentMessage] = []
        self.actions: list[AgentAction] = []
        self.events: list[AgentEvent] = []

    async def create_thread(self, *, owner_user_id: UUID, title: str, metadata: dict):
        self.thread = AgentThread(id=uuid4(), owner_user_id=owner_user_id, title=title, status="active", metadata_json=metadata)
        return self.thread

    async def get_thread_for_owner(self, *, thread_id: UUID, owner_user_id: UUID):
        if self.thread and self.thread.id == thread_id and self.thread.owner_user_id == owner_user_id:
            return self.thread
        return None

    async def list_threads_for_owner(self, *, owner_user_id: UUID, limit: int):
        return [self.thread] if self.thread and self.thread.owner_user_id == owner_user_id else []

    async def touch_thread(self, *, thread: AgentThread, updated_at):
        thread.updated_at = updated_at
        return thread

    async def get_active_run_for_thread(self, *, thread_id: UUID, owner_user_id: UUID):
        if (
            self.run
            and self.run.thread_id == thread_id
            and self.run.actor_user_id == owner_user_id
            and self.run.status in {"queued", "running"}
        ):
            return self.run
        return None

    async def create_run(self, **kwargs):
        self.run = AgentRun(
            id=uuid4(),
            thread_id=kwargs["thread_id"],
            actor_user_id=kwargs["actor_user_id"],
            status="queued",
            runtime_pattern=kwargs["runtime_pattern"],
            graph_version=kwargs["graph_version"],
            prompt_version=kwargs["prompt_version"],
            request_id=kwargs["request_id"],
            trace_id=kwargs["trace_id"],
            error_code="",
            error_details={},
        )
        return self.run

    async def get_run(self, *, run_id: UUID):
        return self.run if self.run and self.run.id == run_id else None

    async def get_run_for_owner(self, *, run_id: UUID, owner_user_id: UUID):
        if self.run and self.run.id == run_id and self.run.actor_user_id == owner_user_id:
            return self.run
        return None

    def begin_nested(self):
        return _NoopSavepoint()

    async def refresh_run(self, *, run: AgentRun):
        return run

    async def mark_run_running(self, *, run: AgentRun, started_at):
        run.status = "running"
        run.started_at = started_at
        return run

    async def mark_run_queued(self, *, run: AgentRun):
        run.status = "queued"
        run.started_at = None
        return run

    async def mark_run_completed(self, *, run: AgentRun, completed_at):
        run.status = "completed"
        run.completed_at = completed_at
        return run

    async def create_message(self, **kwargs):
        message_id = kwargs.pop("message_id", None)
        message = AgentMessage(id=message_id or uuid4(), sequence=len(self.messages) + 1, **kwargs)
        self.messages.append(message)
        return message

    async def get_latest_assistant_message_for_run(self, *, run_id: UUID):
        return next(
            (message for message in reversed(self.messages) if message.run_id == run_id and message.role == "assistant"),
            None,
        )

    async def create_action(self, **kwargs):
        action = AgentAction(
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
        self.actions.append(action)
        return action

    async def get_action(self, *, action_id: UUID):
        return next((action for action in self.actions if action.id == action_id), None)

    async def get_action_for_owner(self, *, action_id: UUID, owner_user_id: UUID):
        return next((action for action in self.actions if action.id == action_id and action.actor_user_id == owner_user_id), None)

    async def list_actions_for_run(self, *, run_id: UUID):
        return [action for action in self.actions if action.run_id == run_id]

    async def mark_action_confirmed(self, *, action: AgentAction, confirmed_at, apply_payload, idempotency_key: str):
        action.status = "confirmed"
        action.confirmed_at = confirmed_at
        action.idempotency_key = idempotency_key
        if apply_payload is not None:
            action.apply_payload = apply_payload
        return action

    async def mark_action_applying(self, *, action: AgentAction):
        action.status = "applying"
        return action

    async def mark_action_applied(self, *, action: AgentAction, applied_at):
        action.status = "applied"
        action.applied_at = applied_at
        return action

    async def mark_action_failed(self, *, action: AgentAction, failed_at, error_code: str):
        action.status = "failed"
        action.failed_at = failed_at
        action.error_code = error_code
        return action

    async def append_event(self, *, thread_id: UUID, run_id: UUID, event_type: str, payload: dict):
        event = AgentEvent(
            event_id=uuid4(),
            thread_id=thread_id,
            run_id=run_id,
            sequence=len(self.events) + 1,
            event_type=event_type,
            payload=payload,
        )
        self.events.append(event)
        return event


class _NoopSavepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class InMemorySupportTicketsRepository:
    def __init__(self) -> None:
        self.tickets: list[SupportTicket] = []

    async def create_ticket(self, **kwargs):
        ticket = SupportTicket(id=uuid4(), **kwargs)
        self.tickets.append(ticket)
        return ticket

    async def get_for_owner(self, *, ticket_id: UUID, owner_user_id: UUID):
        return next((ticket for ticket in self.tickets if ticket.id == ticket_id and ticket.owner_user_id == owner_user_id), None)

    async def list_for_owner(self, *, owner_user_id: UUID, status: str | None, limit: int):
        tickets = [
            ticket for ticket in self.tickets if ticket.owner_user_id == owner_user_id and (status is None or ticket.status == status)
        ]
        return tickets[:limit]


class FlowIdempotencyService:
    async def reserve(self, **kwargs):
        record = IdempotencyKey(
            actor_user_id=kwargs["actor_user_id"],
            scope=kwargs["scope"],
            key=kwargs["key"],
            request_hash=kwargs["request_hash"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        return FlowIdempotencyDecision(record=record)

    async def mark_completed(self, *, record, response_ref: str):
        record.response_ref = response_ref
        return record


class FlowIdempotencyDecision:
    def __init__(self, *, record) -> None:
        self.status = "reserved"
        self.record = record


class FlowAuditService:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)
