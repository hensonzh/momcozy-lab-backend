from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.core.settings import Settings
from production_backend.app.infrastructure.db.session import create_db_engine, create_session_factory
from production_backend.app.infrastructure.object_storage.factory import create_object_storage
from production_backend.app.infrastructure.redis.client import close_redis_client, create_redis_client
from production_backend.app.modules.agent_runtime.event_stream.sink import AgentEventSink
from production_backend.app.modules.agent_runtime.event_stream.transient import AgentTransientStream
from production_backend.app.modules.agent_runtime.memory.service import AgentMemoryRepository, AgentMemoryService
from production_backend.app.modules.agent_runtime.memory.actions import AGENT_MEMORY_CREATE_ACTION, AgentMemoryCreateActionHandler
from production_backend.app.modules.agent_runtime.repository import AgentRuntimeRepository
from production_backend.app.modules.agent_runtime.run_lifecycle.controls import AgentRunControls
from production_backend.app.modules.audit import AuditService, IdempotencyService, OutboxService
from production_backend.app.modules.audit.repository import AuditRepository, OutboxRepository
from production_backend.app.modules.diary.agent_actions import DIARY_ENTRY_UPSERT_ACTION, DiaryEntryUpsertActionHandler
from production_backend.app.modules.diary.repository import DiaryRepository
from production_backend.app.modules.diary.service import DiaryService
from production_backend.app.modules.notifications import (
    MILK_REMINDER_CREATE_ACTION,
    MilkReminderCreateActionHandler,
    NotificationsService,
)
from production_backend.app.modules.notifications.repository import NotificationsRepository
from production_backend.app.modules.plans.agent_actions import (
    MILK_PLAN_CREATE_ACTION,
    PLAN_TASK_COMPLETE_ACTION,
    PLAN_TASK_CREATE_ACTION,
    PLAN_TASK_DELETE_ACTION,
    PLAN_TASK_UPDATE_ACTION,
    PLAN_DELETE_ACTION,
    PREGNANCY_PLAN_CREATE_ACTION,
    MilkPlanCreateActionHandler,
    PlanDeleteActionHandler,
    PlanTaskCompleteActionHandler,
    PlanTaskCreateActionHandler,
    PlanTaskDeleteActionHandler,
    PlanTaskUpdateActionHandler,
    PregnancyPlanCreateActionHandler,
)
from production_backend.app.modules.plans.repository import PlansRepository
from production_backend.app.modules.plans.service import PlansService
from production_backend.app.modules.records.agent_actions import (
    FEEDING_RECORD_CREATE_ACTION,
    FEEDING_RECORD_DELETE_ACTION,
    GROWTH_RECORD_CREATE_ACTION,
    GROWTH_RECORD_DELETE_ACTION,
    GROWTH_RECORD_UPDATE_ACTION,
    PUMPING_RECORD_CREATE_ACTION,
    PUMPING_RECORD_DELETE_ACTION,
    FeedingRecordCreateActionHandler,
    FeedingRecordDeleteActionHandler,
    GrowthRecordCreateActionHandler,
    GrowthRecordDeleteActionHandler,
    GrowthRecordUpdateActionHandler,
    PumpingRecordCreateActionHandler,
    PumpingRecordDeleteActionHandler,
)
from production_backend.app.modules.records.repository import RecordsRepository
from production_backend.app.modules.records.service import RecordsService
from production_backend.app.modules.support import SupportTicketsService
from production_backend.app.modules.support.agent_actions import SUPPORT_TICKET_CREATE_ACTION, SupportTicketCreateActionHandler
from production_backend.app.modules.support.repository import SupportTicketsRepository
from production_backend.app.workers.outbox import OutboxWorker
from production_backend.app.workers.registry import build_outbox_handlers
from production_backend.scripts.worker_runtime import install_stop_signal_handlers, sleep_until_stop


