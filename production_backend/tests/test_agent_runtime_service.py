import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from production_backend.app.modules.agent_runtime.models import AgentEvent, AgentMessage, AgentRun, AgentThread
from production_backend.app.modules.agent_runtime.service import AgentRuntimeService
from production_backend.app.modules.audit.models import IdempotencyKey


def test_agent_runtime_service_creates_run_with_thread_message_events_and_idempotency() -> None:
    owner_user_id = uuid4()
    repository = FakeAgentRuntimeRepository()
    idempotency_service = FakeIdempotencyService(status="reserved")
    service = AgentRuntimeService(repository=repository, idempotency_service=idempotency_service)

    run = asyncio.run(
        service.create_run(
            actor_user_id=owner_user_id,
            thread_id=None,
            message="Review my pumping pattern",
            request_id="req_run",
            idempotency_key="idem-run",
        )
    )

    assert run.actor_user_id == owner_user_id
    assert run.runtime_pattern == "langgraph_sdk"
    assert repository.messages[0].content["text"] == "Review my pumping pattern"
    assert [event.event_type for event in repository.events] == ["run.queued", "message.completed"]
    assert idempotency_service.reserve_kwargs["scope"] == "agent.runs.create"
    assert idempotency_service.completed_response_ref == str(run.id)


def test_agent_runtime_service_cancels_run_idempotently_and_replays_events() -> None:
    owner_user_id = uuid4()
    repository = FakeAgentRuntimeRepository()
    service = AgentRuntimeService(repository=repository)
    run = asyncio.run(service.create_run(actor_user_id=owner_user_id, thread_id=None, message="Hello"))

    cancelled = asyncio.run(service.cancel_run(owner_user_id=owner_user_id, run_id=run.id, reason="user tapped stop"))
    second_cancel = asyncio.run(service.cancel_run(owner_user_id=owner_user_id, run_id=run.id, reason="repeat"))
    replayed = asyncio.run(service.list_events(owner_user_id=owner_user_id, run_id=run.id, after_sequence=1, limit=10))

    assert cancelled.status == "cancelled"
    assert second_cancel.status == "cancelled"
    assert [event.event_type for event in replayed] == ["message.completed", "run.cancelled"]


def _thread(*, owner_user_id: UUID) -> AgentThread:
    return AgentThread(id=uuid4(), owner_user_id=owner_user_id, title="Thread", status="active", metadata_json={})


def _run(*, thread_id: UUID, actor_user_id: UUID) -> AgentRun:
    return AgentRun(
        id=uuid4(),
        thread_id=thread_id,
        actor_user_id=actor_user_id,
        status="queued",
        runtime_pattern="langgraph_sdk",
        graph_version="momcozy-agent-v1",
        prompt_version="",
        request_id="",
        trace_id="",
        error_code="",
        error_details={},
    )


class FakeAgentRuntimeRepository:
    def __init__(self) -> None:
        self.thread = None
        self.run = None
        self.messages = []
        self.events = []

    async def create_thread(self, **kwargs):
        self.thread = _thread(owner_user_id=kwargs["owner_user_id"])
        self.thread.title = kwargs["title"]
        self.thread.metadata_json = kwargs["metadata"]
        return self.thread

    async def get_thread_for_owner(self, **kwargs):
        return self.thread

    async def list_threads_for_owner(self, **kwargs):
        return [self.thread] if self.thread else []

    async def create_run(self, **kwargs):
        self.run = _run(thread_id=kwargs["thread_id"], actor_user_id=kwargs["actor_user_id"])
        self.run.runtime_pattern = kwargs["runtime_pattern"]
        self.run.graph_version = kwargs["graph_version"]
        self.run.prompt_version = kwargs["prompt_version"]
        self.run.request_id = kwargs["request_id"]
        return self.run

    async def get_run_for_owner(self, **kwargs):
        return self.run

    async def create_message(self, **kwargs):
        message = AgentMessage(
            id=uuid4(),
            thread_id=kwargs["thread_id"],
            run_id=kwargs["run_id"],
            role=kwargs["role"],
            message_type=kwargs["message_type"],
            content=kwargs["content"],
            status=kwargs["status"],
            sequence=len(self.messages) + 1,
        )
        self.messages.append(message)
        return message

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

    async def list_events_for_owner(self, **kwargs):
        return [event for event in self.events if event.sequence > kwargs["after_sequence"]]

    async def mark_run_cancelled(self, **kwargs):
        self.run.status = "cancelled"
        self.run.cancelled_at = kwargs["cancelled_at"]
        self.run.completed_at = kwargs["cancelled_at"]
        self.run.error_code = kwargs["error_code"]
        return self.run


class FakeIdempotencyService:
    def __init__(self, *, status: str, response_ref: str = "") -> None:
        self.status = status
        self.reserve_kwargs = {}
        self.completed_response_ref = ""
        self.record = IdempotencyKey(
            actor_user_id=uuid4(),
            scope="agent.runs.create",
            key="idem-run",
            request_hash="hash",
            response_ref=response_ref,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    async def reserve(self, **kwargs):
        self.reserve_kwargs = kwargs
        return FakeIdempotencyDecision(status=self.status, record=self.record)

    async def mark_completed(self, *, record, response_ref: str):
        self.completed_response_ref = response_ref
        record.response_ref = response_ref
        return record


class FakeIdempotencyDecision:
    def __init__(self, *, status: str, record) -> None:
        self.status = status
        self.record = record
