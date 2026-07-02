from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from production_backend.app.core.settings import Settings
from production_backend.app.infrastructure.db.session import create_db_engine, create_session_factory
from production_backend.app.infrastructure.object_storage.factory import create_object_storage
from production_backend.app.modules.agent_runtime.repository import AgentRuntimeRepository
from production_backend.app.modules.audit import AuditService, IdempotencyService, OutboxService
from production_backend.app.modules.audit.repository import AuditRepository, OutboxRepository
from production_backend.app.modules.support import SUPPORT_TICKET_CREATE_ACTION, SupportTicketCreateActionHandler, SupportTicketsService
from production_backend.app.modules.support.repository import SupportTicketsRepository
from production_backend.app.workers.outbox import OutboxWorker
from production_backend.app.workers.registry import build_outbox_handlers


async def run_outbox_worker(
    *,
    settings: Settings | None = None,
    once: bool = False,
    max_cycles: int | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or Settings.from_env()
    resolved_settings.validate_for_startup()
    if not resolved_settings.outbox_worker_enabled:
        return {"status": "disabled", "cycles": 0, "processed": 0}

    db_engine = create_db_engine(resolved_settings)
    session_factory = create_session_factory(db_engine)
    object_storage = create_object_storage(resolved_settings)
    totals = {"status": "ok", "cycles": 0, "processed": 0}
    try:
        while True:
            async with session_factory() as session:
                audit_repository = AuditRepository(session)
                support_service = SupportTicketsService(
                    repository=SupportTicketsRepository(session),
                    audit_service=AuditService(repository=audit_repository),
                    idempotency_service=IdempotencyService(repository=audit_repository),
                )
                worker = OutboxWorker(
                    service=OutboxService(repository=OutboxRepository(session)),
                    handlers=build_outbox_handlers(
                        object_storage=object_storage,
                        agent_runtime_repository=AgentRuntimeRepository(session),
                        agent_action_handlers={
                            SUPPORT_TICKET_CREATE_ACTION: SupportTicketCreateActionHandler(service=support_service),
                        },
                    ),
                    lease_seconds=resolved_settings.outbox_worker_lease_seconds,
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
                return totals
            if not processed:
                await asyncio.sleep(resolved_settings.outbox_worker_idle_seconds)
    finally:
        await db_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the production outbox worker.")
    parser.add_argument("--once", action="store_true", help="Process one job and exit.")
    parser.add_argument("--max-cycles", type=int, default=None, help="Process at most this many cycles before exiting.")
    args = parser.parse_args()

    result = asyncio.run(run_outbox_worker(once=args.once, max_cycles=args.max_cycles))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
