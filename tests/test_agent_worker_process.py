import asyncio
import json
import logging
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from app.core.metrics import RequestMetrics
from app.core.settings import Settings
from scripts.run_agent_worker import (
    AgentRunProcessResult,
    RunnableAgentRunRef,
    _expire_due_fact_candidates,
    _run_fact_worker_lane,
    _log_agent_run_process_timing,
    _fail_run_after_worker_error,
    _process_with_concurrency,
    _supervise_fact_worker_lane,
    _wait_for_next_fact_signal,
    _wait_for_next_agent_run_signal,
    _with_metrics,
    run_agent_worker,
)
from scripts import run_agent_worker as worker_module
from scripts.worker_runtime import sleep_until_stop


def test_agent_worker_process_exits_safely_when_disabled() -> None:
    result = asyncio.run(run_agent_worker(settings=Settings(agent_runtime_worker_enabled=False), once=True))

    assert result == {"status": "disabled", "cycles": 0, "scanned": 0, "processed": 0, "terminal": 0, "interrupted": 0}


def test_agent_worker_process_can_return_metrics_snapshot_for_finite_runs() -> None:
    metrics = RequestMetrics()
    metrics.record_agent_sdk(node_name="openai_responses", outcome="failed", error_code="dependency_not_configured")

    result = _with_metrics({"status": "ok", "cycles": 1, "scanned": 0, "processed": 0, "terminal": 0}, metrics)

    assert result["metrics"]["agent_sdk"][0]["node_name"] == "openai_responses"
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


def test_agent_fact_worker_idle_wait_uses_separate_queue_signal() -> None:
    controls = FakeAgentRunControls()

    asyncio.run(_wait_for_next_fact_signal(controls=controls, idle_seconds=0.25, stop_event=None))

    assert controls.fact_wait_timeouts == [0.25]
    assert controls.wait_timeouts == []


def test_agent_worker_runs_main_and_fact_lanes_concurrently(monkeypatch) -> None:
    main_started = asyncio.Event()
    fact_started = asyncio.Event()

    async def fake_main_lane(**_kwargs):
        main_started.set()
        await fact_started.wait()
        return {"status": "ok", "cycles": 1, "scanned": 0, "processed": 0, "terminal": 0, "interrupted": 0}

    async def fake_fact_lane(**_kwargs):
        await main_started.wait()
        fact_started.set()
        return {"status": "ok", "cycles": 1, "scanned": 0, "processed": 0, "completed": 0, "dead_lettered": 0}

    class FakeEngine:
        async def dispose(self):
            return None

    async def fake_close_redis(_redis):
        return None

    monkeypatch.setattr(worker_module, "create_db_engine", lambda _settings: FakeEngine())
    monkeypatch.setattr(worker_module, "create_session_factory", lambda _engine: object())
    monkeypatch.setattr(worker_module, "create_object_storage", lambda _settings: object())
    monkeypatch.setattr(worker_module, "create_redis_client", lambda _settings: object())
    monkeypatch.setattr(worker_module, "close_redis_client", fake_close_redis)
    monkeypatch.setattr(worker_module, "_run_agent_run_lane", fake_main_lane)
    monkeypatch.setattr(worker_module, "_run_fact_worker_lane", fake_fact_lane)

    result = asyncio.run(
        run_agent_worker(
            settings=Settings(
                app_env="test",
                agent_runtime_worker_enabled=True,
                agent_fact_extraction_enabled=True,
                openai_api_key="test-key",
            ),
            once=True,
        )
    )

    assert result["status"] == "ok"
    assert result["facts"]["status"] == "ok"
    assert main_started.is_set() and fact_started.is_set()


def test_agent_fact_lane_crash_restarts_without_cancelling_main_lane() -> None:
    main_completed = asyncio.Event()
    attempts = 0

    async def main_lane():
        await asyncio.sleep(0)
        main_completed.set()
        return "main-completed"

    async def fact_lane():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("fact lane crashed")
        return {"status": "ok"}

    async def exercise():
        return await asyncio.gather(
            main_lane(),
            _supervise_fact_worker_lane(
                lane_factory=fact_lane,
                restart=True,
                restart_delay_seconds=0.1,
                stop_event=None,
            ),
        )

    main_result, fact_result = asyncio.run(exercise())

    assert main_completed.is_set()
    assert main_result == "main-completed"
    assert attempts == 2
    assert fact_result["status"] == "ok"


def test_agent_fact_worker_cycle_failure_isolated_as_degraded(monkeypatch) -> None:
    async def fail_claim(**_kwargs):
        raise RuntimeError("fact database unavailable")

    monkeypatch.setattr(worker_module, "_claim_due_fact_extractions", fail_claim)

    result = asyncio.run(
        _run_fact_worker_lane(
            worker=object(),
            settings=Settings(),
            once=True,
            max_cycles=None,
            stop_event=None,
            session_factory=object(),
            controls=FakeAgentRunControls(),
        )
    )

    assert result == {
        "status": "degraded",
        "cycles": 1,
        "scanned": 0,
        "processed": 0,
        "completed": 0,
        "dead_lettered": 0,
    }


def test_agent_fact_worker_redis_wait_failure_is_retried_without_escaping(monkeypatch) -> None:
    async def no_claims(**_kwargs):
        return []

    class FailingFactWaitControls(FakeAgentRunControls):
        async def wait_for_fact_queue_signal(self, *, timeout_seconds):
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(worker_module, "_claim_due_fact_extractions", no_claims)

    result = asyncio.run(
        _run_fact_worker_lane(
            worker=object(),
            settings=Settings(agent_fact_worker_idle_seconds=0.01),
            once=False,
            max_cycles=2,
            stop_event=None,
            session_factory=object(),
            controls=FailingFactWaitControls(),
        )
    )

    assert result["status"] == "degraded"
    assert result["cycles"] == 2


def test_agent_fact_expiration_scrubs_with_value_free_audit(monkeypatch) -> None:
    fact = SimpleNamespace(
        id=uuid4(),
        owner_user_id=uuid4(),
        fact_key="profile.age",
        fact_kind="conversation_candidate",
    )
    audit_calls = []

    class FakeSession:
        committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            self.committed = True

    session = FakeSession()

    class FakeRepository:
        async def expire_due_candidates(self, **_kwargs):
            return [fact]

    class FakeAuditService:
        async def record(self, **kwargs):
            audit_calls.append(kwargs)

    monkeypatch.setattr(worker_module, "AgentFactRepository", lambda _session: FakeRepository())
    monkeypatch.setattr(worker_module, "AuditRepository", lambda _session: object())
    monkeypatch.setattr(worker_module, "AuditService", lambda **_kwargs: FakeAuditService())

    expired = asyncio.run(
        _expire_due_fact_candidates(
            session_factory=lambda: session,
            batch_limit=10,
        )
    )

    assert expired == 1
    assert session.committed is True
    assert audit_calls == [
        {
            "actor_user_id": fact.owner_user_id,
            "actor_type": "service",
            "actor_service": "agent-fact-worker",
            "action": "agent.fact.candidate.expired",
            "resource_type": "agent_user_fact",
            "resource_id": str(fact.id),
            "details": {
                "fact_key": "profile.age",
                "fact_kind": "conversation_candidate",
                "deletion_reason": "expired",
            },
        }
    ]


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
        self.fact_wait_timeouts = []

    async def wait_for_run_queue_signal(self, *, timeout_seconds):
        self.wait_timeouts.append(timeout_seconds)
        return "run-id"

    async def wait_for_fact_queue_signal(self, *, timeout_seconds):
        self.fact_wait_timeouts.append(timeout_seconds)
        return "run-id"
