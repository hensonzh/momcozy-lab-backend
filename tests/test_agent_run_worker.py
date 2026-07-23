import asyncio
import hashlib
from uuid import uuid4

from app.core.errors import ApiError
from app.agent_runtime.actions.executor import AgentActionExecutionOutcome
from app.agent_runtime.runs.models import AgentAction, AgentEvent, AgentMessage, AgentRun
from app.workers.agent_run import INTERRUPTED_RUN_ERROR_CODE, AgentRunQueueWorker, AgentRunWorkerResult, AgentRunWorker


def test_agent_run_worker_completes_run_with_assistant_message_events_and_lock() -> None:
    repository = FakeAgentRuntimeRepository()
    controls = FakeAgentRunControls()
    transient_stream = FakeTransientStream()
    commits = []

    async def handler(run: AgentRun) -> AgentRunWorkerResult:
        assert run.status == "running"
        return AgentRunWorkerResult(status="completed", final_text="Here is the summary.", stream_segment_count=2)

    async def after_event_append() -> None:
        commits.append("commit")

    worker = AgentRunWorker(
        repository=repository,
        controls=controls,
        handler=handler,
        after_event_append=after_event_append,
        transient_stream=transient_stream,
    )

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is not None
    assert run.status == "completed"
    assert [event.event_type for event in repository.events] == ["run.started", "message.completed", "run.completed"]
    assert repository.events[0].payload["semantic"]["phase"] == "thinking"
    assert repository.events[0].payload["semantic"]["label"] == "我已经收到你的消息啦～"
    assert repository.events[2].payload["semantic"]["surface"] == "hidden"
    assert repository.events[2].payload["semantic"]["lifecycle"] == "completed"
    assert repository.messages[0].content == {"text": "Here is the summary."}
    assert [item.item for item in repository.context_items] == [
        {"role": "assistant", "content": "Here is the summary."}
    ]
    assert repository.events[1].payload == {
        "message_id": str(repository.messages[0].id),
        "message_stream_id": str(repository.messages[0].id),
        "role": "assistant",
        "text": "Here is the summary.",
        "stream_schema_version": "append-only.v1",
        "segment_count": 2,
        "content_utf8_bytes": len("Here is the summary.".encode("utf-8")),
        "content_sha256": hashlib.sha256(b"Here is the summary.").hexdigest(),
    }
    assert commits == ["commit", "commit", "commit"]
    assert controls.lock_released is True
    assert controls.cleared_active_run == (repository.run.thread_id, repository.run.id)
    assert controls.stream_cursor == (repository.run.id, 3)
    assert [event["event_type"] for event in transient_stream.events] == ["message.completed", "run.completed"]
    assert transient_stream.events[0]["payload"]["text"] == "Here is the summary."
    assert transient_stream.events[0]["dedupe_key"] == f"{repository.run.id}:message.completed:{repository.messages[0].id}"
    assert transient_stream.events[1]["dedupe_key"] == f"{repository.run.id}:run.completed"
    assert {event["durable"] for event in transient_stream.events} == {True}
    assert {event["optimistic"] for event in transient_stream.events} == {False}


def test_agent_run_worker_attaches_quick_replies_to_transient_completed_message_only() -> None:
    repository = FakeAgentRuntimeRepository()
    transient_stream = FakeTransientStream(operations=repository.operations)
    assistant_message_id = uuid4()
    replies = [{"id": "qr_1", "text": "看今日安排"}, {"id": "qr_2", "text": "先不保存"}, {"id": "qr_3", "text": "换简单版"}]

    async def handler(_run: AgentRun) -> AgentRunWorkerResult:
        return AgentRunWorkerResult(
            status="completed",
            final_text="已经整理好了。",
            assistant_message_id=assistant_message_id,
            quick_replies=replies,
        )

    worker = AgentRunWorker(repository=repository, handler=handler, transient_stream=transient_stream)

    asyncio.run(worker.run_once(run_id=repository.run.id))

    assert repository.messages[0].id == assistant_message_id
    assert repository.messages[0].content == {
        "text": "已经整理好了。",
    }
    assert repository.events[1].payload == {
        "message_id": str(assistant_message_id),
        "message_stream_id": str(assistant_message_id),
        "role": "assistant",
        "text": "已经整理好了。",
        "stream_schema_version": "append-only.v1",
        "segment_count": 0,
        "content_utf8_bytes": len("已经整理好了。".encode("utf-8")),
        "content_sha256": hashlib.sha256("已经整理好了。".encode("utf-8")).hexdigest(),
    }
    assert repository.events[2].event_type == "run.completed"
    assert [event["event_type"] for event in transient_stream.events] == [
        "message.completed",
        "run.completed",
    ]
    assert transient_stream.events[0] == {
        "event_type": "message.completed",
        "payload": {
            "message_id": str(assistant_message_id),
            "message_stream_id": str(assistant_message_id),
            "role": "assistant",
            "text": "已经整理好了。",
            "stream_schema_version": "append-only.v1",
            "segment_count": 0,
            "content_utf8_bytes": len("已经整理好了。".encode("utf-8")),
            "content_sha256": hashlib.sha256("已经整理好了。".encode("utf-8")).hexdigest(),
            "quick_replies": replies,
        },
        "dedupe_key": f"{repository.run.id}:message.completed:{assistant_message_id}",
        "optimistic": False,
        "durable": False,
    }
    assert transient_stream.events[1]["durable"] is True
    assert repository.operations[:4] == [
        "db:run.started",
        "redis:message.completed",
        "db:message.completed",
        "db:run.completed",
    ]


