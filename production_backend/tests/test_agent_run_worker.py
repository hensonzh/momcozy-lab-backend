import asyncio
from uuid import uuid4

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import AgentEvent, AgentMessage, AgentRun
from production_backend.app.workers.agent_run import AgentRunWorker, AgentRunWorkerResult


def test_agent_run_worker_completes_run_with_assistant_message_events_and_lock() -> None:
    repository = FakeAgentRuntimeRepository()
    controls = FakeAgentRunControls()

    async def handler(run: AgentRun) -> AgentRunWorkerResult:
        assert run.status == "running"
        return AgentRunWorkerResult(status="completed", final_text="Here is the summary.")

    worker = AgentRunWorker(repository=repository, controls=controls, handler=handler)

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is not None
    assert run.status == "completed"
    assert [event.event_type for event in repository.events] == ["run.started", "message.completed", "run.completed"]
    assert repository.messages[0].content == {"text": "Here is the summary."}
    assert controls.lock_released is True
    assert controls.cleared_active_run == (repository.run.thread_id, repository.run.id)
    assert controls.stream_cursor == (repository.run.id, 3)


def test_agent_run_worker_cancels_before_handler_when_cancel_requested() -> None:
    repository = FakeAgentRuntimeRepository()
    controls = FakeAgentRunControls(cancel_requested=True)
    handler_called = False

    async def handler(_run: AgentRun) -> AgentRunWorkerResult:
        nonlocal handler_called
        handler_called = True
        return AgentRunWorkerResult(status="completed")

    worker = AgentRunWorker(repository=repository, controls=controls, handler=handler)

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is not None
    assert run.status == "cancelled"
    assert run.error_code == "cancelled_before_start"
    assert handler_called is False
    assert [event.event_type for event in repository.events] == ["run.cancelled"]


def test_agent_run_worker_marks_failed_for_handler_error() -> None:
    repository = FakeAgentRuntimeRepository()

    async def handler(_run: AgentRun) -> AgentRunWorkerResult:
        raise ApiError(code="dependency_not_configured", message="missing sdk", status=503)

    worker = AgentRunWorker(repository=repository, handler=handler)

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "dependency_not_configured"
    assert [event.event_type for event in repository.events] == ["run.started", "run.failed"]


def test_agent_run_worker_preserves_waiting_for_confirmation_state() -> None:
    repository = FakeAgentRuntimeRepository()
    action_id = uuid4()

    async def handler(_run: AgentRun) -> AgentRunWorkerResult:
        return AgentRunWorkerResult(status="waiting_for_confirmation", pending_action_id=action_id)

    worker = AgentRunWorker(repository=repository, handler=handler)

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is not None
    assert run.status == "waiting_for_confirmation"
    assert repository.events[-1].event_type == "run.waiting_for_confirmation"
    assert repository.events[-1].payload["action_id"] == str(action_id)


class FakeAgentRuntimeRepository:
    def __init__(self) -> None:
        self.run = AgentRun(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            status="queued",
            runtime_pattern="langgraph_sdk",
            graph_version="momcozy-agent-v1",
            prompt_version="",
            request_id="req",
            trace_id="trace",
            error_code="",
            error_details={},
        )
        self.messages = []
        self.events = []

    async def get_run(self, *, run_id):
        return self.run if run_id == self.run.id else None

    async def mark_run_running(self, *, run, started_at):
        run.status = "running"
        run.started_at = started_at
        return run

    async def mark_run_completed(self, *, run, completed_at):
        run.status = "completed"
        run.completed_at = completed_at
        return run

    async def mark_run_waiting_for_confirmation(self, *, run):
        run.status = "waiting_for_confirmation"
        return run

    async def mark_run_cancelled(self, *, run, cancelled_at, error_code):
        run.status = "cancelled"
        run.cancelled_at = cancelled_at
        run.completed_at = cancelled_at
        run.error_code = error_code
        return run

    async def mark_run_failed(self, *, run, completed_at, error_code, error_details):
        run.status = "failed"
        run.completed_at = completed_at
        run.error_code = error_code
        run.error_details = error_details
        return run

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


class FakeAgentRunControls:
    def __init__(self, *, cancel_requested: bool = False) -> None:
        self.cancel_requested = cancel_requested
        self.lock_released = False
        self.cleared_active_run = None
        self.stream_cursor = None

    def run_lock(self, *, run_id, ttl_seconds=60):
        return FakeRunLock(self, acquired=True)

    async def is_cancel_requested(self, *, run_id):
        return self.cancel_requested

    async def set_stream_cursor(self, *, run_id, sequence):
        self.stream_cursor = (run_id, sequence)

    async def clear_active_run(self, *, thread_id, run_id=None):
        self.cleared_active_run = (thread_id, run_id)

    async def clear_cancel(self, *, run_id):
        self.cancel_requested = False


class FakeRunLock:
    def __init__(self, controls: FakeAgentRunControls, *, acquired: bool) -> None:
        self.controls = controls
        self.acquired = acquired

    async def __aenter__(self):
        return self.acquired

    async def __aexit__(self, exc_type, exc, traceback):
        self.controls.lock_released = True
        return False
