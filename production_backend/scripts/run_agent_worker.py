from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, TypeVar
from uuid import UUID

from production_backend.app.core.logging import log_agent_runtime_event
from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.core.settings import Settings
from production_backend.app.infrastructure.db.session import create_db_engine, create_session_factory
from production_backend.app.infrastructure.object_storage.factory import create_object_storage
from production_backend.app.infrastructure.redis.client import close_redis_client, create_redis_client
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.context import BusinessFactsProjector
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools import (
    ToolExecutor,
    build_default_tool_handlers,
    default_tool_registry,
)
from production_backend.app.modules.agent_runtime.actions.executor import AgentActionExecutor
from production_backend.app.modules.agent_runtime.actions.registry import build_agent_action_handlers
from production_backend.app.modules.agent_runtime.event_stream.sink import AgentEventSink
from production_backend.app.modules.agent_runtime.event_stream.transient import AgentTransientStream
from production_backend.app.modules.agent_runtime.memory.service import AgentMemoryRepository, AgentMemoryService
from production_backend.app.modules.agent_runtime.facts import (
    AgentFactExtractor,
    AgentFactRepository,
    AgentFactService,
    FactExtractionJobClaim,
)
from production_backend.app.modules.agent_runtime.facts.worker import (
    AgentFactExtractionWorker,
    FactExtractionProcessResult,
)
from production_backend.app.modules.agent_runtime.repository import AgentRuntimeRepository
from production_backend.app.modules.agent_runtime.run_lifecycle.controls import AgentRunControls
from production_backend.app.modules.agent_runtime.run_lifecycle.executor import AgentRuntimeExecutor
from production_backend.app.modules.agent_runtime.run_lifecycle.quick_replies import QuickReplyFinalizer
from production_backend.app.modules.agent_runtime.run_lifecycle.working_context import RedisAgentWorkingContextStore
from production_backend.app.modules.agent_runtime.sdk import OpenAIResponsesRunner, create_agent_model_runner
from production_backend.app.modules.agent_runtime.service import AgentRuntimeService
from production_backend.app.modules.assets.service import ProductAssetService
from production_backend.app.modules.audit import AuditService, IdempotencyService
from production_backend.app.modules.audit.repository import AuditRepository
from production_backend.app.modules.devices.repository import DevicesRepository
from production_backend.app.modules.devices.service import DevicesService
from production_backend.app.modules.diary.repository import DiaryRepository
from production_backend.app.modules.diary.service import DiaryService
from production_backend.app.modules.notifications import NotificationsService
from production_backend.app.modules.notifications.repository import NotificationsRepository
from production_backend.app.modules.plans.repository import PlansRepository
from production_backend.app.modules.plans.service import PlansService
from production_backend.app.modules.profiles.repository import ProfileRepository
from production_backend.app.modules.profiles.service import ProfileService
from production_backend.app.modules.records.repository import RecordsRepository
from production_backend.app.modules.records.service import RecordsService
from production_backend.app.modules.support import SupportTicketsService
from production_backend.app.modules.support.repository import SupportTicketsRepository
from production_backend.app.workers.agent_run import AgentRunQueueWorkerResult, AgentRunWorker, TERMINAL_RUN_STATUSES
from production_backend.scripts.worker_runtime import install_stop_signal_handlers


@dataclass(frozen=True)
class AgentRunProcessResult:
    status_changed: bool
    terminal: bool
    interrupted: bool = False


@dataclass(frozen=True)
class RunnableAgentRunRef:
    run_id: UUID
    status: str


