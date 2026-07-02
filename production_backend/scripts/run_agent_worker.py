from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.core.settings import Settings
from production_backend.app.infrastructure.db.session import create_db_engine, create_session_factory
from production_backend.app.infrastructure.redis.client import close_redis_client, create_redis_client
from production_backend.app.modules.agent_runtime.controls import AgentRunControls
from production_backend.app.modules.agent_runtime.events import AgentEventSink
from production_backend.app.modules.agent_runtime.graphs import AgentGraphCheckpointStore
from production_backend.app.modules.agent_runtime.repository import AgentRuntimeRepository
from production_backend.app.modules.agent_runtime.runtime import AgentRuntimeExecutor
from production_backend.app.modules.agent_runtime.safety import AgentSafetyService
from production_backend.app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner
from production_backend.app.modules.agent_runtime.service import AgentRuntimeService
from production_backend.app.modules.agent_runtime.state_store import AgentRuntimeStateStore
from production_backend.app.modules.agent_runtime.tools import ToolExecutor, build_default_tool_handlers, default_tool_registry
from production_backend.app.modules.audit import AuditService, IdempotencyService, OutboxService
from production_backend.app.modules.audit.repository import AuditRepository, OutboxRepository
from production_backend.app.modules.devices.repository import DevicesRepository
from production_backend.app.modules.devices.service import DevicesService
from production_backend.app.modules.diary.repository import DiaryRepository
from production_backend.app.modules.diary.service import DiaryService
from production_backend.app.modules.plans.repository import PlansRepository
from production_backend.app.modules.plans.service import PlansService
from production_backend.app.modules.profiles.repository import ProfileRepository
from production_backend.app.modules.profiles.service import ProfileService
from production_backend.app.modules.records.repository import RecordsRepository
from production_backend.app.modules.records.service import RecordsService
from production_backend.app.workers.agent_run import AgentRunQueueWorker, AgentRunWorker
from production_backend.scripts.worker_runtime import install_stop_signal_handlers, sleep_until_stop


async def run_agent_worker(
    *,
    settings: Settings | None = None,
    once: bool = False,
    max_cycles: int | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or Settings.from_env()
    resolved_settings.validate_for_startup()
    if not resolved_settings.agent_runtime_worker_enabled:
        return {"status": "disabled", "cycles": 0, "scanned": 0, "processed": 0, "terminal": 0}

    db_engine = create_db_engine(resolved_settings)
    session_factory = create_session_factory(db_engine)
    redis_client = create_redis_client(resolved_settings)
    controls = AgentRunControls(redis_client)
    metrics = RequestMetrics()
    totals: dict[str, Any] = {"status": "ok", "cycles": 0, "scanned": 0, "processed": 0, "terminal": 0}
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                totals["status"] = "stopping"
                return _with_metrics(totals, metrics)
            async with session_factory() as session:
                repository = AgentRuntimeRepository(session)
                audit_repository = AuditRepository(session)
                agent_runtime_service = AgentRuntimeService(
                    repository=repository,
                    idempotency_service=IdempotencyService(repository=audit_repository),
                    outbox_service=OutboxService(repository=OutboxRepository(session)),
                    controls=controls,
                    safety_service=AgentSafetyService(repository=repository, metrics=metrics),
                )
                profile_service = ProfileService(
                    repository=ProfileRepository(session),
                    audit_service=AuditService(repository=audit_repository),
                    idempotency_service=IdempotencyService(repository=audit_repository),
                )
                records_service = RecordsService(
                    repository=RecordsRepository(session),
                    audit_service=AuditService(repository=audit_repository),
                    idempotency_service=IdempotencyService(repository=audit_repository),
                )
                plans_service = PlansService(
                    repository=PlansRepository(session),
                    audit_service=AuditService(repository=audit_repository),
                    idempotency_service=IdempotencyService(repository=audit_repository),
                )
                diary_service = DiaryService(
                    repository=DiaryRepository(session),
                    audit_service=AuditService(repository=audit_repository),
                )
                devices_service = DevicesService(
                    repository=DevicesRepository(session),
                    audit_service=AuditService(repository=audit_repository),
                    idempotency_service=IdempotencyService(repository=audit_repository),
                )
                tool_registry = default_tool_registry()
                event_sink = AgentEventSink(repository=repository, controls=controls, after_append=session.commit)
                tool_executor = ToolExecutor(
                    registry=tool_registry,
                    repository=repository,
                    event_sink=event_sink,
                    metrics=metrics,
                    handlers=build_default_tool_handlers(
                        profile_service=profile_service,
                        records_service=records_service,
                        plans_service=plans_service,
                        diary_service=diary_service,
                        devices_service=devices_service,
                        agent_runtime_service=agent_runtime_service,
                    ),
                )
                handler = AgentRuntimeExecutor(
                    repository=repository,
                    checkpoint_store=AgentGraphCheckpointStore(repository=repository),
                    state_store=AgentRuntimeStateStore(repository=repository),
                    tool_registry=tool_registry,
                    tool_executor=tool_executor,
                    event_sink=event_sink,
                    sdk_runner=OpenAIAgentsSdkRunner(model=resolved_settings.openai_model, metrics=metrics),
                )
                worker = AgentRunQueueWorker(
                    repository=repository,
                    run_worker=AgentRunWorker(
                        repository=repository,
                        controls=controls,
                        handler=handler,
                        after_event_append=session.commit,
                    ),
                    batch_limit=resolved_settings.agent_runtime_worker_batch_limit,
                    recover_running_older_than_seconds=resolved_settings.agent_runtime_recover_running_older_than_seconds,
                )
                try:
                    result = await worker.run_once()
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

            totals["cycles"] += 1
            totals["scanned"] += result.scanned
            totals["processed"] += result.processed
            totals["terminal"] += result.terminal
            if once or (max_cycles is not None and totals["cycles"] >= max_cycles):
                return _with_metrics(totals, metrics)
            if result.scanned == 0:
                await sleep_until_stop(seconds=resolved_settings.agent_runtime_worker_idle_seconds, stop_event=stop_event)
    finally:
        await close_redis_client(redis_client)
        await db_engine.dispose()


def _with_metrics(totals: dict[str, Any], metrics: RequestMetrics) -> dict[str, Any]:
    return {**totals, "metrics": metrics.snapshot()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the production agent runtime worker.")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
    parser.add_argument("--max-cycles", type=int, default=None, help="Process at most this many cycles before exiting.")
    args = parser.parse_args()

    asyncio.run(_run_from_cli(once=args.once, max_cycles=args.max_cycles))


async def _run_from_cli(*, once: bool, max_cycles: int | None) -> None:
    stop_event = asyncio.Event()
    install_stop_signal_handlers(stop_event)
    result = await run_agent_worker(once=once, max_cycles=max_cycles, stop_event=stop_event)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