async def run_outbox_worker(
    *,
    settings: Settings | None = None,
    once: bool = False,
    max_cycles: int | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or Settings.from_env()
    resolved_settings.validate_for_startup()
    if not resolved_settings.outbox_worker_enabled:
        return {"status": "disabled", "cycles": 0, "processed": 0}

    db_engine = create_db_engine(resolved_settings)
    session_factory = create_session_factory(db_engine)
    object_storage = create_object_storage(resolved_settings)
    redis_client = create_redis_client(resolved_settings)
    controls = AgentRunControls(redis_client)
    transient_stream = AgentTransientStream(redis_client)
    metrics = RequestMetrics()
    totals: dict[str, Any] = {"status": "ok", "cycles": 0, "processed": 0}
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                totals["status"] = "stopping"
                return _with_metrics(totals, metrics)
            async with session_factory() as session:
                audit_repository = AuditRepository(session)
                support_service = SupportTicketsService(
                    repository=SupportTicketsRepository(session),
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
                notifications_service = NotificationsService(
                    repository=NotificationsRepository(session),
                    audit_service=AuditService(repository=audit_repository),
                    idempotency_service=IdempotencyService(repository=audit_repository),
                )
                diary_service = DiaryService(
                    repository=DiaryRepository(session),
                    audit_service=AuditService(repository=audit_repository),
                )
                agent_runtime_repository = AgentRuntimeRepository(session)
                memory_service = AgentMemoryService(repository=AgentMemoryRepository(session))
                worker = OutboxWorker(
                    service=OutboxService(repository=OutboxRepository(session)),
                    handlers=build_outbox_handlers(
                        object_storage=object_storage,
                        agent_runtime_repository=agent_runtime_repository,
                        agent_event_sink=AgentEventSink(
                            repository=agent_runtime_repository,
                            controls=controls,
                            transient_stream=transient_stream,
                        ),
                        agent_action_handlers={
                            MILK_REMINDER_CREATE_ACTION: MilkReminderCreateActionHandler(service=notifications_service),
                            MILK_PLAN_CREATE_ACTION: MilkPlanCreateActionHandler(service=plans_service),
                            PREGNANCY_PLAN_CREATE_ACTION: PregnancyPlanCreateActionHandler(service=plans_service),
                            PLAN_TASK_CREATE_ACTION: PlanTaskCreateActionHandler(service=plans_service),
                            PLAN_TASK_COMPLETE_ACTION: PlanTaskCompleteActionHandler(service=plans_service),
                            PLAN_TASK_UPDATE_ACTION: PlanTaskUpdateActionHandler(service=plans_service),
                            PLAN_TASK_DELETE_ACTION: PlanTaskDeleteActionHandler(service=plans_service),
                            PLAN_DELETE_ACTION: PlanDeleteActionHandler(service=plans_service),
                            DIARY_ENTRY_UPSERT_ACTION: DiaryEntryUpsertActionHandler(service=diary_service),
                            AGENT_MEMORY_CREATE_ACTION: AgentMemoryCreateActionHandler(service=memory_service),
                            FEEDING_RECORD_CREATE_ACTION: FeedingRecordCreateActionHandler(service=records_service),
                            PUMPING_RECORD_CREATE_ACTION: PumpingRecordCreateActionHandler(service=records_service),
                            FEEDING_RECORD_DELETE_ACTION: FeedingRecordDeleteActionHandler(service=records_service),
                            PUMPING_RECORD_DELETE_ACTION: PumpingRecordDeleteActionHandler(service=records_service),
                            GROWTH_RECORD_CREATE_ACTION: GrowthRecordCreateActionHandler(service=records_service),
                            GROWTH_RECORD_UPDATE_ACTION: GrowthRecordUpdateActionHandler(service=records_service),
                            GROWTH_RECORD_DELETE_ACTION: GrowthRecordDeleteActionHandler(service=records_service),
                            SUPPORT_TICKET_CREATE_ACTION: SupportTicketCreateActionHandler(service=support_service),
                        },
                    ),
                    lease_seconds=resolved_settings.outbox_worker_lease_seconds,
                    metrics=metrics,
                )
                try:
                    processed = await worker.run_once()
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

            totals["cycles"] += 1
            if processed:
                totals["processed"] += 1
            if once or (max_cycles is not None and totals["cycles"] >= max_cycles):
                return _with_metrics(totals, metrics)
            if not processed:
                await sleep_until_stop(seconds=resolved_settings.outbox_worker_idle_seconds, stop_event=stop_event)
    finally:
        await close_redis_client(redis_client)
        await db_engine.dispose()


def _with_metrics(totals: dict[str, Any], metrics: RequestMetrics) -> dict[str, Any]:
    return {**totals, "metrics": metrics.snapshot()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the production outbox worker.")
    parser.add_argument("--once", action="store_true", help="Process one job and exit.")
    parser.add_argument("--max-cycles", type=int, default=None, help="Process at most this many cycles before exiting.")
    args = parser.parse_args()

    asyncio.run(_run_from_cli(once=args.once, max_cycles=args.max_cycles))


async def _run_from_cli(*, once: bool, max_cycles: int | None) -> None:
    stop_event = asyncio.Event()
    install_stop_signal_handlers(stop_event)
    result = await run_outbox_worker(once=once, max_cycles=max_cycles, stop_event=stop_event)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