ItemT = TypeVar("ItemT")


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
        return {"status": "disabled", "cycles": 0, "scanned": 0, "processed": 0, "terminal": 0, "interrupted": 0}

    db_engine = create_db_engine(resolved_settings)
    session_factory = create_session_factory(db_engine)
    object_storage = create_object_storage(resolved_settings)
    redis_client = create_redis_client(resolved_settings)
    controls = AgentRunControls(redis_client)
    metrics = RequestMetrics()
    lane_tasks: list[asyncio.Task[dict[str, Any]]] = []
    try:
        lane_tasks.append(
            asyncio.create_task(
                _run_agent_run_lane(
                    settings=resolved_settings,
                    once=once,
                    max_cycles=max_cycles,
                    stop_event=stop_event,
                    session_factory=session_factory,
                    object_storage=object_storage,
                    redis_client=redis_client,
                    controls=controls,
                    metrics=metrics,
                )
            )
        )
        if resolved_settings.agent_fact_extraction_enabled:
            fact_runner = OpenAIResponsesRunner(
                model=resolved_settings.agent_fact_extraction_model,
                max_turns=1,
                timeout_seconds=resolved_settings.agent_fact_extraction_timeout_seconds,
                api_key=resolved_settings.openai_api_key,
                reasoning_effort="none",
                store_responses=False,
                metrics=metrics,
                metrics_node_name="turn_fact_extractor",
            )
            fact_worker = AgentFactExtractionWorker(
                session_factory=session_factory,
                extractor=AgentFactExtractor(
                    model_runner=fact_runner,
                    extractor_version=resolved_settings.agent_fact_extraction_version,
                ),
            )
            lane_tasks.append(
                asyncio.create_task(
                    _supervise_fact_worker_lane(
                        lane_factory=lambda: _run_fact_worker_lane(
                            worker=fact_worker,
                            settings=resolved_settings,
                            once=once,
                            max_cycles=max_cycles,
                            stop_event=stop_event,
                            session_factory=session_factory,
                            controls=controls,
                        ),
                        restart=not once and max_cycles is None,
                        restart_delay_seconds=max(resolved_settings.agent_fact_worker_idle_seconds, 0.1),
                        stop_event=stop_event,
                    )
                )
            )
        lane_results = await asyncio.gather(*lane_tasks)
        totals = lane_results[0]
        if len(lane_results) > 1:
            totals["facts"] = lane_results[1]
        return _with_metrics(totals, metrics)
    finally:
        for task in lane_tasks:
            if not task.done():
                task.cancel()
        for task in lane_tasks:
            if not task.done():
                with suppress(asyncio.CancelledError):
                    await task
        await close_redis_client(redis_client)
        await db_engine.dispose()


async def _run_agent_run_lane(
    *,
    settings: Settings,
    once: bool,
    max_cycles: int | None,
    stop_event: asyncio.Event | None,
    session_factory: Any,
    object_storage: Any,
    redis_client: Any,
    controls: AgentRunControls,
    metrics: RequestMetrics,
) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "status": "ok",
        "cycles": 0,
        "scanned": 0,
        "processed": 0,
        "terminal": 0,
        "interrupted": 0,
    }
    while True:
        if stop_event is not None and stop_event.is_set():
            totals["status"] = "stopping"
            return totals
        try:
            await _expire_due_fact_candidates(
                session_factory=session_factory,
                batch_limit=settings.agent_fact_worker_batch_limit,
            )
        except Exception as exc:
            log_agent_runtime_event(
                "agent.fact.expiration_failed",
                outcome="exception",
                error_type=type(exc).__name__,
            )
        stale_run_refs = await _list_stale_running_run_refs(
            session_factory=session_factory,
            batch_limit=settings.agent_runtime_worker_batch_limit,
            interrupt_running_older_than_seconds=settings.agent_runtime_interrupt_running_older_than_seconds,
        )
        interrupt_results = await _process_with_concurrency(
            items=stale_run_refs,
            concurrency=settings.agent_runtime_worker_concurrency,
            processor=lambda run_ref: _interrupt_agent_run(
                run_id=run_ref.run_id,
                before_status=run_ref.status,
                session_factory=session_factory,
                redis_client=redis_client,
                controls=controls,
            ),
        )
        queued_limit = max(0, settings.agent_runtime_worker_batch_limit - len(stale_run_refs))
        run_refs = await _list_runnable_run_refs(
            session_factory=session_factory,
            batch_limit=queued_limit,
        )
        run_results = await _process_with_concurrency(
            items=run_refs,
            concurrency=settings.agent_runtime_worker_concurrency,
            processor=lambda run_ref: _process_agent_run(
                run_id=run_ref.run_id,
                before_status=run_ref.status,
                session_factory=session_factory,
                settings=settings,
                object_storage=object_storage,
                redis_client=redis_client,
                controls=controls,
                metrics=metrics,
            ),
        )
        all_results = [*interrupt_results, *run_results]
        result = AgentRunQueueWorkerResult(
            scanned=len(stale_run_refs) + len(run_refs),
            processed=sum(1 for item in all_results if item.status_changed),
            terminal=sum(1 for item in all_results if item.terminal),
            interrupted=sum(1 for item in all_results if item.interrupted),
        )

        totals["cycles"] += 1
        totals["scanned"] += result.scanned
        totals["processed"] += result.processed
        totals["terminal"] += result.terminal
        totals["interrupted"] = int(totals.get("interrupted", 0)) + result.interrupted
        if once or (max_cycles is not None and totals["cycles"] >= max_cycles):
            return totals
        if result.scanned == 0 or result.processed == 0:
            await _wait_for_next_agent_run_signal(
                controls=controls,
                idle_seconds=settings.agent_runtime_worker_idle_seconds,
                stop_event=stop_event,
            )