def test_agent_run_worker_persists_workflow_reply_cursor_on_completed_message() -> None:
    repository = FakeAgentRuntimeRepository()
    workflow_reply = {
        "workflow_state_id": str(uuid4()),
        "workflow_type": "milk_analysis",
        "revision": 3,
        "step_token": "opaque-step-token",
    }

    async def handler(_run: AgentRun) -> AgentRunWorkerResult:
        return AgentRunWorkerResult(
            status="completed",
            final_text="请告诉我宝宝最近的尿布情况。",
            workflow_reply=workflow_reply,
        )

    asyncio.run(AgentRunWorker(repository=repository, handler=handler).run_once(run_id=repository.run.id))

    assert repository.messages[0].content == {
        "text": "请告诉我宝宝最近的尿布情况。",
        "workflow_reply": workflow_reply,
    }
    assert repository.events[1].payload["workflow_reply"] == workflow_reply


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
        raise ApiError(
            code="dependency_not_configured",
            message="missing sdk",
            status=503,
            details={"exception_type": "MissingDependency", "message_excerpt": "sdk missing"},
        )

    worker = AgentRunWorker(repository=repository, handler=handler)

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "dependency_not_configured"
    assert run.error_details == {
        "code": "dependency_not_configured",
        "status": 503,
        "exception_type": "MissingDependency",
        "message_excerpt": "sdk missing",
    }
    assert [event.event_type for event in repository.events] == ["run.started", "run.failed"]


def test_agent_run_worker_does_not_duplicate_terminal_event_when_cancelled_externally() -> None:
    repository = FakeAgentRuntimeRepository()
    repository.external_status_on_refresh = "cancelled"
    controls = FakeAgentRunControls(cancel_sequence=[False, False, True])

    async def handler(_run: AgentRun) -> AgentRunWorkerResult:
        return AgentRunWorkerResult(status="completed", final_text="Finished too late.")

    worker = AgentRunWorker(repository=repository, controls=controls, handler=handler)

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is not None
    assert run.status == "cancelled"
    assert [event.event_type for event in repository.events] == ["run.started"]
    assert controls.cleared_active_run == (repository.run.thread_id, repository.run.id)
    assert controls.cancel_requested is False


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
    assert repository.events[-1].payload["semantic"]["phase"] == "confirming"


def test_agent_run_worker_does_not_resume_existing_running_run() -> None:
    repository = FakeAgentRuntimeRepository()
    repository.run.status = "running"
    handler_called = False

    async def handler(_run: AgentRun) -> AgentRunWorkerResult:
        nonlocal handler_called
        handler_called = True
        return AgentRunWorkerResult(status="completed")

    worker = AgentRunWorker(repository=repository, handler=handler)

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is repository.run
    assert run.status == "running"
    assert handler_called is False
    assert repository.events == []


def test_agent_run_worker_rejects_retired_runtime_without_calling_handler() -> None:
    repository = FakeAgentRuntimeRepository()
    repository.run.runtime_version = "momcozy-agent-v1"
    handler_called = False

    async def handler(_run: AgentRun) -> AgentRunWorkerResult:
        nonlocal handler_called
        handler_called = True
        return AgentRunWorkerResult(status="completed")

    worker = AgentRunWorker(repository=repository, handler=handler)

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is repository.run
    assert run.status == "failed"
    assert run.error_code == "runtime_version_retired"
    assert run.error_details == {
        "code": "runtime_version_retired",
        "runtime_version": "momcozy-agent-v1",
        "status": 409,
        "supported_runtime_version": "momcozy-agent-v2",
    }
    assert handler_called is False


