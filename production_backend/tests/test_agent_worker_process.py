import asyncio
import json
import logging
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.core.settings import Settings
from production_backend.scripts.run_agent_worker import (
    AgentRunProcessResult,
    RunnableAgentRunRef,
    _log_agent_run_process_timing,
    _fail_run_after_worker_error,
    _process_with_concurrency,
    _wait_for_next_agent_run_signal,
    _with_metrics,
    run_agent_worker,
)
from production_backend.scripts import run_agent_worker as worker_module
from production_backend.scripts.worker_runtime import sleep_until_stop


def test_agent_worker_process_exits_safely_when_disabled() -> None:
    result = asyncio.run(run_agent_worker(settings=Settings(agent_runtime_worker_enabled=False), once=True))

    assert result == {"status": "disabled", "cycles": 0, "scanned": 0, "processed": 0, "terminal": 0, "interrupted": 0}


def test_agent_worker_process_can_return_metrics_snapshot_for_finite_runs() -> None:
    metrics = RequestMetrics()
    metrics.record_agent_sdk(node_name="openai_agents_sdk", outcome="failed", error_code="dependency_not_configured")

    result = _with_metrics({"status": "ok", "cycles": 1, "scanned": 0, "processed": 0, "terminal": 0}, metrics)

    assert result["metrics"]["agent_sdk"][0]["node_name"] == "openai_agents_sdk"
    assert result["metrics"]["agent_sdk"][0]["error_code_counts"]["dependency_not_configured"] == 1


def test_agent_worker_process_bounds_in_process_run_concurrency() -> None:
    active = 0
    max_active = 0
    processed = []

    async def processor(run_id):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        processed.append(run_id)
        active -= 1
        return AgentRunProcessResult(status_changed=True, terminal=True)

    run_ids = [uuid4() for _ in range(5)]

    results = asyncio.run(_process_with_concurrency(items=run_ids, concurrency=2, processor=processor))

    assert max_active == 2
    assert set(processed) == set(run_ids)
    assert len(results) == 5
    assert all(result.status_changed and result.terminal for result in results)


def test_agent_worker_process_isolates_one_failed_run_from_the_batch() -> None:
    failed_run_id = uuid4()
    completed_run_id = uuid4()
    processed = []

    async def processor(run_id):
        if run_id == failed_run_id:
            raise RuntimeError("run failed")
        processed.append(run_id)
        return AgentRunProcessResult(status_changed=True, terminal=True)

    results = asyncio.run(
        _process_with_concurrency(
            items=[failed_run_id, completed_run_id],
            concurrency=2,
            processor=processor,
        )
    )

    assert processed == [completed_run_id]
    assert results == [
        AgentRunProcessResult(status_changed=False, terminal=False),
        AgentRunProcessResult(status_changed=True, terminal=True),
    ]


def test_runnable_agent_run_ref_carries_scanned_status() -> None:
    run_id = uuid4()

    ref = RunnableAgentRunRef(run_id=run_id, status="queued")

    assert ref.run_id == run_id
    assert ref.status == "queued"


def test_agent_worker_process_logs_run_timing(caplog) -> None:
    caplog.set_level(logging.INFO, logger="production_backend.agent_runtime")
    run_id = uuid4()

    _log_agent_run_process_timing(
        run_id=run_id,
        before_status="queued",
        after_status="completed",
        started_at=perf_counter(),
        outcome="completed",
        queue_wait_ms=42.0,
    )

    payloads = [json.loads(record.getMessage()) for record in caplog.records if record.name == "production_backend.agent_runtime"]
    assert {
        "event": "agent.run.worker_execute",
        "run_id": str(run_id),
        "before_status": "queued",
        "after_status": "completed",
        "outcome": "completed",
        "terminal": True,
        "queue_wait_ms": 42.0,
        "error_type": "",
    }.items() <= payloads[-1].items()
    assert payloads[-1]["duration_ms"] >= 0


def test_agent_worker_idle_wait_uses_queue_signal() -> None:
    controls = FakeAgentRunControls()

    asyncio.run(_wait_for_next_agent_run_signal(controls=controls, idle_seconds=0.25, stop_event=None))

    assert controls.wait_timeouts == [0.25]


def test_agent_worker_idle_wait_returns_immediately_when_stopping() -> None:
    controls = FakeAgentRunControls()
    stop_event = asyncio.Event()
    stop_event.set()

    asyncio.run(_wait_for_next_agent_run_signal(controls=controls, idle_seconds=30, stop_event=stop_event))

    assert controls.wait_timeouts == []


def test_agent_worker_failure_cleanup_publishes_live_before_persisting(monkeypatch) -> None:
    operations = []
    run = SimpleNamespace(id=uuid4(), thread_id=uuid4(), status="running")

    class FakeRepository:
        async def get_run(self, *, run_id):
            return run if run_id == run.id else None

        async def mark_run_failed(self, **kwargs):
            operations.append("db:mark_failed")
            kwargs["run"].status = "failed"
            return kwargs["run"]

        async def append_event(self, **kwargs):
            operations.append(f"db:{kwargs['event_type']}")
            return SimpleNamespace(sequence=7)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            operations.append("db:commit")

    class FakeTransientStream:
        async def publish_application_event(self, **kwargs):
            operations.append(f"redis:{kwargs['event_type']}")

    class FakeControls:
        async def set_stream_cursor(self, **_kwargs):
            operations.append("redis:set_cursor")

        async def clear_active_run(self, **_kwargs):
            operations.append("redis:clear_active")

        async def clear_cancel(self, **_kwargs):
            operations.append("redis:clear_cancel")

    monkeypatch.setattr(worker_module, "AgentRuntimeRepository", lambda _session: FakeRepository())
    monkeypatch.setattr(worker_module, "AgentTransientStream", lambda _redis: FakeTransientStream())

    result = asyncio.run(
        _fail_run_after_worker_error(
            run_id=run.id,
            before_status="running",
            session_factory=FakeSession,
            redis_client=object(),
            controls=FakeControls(),
            error_type="RuntimeError",
        )
    )

    assert result == AgentRunProcessResult(status_changed=True, terminal=True)
    assert operations == [
        "redis:run.failed",
        "db:mark_failed",
        "db:run.failed",
        "db:commit",
        "redis:set_cursor",
        "redis:clear_active",
        "redis:clear_cancel",
    ]


def test_worker_runtime_sleep_returns_when_stop_event_is_set() -> None:
    async def run() -> None:
        stop_event = asyncio.Event()
        stop_event.set()
        await sleep_until_stop(seconds=30, stop_event=stop_event)

    asyncio.run(run())


class FakeAgentRunControls:
    def __init__(self) -> None:
        self.wait_timeouts = []

    async def wait_for_run_queue_signal(self, *, timeout_seconds):
        self.wait_timeouts.append(timeout_seconds)
        return "run-id"