async def _supervise_fact_worker_lane(
    *,
    lane_factory: Callable[[], Awaitable[dict[str, Any]]],
    restart: bool,
    restart_delay_seconds: float,
    stop_event: asyncio.Event | None,
) -> dict[str, Any]:
    while True:
        try:
            return await lane_factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_agent_runtime_event(
                "agent.fact.worker_lane_failed",
                outcome="exception",
                error_type=type(exc).__name__,
            )
            if not restart or (stop_event is not None and stop_event.is_set()):
                return _failed_fact_lane_totals(status="failed")
            await _wait_before_fact_lane_restart(
                delay_seconds=restart_delay_seconds,
                stop_event=stop_event,
            )
            if stop_event is not None and stop_event.is_set():
                return _failed_fact_lane_totals(status="stopping")


async def _wait_before_fact_lane_restart(
    *,
    delay_seconds: float,
    stop_event: asyncio.Event | None,
) -> None:
    delay = max(delay_seconds, 0.1)
    if stop_event is None:
        await asyncio.sleep(delay)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return


def _failed_fact_lane_totals(*, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "cycles": 0,
        "scanned": 0,
        "processed": 0,
        "completed": 0,
        "dead_lettered": 0,
    }


def _with_metrics(totals: dict[str, Any], metrics: RequestMetrics) -> dict[str, Any]:
    return {**totals, "metrics": metrics.snapshot()}


async def _list_runnable_run_refs(
    *,
    session_factory: Any,
    batch_limit: int,
) -> list[RunnableAgentRunRef]:
    if batch_limit <= 0:
        return []
    async with session_factory() as session:
        repository = AgentRuntimeRepository(session)
        runs = await repository.list_runnable_runs(limit=batch_limit)
        return [RunnableAgentRunRef(run_id=run.id, status=run.status) for run in runs]


async def _list_stale_running_run_refs(
    *,
    session_factory: Any,
    batch_limit: int,
    interrupt_running_older_than_seconds: int | None,
) -> list[RunnableAgentRunRef]:
    if batch_limit <= 0:
        return []
    async with session_factory() as session:
        repository = AgentRuntimeRepository(session)
        runs = await repository.list_stale_running_runs(
            cutoff=_interrupt_running_before(interrupt_running_older_than_seconds),
            limit=batch_limit,
        )
        return [RunnableAgentRunRef(run_id=run.id, status=run.status) for run in runs]