def test_agent_run_queue_worker_interrupts_stale_running_runs_without_resuming() -> None:
    repository = FakeAgentRuntimeRepository()
    running = repository.add_run(status="running")
    handled_run_ids = []

    async def handler(run: AgentRun) -> AgentRunWorkerResult:
        handled_run_ids.append(run.id)
        return AgentRunWorkerResult(status="completed")

    run_worker = AgentRunWorker(repository=repository, handler=handler)
    queue_worker = AgentRunQueueWorker(repository=repository, run_worker=run_worker, batch_limit=5)

    result = asyncio.run(queue_worker.run_once())

    assert result.scanned == 2
    assert result.processed == 2
    assert result.terminal == 2
    assert result.interrupted == 1
    assert repository.run.status == "completed"
    assert running.status == "failed"
    assert running.error_code == INTERRUPTED_RUN_ERROR_CODE
    assert handled_run_ids == [repository.run.id]


def test_agent_run_worker_resumes_confirmed_action_in_worker_before_terminal_result() -> None:
    repository = FakeAgentRuntimeRepository()
    action = repository.add_action(status="confirmed", action_type="support.ticket.create")
    action_executor = FakeActionExecutor()
    handler_called = False

    async def handler(_run: AgentRun) -> AgentRunWorkerResult:
        nonlocal handler_called
        handler_called = True
        return AgentRunWorkerResult(status="completed")

    worker = AgentRunWorker(repository=repository, handler=handler, action_executor=action_executor)  # type: ignore[arg-type]

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is not None and run.status == "completed"
    assert handler_called is False
    assert action_executor.calls == [action.id]
    assert action.status == "applied"
    assert repository.messages[-1].content == {"text": "客服工单已提交。"}
    assert repository.events[-1].event_type == "run.completed"
    assert repository.events[-1].payload["reason"] == "action_applied"
    assert repository.events[-1].payload["action_id"] == str(action.id)
    assert repository.events[-1].payload["semantic"]["surface"] == "hidden"


def test_agent_run_worker_recovers_post_apply_crash_without_reapplying_or_duplicate_message() -> None:
    repository = FakeAgentRuntimeRepository()
    action = repository.add_action(status="applied", action_type="pregnancy.plan.create")
    existing = asyncio.run(
        repository.create_message(
            thread_id=repository.run.thread_id,
            run_id=repository.run.id,
            role="assistant",
            message_type="text",
            content={"text": "孕期计划已生成。"},
            status="completed",
        )
    )
    action_executor = FakeActionExecutor()
    worker = AgentRunWorker(repository=repository, action_executor=action_executor)  # type: ignore[arg-type]

    run = asyncio.run(worker.run_once(run_id=repository.run.id))

    assert run is not None and run.status == "completed"
    assert action_executor.calls == [action.id]
    assert action_executor.handler_calls == []
    assert repository.messages == [existing]
    assert repository.events[-1].payload["reason"] == "action_applied"
    assert repository.events[-1].payload["action_id"] == str(action.id)
    assert repository.events[-1].payload["semantic"]["surface"] == "hidden"


def test_agent_run_worker_requeues_stale_confirmed_action_instead_of_failing_run() -> None:
    repository = FakeAgentRuntimeRepository()
    repository.run.status = "running"
    action = repository.add_action(status="confirmed", action_type="support.ticket.create")
    controls = FakeAgentRunControls()
    worker = AgentRunWorker(repository=repository, controls=controls)

    run = asyncio.run(worker.interrupt_running(run_id=repository.run.id))

    assert run is not None and run.status == "queued"
    assert run.error_code == ""
    assert repository.events[-1].event_type == "run.queued"
    assert repository.events[-1].payload["reason"] == "action_resume"
    assert repository.events[-1].payload["action_id"] == str(action.id)
    assert repository.events[-1].payload["phase"] == "queued"
    assert repository.events[-1].payload["semantic"]["label"] == "我已经收到你的消息啦～"
    assert controls.notified_run_ids == [repository.run.id]


