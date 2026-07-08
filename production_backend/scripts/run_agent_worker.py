from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.core.settings import Settings
from production_backend.app.infrastructure.db.session import create_db_engine, create_session_factory
from production_backend.app.infrastructure.object_storage.factory import create_object_storage
from production_backend.app.infrastructure.redis.client import close_redis_client, create_redis_client
from production_backend.app.modules.agent_runtime.context import BusinessFactsProjector
from production_backend.app.modules.agent_runtime.event_stream.sink import AgentEventSink
from production_backend.app.modules.agent_runtime.event_stream.transient import AgentTransientStream
from production_backend.app.modules.agent_runtime.graphs import AgentGraphCheckpointStore, AgentRuntimeGraphRunner
from production_backend.app.modules.agent_runtime.memory.service import AgentMemoryRepository, AgentMemoryService
from production_backend.app.modules.agent_runtime.repository import AgentRuntimeRepository
from production_backend.app.modules.agent_runtime.routing import ModelSkillIntentPlanner, SkillRoutingService
from production_backend.app.modules.agent_runtime.run_lifecycle.controls import AgentRunControls
from production_backend.app.modules.agent_runtime.run_lifecycle.executor import AgentRuntimeExecutor
from production_backend.app.modules.agent_runtime.run_lifecycle.state_store import AgentRuntimeStateStore
from production_backend.app.modules.agent_runtime.safety.service import AgentSafetyService
from production_backend.app.modules.agent_runtime.sdk import create_agent_sdk_runner
from production_backend.app.modules.agent_runtime.service import AgentRuntimeService
from production_backend.app.modules.agent_runtime.tools import ToolExecutor, build_default_tool_handlers, default_tool_registry
from production_backend.app.modules.assets.service import ProductAssetService
from production_backend.app.modules.audit import AuditService, IdempotencyService, OutboxService
from production_backend.app.modules.audit.repository import AuditRepository, OutboxRepository
from production_backend.app.modules.devices.repository import DevicesRepository
from production_backend.app.modules.devices.service import DevicesService
from production_backend.app.modules.diary.repository import DiaryRepository
from production_backend.app.modules.diary.service import DiaryService
from production_backend.app.modules.files.repository import FileRepository
from production_backend.app.modules.files.vision_service import FileVisionService
from production_backend.app.modules.plans.repository import PlansRepository
from production_backend.app.modules.plans.service import PlansService
from production_backend.app.modules.profiles.repository import ProfileRepository
from production_backend.app.modules.profiles.service import ProfileService
from production_backend.app.modules.records.repository import RecordsRepository
from production_backend.app.modules.records.service import RecordsService
from production_backend.app.workers.agent_run import AgentRunQueueWorkerResult, AgentRunWorker, TERMINAL_RUN_STATUSES
from production_backend.scripts.worker_runtime import install_stop_signal_handlers, sleep_until_stop


@dataclass(frozen=True)
class AgentRunProcessResult:
    status_changed: bool
    terminal: bool


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
    object_storage = create_object_storage(resolved_settings)
    redis_client = create_redis_client(resolved_settings)
    controls = AgentRunControls(redis_client)
    metrics = RequestMetrics()
    totals: dict[str, Any] = {"status": "ok", "cycles": 0, "scanned": 0, "processed": 0, "terminal": 0}
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                totals["status"] = "stopping"
                return _with_metrics(totals, metrics)
            run_ids = await _list_runnable_run_ids(
                session_factory=session_factory,
                batch_limit=resolved_settings.agent_runtime_worker_batch_limit,
                recover_running_older_than_seconds=resolved_settings.agent_runtime_recover_running_older_than_seconds,
            )
            run_results = await _process_with_concurrency(
                items=run_ids,
                concurrency=resolved_settings.agent_runtime_worker_concurrency,
                processor=lambda run_id: _process_agent_run(
                    run_id=run_id,
                    session_factory=session_factory,
                    settings=resolved_settings,
                    object_storage=object_storage,
                    redis_client=redis_client,
                    controls=controls,
                    metrics=metrics,
                ),
            )
            result = AgentRunQueueWorkerResult(
                scanned=len(run_ids),
                processed=sum(1 for item in run_results if item.status_changed),
                terminal=sum(1 for item in run_results if item.terminal),
            )

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