async def _process_with_concurrency(
    *,
    items: Sequence[ItemT],
    concurrency: int,
    processor: Callable[[ItemT], Awaitable[AgentRunProcessResult]],
) -> list[AgentRunProcessResult]:
    if not items:
        return []
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(item: ItemT) -> AgentRunProcessResult:
        async with semaphore:
            try:
                return await processor(item)
            except Exception as exc:
                log_agent_runtime_event(
                    "agent.run.worker_item_failed",
                    outcome="exception",
                    error_type=type(exc).__name__,
                )
                return AgentRunProcessResult(status_changed=False, terminal=False)

    return list(await asyncio.gather(*(guarded(item) for item in items)))


async def _run_fact_worker_lane(
    *,
    worker: AgentFactExtractionWorker,
    settings: Settings,
    once: bool,
    max_cycles: int | None,
    stop_event: asyncio.Event | None,
    session_factory: Any,
    controls: AgentRunControls,
) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "status": "ok",
        "cycles": 0,
        "scanned": 0,
        "processed": 0,
        "completed": 0,
        "dead_lettered": 0,
    }
    while True:
        if stop_event is not None and stop_event.is_set():
            totals["status"] = "stopping"
            return totals
        try:
            claims = await _claim_due_fact_extractions(
                session_factory=session_factory,
                batch_limit=settings.agent_fact_worker_batch_limit,
                lease_seconds=settings.agent_fact_worker_lease_seconds,
            )
            results = await _process_fact_claims(
                claims=claims,
                concurrency=settings.agent_fact_worker_concurrency,
                worker=worker,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_agent_runtime_event(
                "agent.fact.worker_cycle_failed",
                outcome="exception",
                error_type=type(exc).__name__,
            )
            claims = []
            results = []
            totals["status"] = "degraded"

        totals["cycles"] += 1
        totals["scanned"] += len(claims)
        totals["processed"] += sum(1 for result in results if result.status != "stale_lease")
        totals["completed"] += sum(1 for result in results if result.status == "completed")
        totals["dead_lettered"] += sum(1 for result in results if result.status == "dead_lettered")
        if once or (max_cycles is not None and totals["cycles"] >= max_cycles):
            return totals
        if not claims:
            try:
                await _wait_for_next_fact_signal(
                    controls=controls,
                    idle_seconds=settings.agent_fact_worker_idle_seconds,
                    stop_event=stop_event,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                totals["status"] = "degraded"
                log_agent_runtime_event(
                    "agent.fact.worker_wait_failed",
                    outcome="exception",
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(max(settings.agent_fact_worker_idle_seconds, 0.1))


async def _claim_due_fact_extractions(
    *,
    session_factory: Any,
    batch_limit: int,
    lease_seconds: int,
) -> list[FactExtractionJobClaim]:
    if batch_limit <= 0:
        return []
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        repository = AgentFactRepository(session)
        claims: list[FactExtractionJobClaim] = await repository.claim_due_extractions(
            now=now,
            locked_until=now + timedelta(seconds=lease_seconds),
            limit=batch_limit,
        )
        await session.commit()
        return claims


async def _expire_due_fact_candidates(*, session_factory: Any, batch_limit: int) -> int:
    if batch_limit <= 0:
        return 0
    async with session_factory() as session:
        repository = AgentFactRepository(session)
        expired = await repository.expire_due_candidates(
            now=datetime.now(timezone.utc),
            limit=batch_limit,
        )
        audit_service = AuditService(repository=AuditRepository(session))
        for fact in expired:
            await audit_service.record(
                actor_user_id=fact.owner_user_id,
                actor_type="service",
                actor_service="agent-fact-worker",
                action="agent.fact.candidate.expired",
                resource_type="agent_user_fact",
                resource_id=str(fact.id),
                details={
                    "fact_key": fact.fact_key,
                    "fact_kind": fact.fact_kind,
                    "deletion_reason": "expired",
                },
            )
        await session.commit()
        return len(expired)


async def _process_fact_claims(
    *,
    claims: Sequence[FactExtractionJobClaim],
    concurrency: int,
    worker: AgentFactExtractionWorker,
) -> list[FactExtractionProcessResult]:
    if not claims:
        return []
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(claim: FactExtractionJobClaim) -> FactExtractionProcessResult:
        async with semaphore:
            try:
                return await worker.process(claim)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_agent_runtime_event(
                    "agent.fact.worker_item_failed",
                    outcome="exception",
                    error_type=type(exc).__name__,
                )
                return FactExtractionProcessResult(status="worker_error")

    return list(await asyncio.gather(*(guarded(claim) for claim in claims)))


async def _wait_for_next_agent_run_signal(
    *,
    controls: AgentRunControls,
    idle_seconds: float,
    stop_event: asyncio.Event | None,
) -> None:
    if idle_seconds <= 0:
        return
    if stop_event is None:
        await controls.wait_for_run_queue_signal(timeout_seconds=idle_seconds)
        return
    if stop_event.is_set():
        return
    signal_task = asyncio.create_task(controls.wait_for_run_queue_signal(timeout_seconds=idle_seconds))
    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait({signal_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    if signal_task in done:
        await signal_task


async def _wait_for_next_fact_signal(
    *,
    controls: AgentRunControls,
    idle_seconds: float,
    stop_event: asyncio.Event | None,
) -> None:
    if idle_seconds <= 0:
        return
    if stop_event is None:
        await controls.wait_for_fact_queue_signal(timeout_seconds=idle_seconds)
        return
    if stop_event.is_set():
        return
    signal_task = asyncio.create_task(controls.wait_for_fact_queue_signal(timeout_seconds=idle_seconds))
    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait({signal_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    if signal_task in done:
        await signal_task


async def _process_agent_run(
    *,
    run_id: UUID,
    before_status: str,
    session_factory: Any,
    settings: Settings,
    object_storage: Any,
    redis_client: Any,
    controls: AgentRunControls,
    metrics: RequestMetrics,
) -> AgentRunProcessResult:
    started_at = perf_counter()
    try:
        return await _execute_agent_run(
            run_id=run_id,
            before_status=before_status,
            session_factory=session_factory,
            settings=settings,
            object_storage=object_storage,
            redis_client=redis_client,
            controls=controls,
            metrics=metrics,
        )
    except Exception as exc:
        _log_agent_run_process_timing(
            run_id=run_id,
            before_status=before_status,
            after_status=before_status,
            started_at=started_at,
            outcome="exception",
            error_type=type(exc).__name__,
        )
        return await _fail_run_after_worker_error(
            run_id=run_id,
            before_status=before_status,
            session_factory=session_factory,
            redis_client=redis_client,
            controls=controls,
            error_type=type(exc).__name__,
        )


async def _execute_agent_run(
    *,
    run_id: UUID,
    before_status: str,
    session_factory: Any,
    settings: Settings,
    object_storage: Any,
    redis_client: Any,
    controls: AgentRunControls,
    metrics: RequestMetrics,
) -> AgentRunProcessResult:
    started_at = perf_counter()
    async with session_factory() as session:
        repository = AgentRuntimeRepository(session)
        fact_repository = AgentFactRepository(session)
        audit_repository = AuditRepository(session)
        memory_service = AgentMemoryService(repository=AgentMemoryRepository(session))
        fact_service = AgentFactService(
            repository=fact_repository,
            audit_service=AuditService(repository=audit_repository),
            memory_consent_reader=memory_service,
            extraction_enabled=False,
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
        notifications_service = NotificationsService(
            repository=NotificationsRepository(session),
            audit_service=AuditService(repository=audit_repository),
            idempotency_service=IdempotencyService(repository=audit_repository),
        )
        support_service = SupportTicketsService(
            repository=SupportTicketsRepository(session),
            audit_service=AuditService(repository=audit_repository),
            idempotency_service=IdempotencyService(repository=audit_repository),
        )
        action_executor = AgentActionExecutor(
            repository=repository,
            handlers=build_agent_action_handlers(
                notifications_service=notifications_service,
                plans_service=plans_service,
                records_service=records_service,
                support_service=support_service,
            ),
        )
        agent_runtime_service = AgentRuntimeService(
            repository=repository,
            idempotency_service=IdempotencyService(repository=audit_repository),
            action_executor=action_executor,
            controls=controls,
            fact_service=fact_service,
            memory_service=memory_service,
        )
        tool_registry = default_tool_registry()
        transient_stream = AgentTransientStream(redis_client)
        event_sink = AgentEventSink(
            repository=repository,
            controls=controls,
            after_append=session.commit,
            transient_stream=transient_stream,
        )
        tool_handlers = build_default_tool_handlers(
            profile_service=profile_service,
            records_service=records_service,
            plans_service=plans_service,
            diary_service=diary_service,
            devices_service=devices_service,
            asset_service=ProductAssetService(),
            agent_runtime_service=agent_runtime_service,
            object_storage=object_storage,
        )
        tool_executor = ToolExecutor(
            registry=tool_registry,
            repository=repository,
            event_sink=event_sink,
            metrics=metrics,
            object_storage=object_storage,
            max_inline_output_bytes=settings.agent_runtime_max_inline_payload_bytes,
            handlers=tool_handlers,
            transient_stream=transient_stream,
        )
        sdk_runner = create_agent_model_runner(settings=settings, metrics=metrics)
        quick_reply_runner = create_agent_model_runner(
            settings=settings,
            metrics=metrics,
            trace_enabled=False,
            model=settings.agent_quick_reply_model or None,
            max_turns=1,
            timeout_seconds=settings.agent_quick_reply_timeout_seconds,
            reasoning_effort="none",
            metrics_node_name="quick_reply_finalizer",
        )
        runtime_executor = AgentRuntimeExecutor(
            repository=repository,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            event_sink=event_sink,
            memory_service=memory_service,
            business_facts_projector=BusinessFactsProjector(handlers=tool_handlers),
            transient_stream=transient_stream,
            quick_reply_finalizer=QuickReplyFinalizer(sdk_runner=quick_reply_runner),
            working_context_store=RedisAgentWorkingContextStore(redis_client),
            fact_service=fact_service,
            sdk_runner=sdk_runner,
            object_storage=object_storage,
            max_inline_artifact_payload_bytes=settings.agent_runtime_max_inline_payload_bytes,
        )
        worker = AgentRunWorker(
            repository=repository,
            controls=controls,
            handler=runtime_executor,
            action_executor=action_executor,
            after_event_append=session.commit,
            transient_stream=transient_stream,
        )
        after_run = await worker.run_once(run_id=run_id)
        await session.commit()
        after_status = after_run.status if after_run is not None else before_status
        _log_agent_run_process_timing(
            run_id=run_id,
            before_status=before_status,
            after_status=after_status,
            started_at=started_at,
            outcome="completed",
            queue_wait_ms=_duration_between_ms(
                after_run.created_at if after_run is not None else None,
                after_run.started_at if after_run is not None else None,
            ),
        )
        return AgentRunProcessResult(
            status_changed=bool(after_run is not None and after_status != before_status),
            terminal=after_status in TERMINAL_RUN_STATUSES,
        )


async def _fail_run_after_worker_error(
    *,
    run_id: UUID,
    before_status: str,
    session_factory: Any,
    redis_client: Any,
    controls: AgentRunControls,
    error_type: str,
) -> AgentRunProcessResult:
    async with session_factory() as session:
        repository = AgentRuntimeRepository(session)
        run = await repository.get_run(run_id=run_id)
        if run is None:
            return AgentRunProcessResult(status_changed=False, terminal=False)
        if run.status in TERMINAL_RUN_STATUSES:
            return AgentRunProcessResult(status_changed=run.status != before_status, terminal=True)

        payload = {"code": "worker_process_error"}
        transient_stream = AgentTransientStream(redis_client)
        try:
            await transient_stream.publish_application_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="run.failed",
                payload=payload,
                dedupe_key=f"{run.id}:run.failed",
                optimistic=True,
                durable=False,
            )
        except Exception:
            log_agent_runtime_event(
                "agent.run.worker_failure_event_publish_failed",
                run_id=str(run.id),
                outcome="exception",
            )

        completed_at = datetime.now(timezone.utc)
        failed = await repository.mark_run_failed(
            run=run,
            completed_at=completed_at,
            error_code="worker_process_error",
            error_details={"exception_type": error_type},
        )
        event = await repository.append_event(
            thread_id=failed.thread_id,
            run_id=failed.id,
            event_type="run.failed",
            payload=payload,
        )
        await session.commit()
        try:
            await controls.set_stream_cursor(run_id=failed.id, sequence=event.sequence)
            await controls.clear_active_run(thread_id=failed.thread_id, run_id=failed.id)
            await controls.clear_cancel(run_id=failed.id)
        except Exception:
            log_agent_runtime_event(
                "agent.run.worker_failure_control_cleanup_failed",
                run_id=str(failed.id),
                outcome="exception",
            )
        return AgentRunProcessResult(status_changed=failed.status != before_status, terminal=True)


async def _interrupt_agent_run(
    *,
    run_id: UUID,
    before_status: str,
    session_factory: Any,
    redis_client: Any,
    controls: AgentRunControls,
) -> AgentRunProcessResult:
    started_at = perf_counter()
    async with session_factory() as session:
        repository = AgentRuntimeRepository(session)
        transient_stream = AgentTransientStream(redis_client)
        worker = AgentRunWorker(
            repository=repository,
            controls=controls,
            after_event_append=session.commit,
            transient_stream=transient_stream,
        )
        try:
            after_run = await worker.interrupt_running(run_id=run_id)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            _log_agent_run_process_timing(
                run_id=run_id,
                before_status=before_status,
                after_status=before_status,
                started_at=started_at,
                outcome="exception",
                error_type=type(exc).__name__,
                event_name="agent.run.worker_interrupt",
            )
            raise
        after_status = after_run.status if after_run is not None else before_status
        interrupted = bool(before_status == "running" and after_status == "failed")
        _log_agent_run_process_timing(
            run_id=run_id,
            before_status=before_status,
            after_status=after_status,
            started_at=started_at,
            outcome="completed",
            event_name="agent.run.worker_interrupt",
        )
        return AgentRunProcessResult(
            status_changed=bool(after_run is not None and after_status != before_status),
            terminal=after_status in TERMINAL_RUN_STATUSES,
            interrupted=interrupted,
        )


def _interrupt_running_before(interrupt_running_older_than_seconds: int | None) -> datetime | None:
    if interrupt_running_older_than_seconds is None:
        return None
    return datetime.now(timezone.utc) - timedelta(seconds=interrupt_running_older_than_seconds)


def _log_agent_run_process_timing(
    *,
    run_id: UUID,
    before_status: str,
    after_status: str,
    started_at: float,
    outcome: str,
    queue_wait_ms: float | None = None,
    error_type: str = "",
    event_name: str = "agent.run.worker_execute",
) -> None:
    log_agent_runtime_event(
        event_name,
        run_id=str(run_id),
        before_status=before_status,
        after_status=after_status,
        outcome=outcome,
        terminal=after_status in TERMINAL_RUN_STATUSES,
        queue_wait_ms=queue_wait_ms,
        duration_ms=_elapsed_ms(started_at),
        error_type=error_type,
    )


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)


def _duration_between_ms(started_at: datetime | None, ended_at: datetime | None) -> float | None:
    if started_at is None or ended_at is None:
        return None
    if started_at.tzinfo is None and ended_at.tzinfo is not None:
        started_at = started_at.replace(tzinfo=ended_at.tzinfo)
    elif started_at.tzinfo is not None and ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=started_at.tzinfo)
    return round((ended_at - started_at).total_seconds() * 1000, 3)


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