class FakeAgentRuntimeRepository:
    def __init__(self) -> None:
        self.run = AgentRun(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            status="queued",
            runtime_pattern="sdk_only",
            runtime_version="momcozy-agent-v2",
            prompt_version="",
            request_id="req",
            trace_id="trace",
            error_code="",
            error_details={},
        )
        self.runs = [self.run]
        self.messages = []
        self.context_items = []
        self.events = []
        self.actions = []
        self.operations = []
        self.external_status_on_refresh = ""

    def add_run(self, *, status):
        run = AgentRun(
            id=uuid4(),
            thread_id=uuid4(),
            actor_user_id=uuid4(),
            status=status,
            runtime_pattern="sdk_only",
            runtime_version="momcozy-agent-v2",
            prompt_version="",
            request_id="req",
            trace_id="trace",
            error_code="",
            error_details={},
        )
        self.runs.append(run)
        return run

    def add_action(self, *, status, action_type):
        target_type = "plan" if action_type == "pregnancy.plan.create" else "support_ticket"
        action = AgentAction(
            id=uuid4(),
            run_id=self.run.id,
            actor_user_id=self.run.actor_user_id,
            action_type=action_type,
            target_type=target_type,
            target_id="",
            status=status,
            side_effect_level="medium",
            preview_payload={},
            apply_payload={},
            idempotency_key="idem",
            error_code="",
        )
        self.actions.append(action)
        return action

    async def get_run(self, *, run_id):
        return next((run for run in self.runs if run.id == run_id), None)

    async def refresh_run(self, *, run):
        if self.external_status_on_refresh:
            run.status = self.external_status_on_refresh
        return run

    async def list_runnable_runs(self, *, limit):
        runnable = [run for run in self.runs if run.status == "queued"]
        return runnable[:limit]

    async def list_stale_running_runs(self, *, cutoff, limit):
        if cutoff is None:
            return []
        runnable = [run for run in self.runs if run.status == "running"]
        return runnable[:limit]

    async def list_actions_for_run(self, *, run_id):
        return [action for action in self.actions if action.run_id == run_id]

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

    async def mark_run_queued(self, *, run):
        run.status = "queued"
        run.started_at = None
        run.error_code = ""
        run.error_details = {}
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
            id=kwargs.get("message_id") or uuid4(),
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

    async def get_latest_assistant_message_for_run(self, *, run_id):
        return next(
            (message for message in reversed(self.messages) if message.run_id == run_id and message.role == "assistant"),
            None,
        )

    async def append_context_items(self, *, thread_id, run_id, items):
        self.context_items.extend(items)
        return list(items)

    async def append_event(self, **kwargs):
        self.operations.append(f"db:{kwargs['event_type']}")
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


class FakeTransientStream:
    def __init__(self, *, operations: list[str] | None = None) -> None:
        self.events = []
        self.operations = operations

    async def publish_application_event(self, **kwargs):
        if self.operations is not None:
            self.operations.append(f"redis:{kwargs['event_type']}")
        self.events.append(
            {
                "event_type": kwargs["event_type"],
                "payload": kwargs["payload"],
                "dedupe_key": kwargs["dedupe_key"],
                "optimistic": kwargs["optimistic"],
                "durable": kwargs["durable"],
            }
        )


class FakeAgentRunControls:
    def __init__(self, *, cancel_requested: bool = False, cancel_sequence: list[bool] | None = None) -> None:
        self.cancel_requested = cancel_requested
        self.cancel_sequence = cancel_sequence or []
        self.lock_released = False
        self.cleared_active_run = None
        self.stream_cursor = None
        self.notified_run_ids = []

    def run_lock(self, *, run_id, ttl_seconds=60):
        return FakeRunLock(self, acquired=True)

    async def is_cancel_requested(self, *, run_id):
        if self.cancel_sequence:
            return self.cancel_sequence.pop(0)
        return self.cancel_requested

    async def set_stream_cursor(self, *, run_id, sequence):
        self.stream_cursor = (run_id, sequence)

    async def clear_active_run(self, *, thread_id, run_id=None):
        self.cleared_active_run = (thread_id, run_id)

    async def clear_cancel(self, *, run_id):
        self.cancel_requested = False

    async def notify_run_queued(self, *, run_id):
        self.notified_run_ids.append(run_id)


class FakeActionExecutor:
    def __init__(self) -> None:
        self.calls = []
        self.handler_calls = []

    async def apply(self, action):
        self.calls.append(action.id)
        if action.status in {"applied", "failed"}:
            return AgentActionExecutionOutcome(action=action, replayed=True)
        self.handler_calls.append(action.id)
        action.status = "applied"
        return AgentActionExecutionOutcome(action=action)


class FakeRunLock:
    def __init__(self, controls: FakeAgentRunControls, *, acquired: bool) -> None:
        self.controls = controls
        self.acquired = acquired

    async def __aenter__(self):
        return self.acquired

    async def __aexit__(self, exc_type, exc, traceback):
        self.controls.lock_released = True
        return False