async def _list_runnable_run_ids(
    *,
    session_factory: Any,
    batch_limit: int,
    recover_running_older_than_seconds: int | None,
) -> list[UUID]:
    async with session_factory() as session:
        repository = AgentRuntimeRepository(session)
        runs = await repository.list_runnable_runs(
            limit=batch_limit,
            recover_running_before=_recover_running_before(recover_running_older_than_seconds),
        )
        return [run.id for run in runs]


async def _process_with_concurrency(
    *,
    items: Sequence[UUID],
    concurrency: int,
    processor: Callable[[UUID], Awaitable[AgentRunProcessResult]],
) -> list[AgentRunProcessResult]:
    if not items:
        return []
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(item: UUID) -> AgentRunProcessResult:
        async with semaphore:
            return await processor(item)

    return list(await asyncio.gather(*(guarded(item) for item in items)))


async def _process_agent_run(
    *,
    run_id: UUID,
    session_factory: Any,
    settings: Settings,
    object_storage: Any,
    redis_client: Any,
    controls: AgentRunControls,
    metrics: RequestMetrics,
) -> AgentRunProcessResult:
    async with session_factory() as session:
        repository = AgentRuntimeRepository(session)
        before_run = await repository.get_run(run_id=run_id)
        before_status = before_run.status if before_run is not None else ""
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
        file_vision_service = FileVisionService(
            repository=FileRepository(session),
            object_storage=object_storage,
            settings=settings,
        )
        tool_registry = default_tool_registry()
        memory_service = AgentMemoryService(repository=AgentMemoryRepository(session))
        event_sink = AgentEventSink(repository=repository, controls=controls, after_append=session.commit)
        tool_handlers = build_default_tool_handlers(
            profile_service=profile_service,
            records_service=records_service,
            plans_service=plans_service,
            diary_service=diary_service,
            devices_service=devices_service,
            asset_service=ProductAssetService(),
            file_vision_service=file_vision_service,
            agent_runtime_service=agent_runtime_service,
        )
        tool_executor = ToolExecutor(
            registry=tool_registry,
            repository=repository,
            event_sink=event_sink,
            metrics=metrics,
            object_storage=object_storage,
            max_inline_output_bytes=settings.agent_runtime_max_inline_payload_bytes,
            handlers=tool_handlers,
        )
        checkpoint_store = AgentGraphCheckpointStore(repository=repository)
        sdk_runner = create_agent_sdk_runner(settings=settings, metrics=metrics)
        runtime_executor = AgentRuntimeExecutor(
            repository=repository,
            checkpoint_store=checkpoint_store,
            state_store=AgentRuntimeStateStore(repository=repository),
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            event_sink=event_sink,
            memory_service=memory_service,
            business_facts_projector=BusinessFactsProjector(handlers=tool_handlers),
            transient_stream=AgentTransientStream(redis_client),
            routing_service=SkillRoutingService(planner=ModelSkillIntentPlanner(sdk_runner=sdk_runner)),
            sdk_runner=sdk_runner,
            object_storage=object_storage,
            max_inline_artifact_payload_bytes=settings.agent_runtime_max_inline_payload_bytes,
        )
        handler = AgentRuntimeGraphRunner(
            repository=repository,
            checkpoint_store=checkpoint_store,
            node_handler=runtime_executor,
        )
        worker = AgentRunWorker(
            repository=repository,
            controls=controls,
            handler=handler,
            after_event_append=session.commit,
        )
        try:
            after_run = await worker.run_once(run_id=run_id)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        after_status = after_run.status if after_run is not None else before_status
        return AgentRunProcessResult(
            status_changed=bool(after_run is not None and after_status != before_status),
            terminal=after_status in TERMINAL_RUN_STATUSES,
        )


def _recover_running_before(recover_running_older_than_seconds: int | None) -> datetime | None:
    if recover_running_older_than_seconds is None:
        return None
    return datetime.now(timezone.utc) - timedelta(seconds=recover_running_older_than_seconds)


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
