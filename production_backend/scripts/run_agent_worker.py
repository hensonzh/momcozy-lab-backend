from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from production_backend.app.core.settings import Settings
from production_backend.app.infrastructure.db.session import create_db_engine, create_session_factory
from production_backend.app.infrastructure.redis.client import close_redis_client, create_redis_client
from production_backend.app.modules.agent_runtime.controls import AgentRunControls
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
from production_backend.app.modules.profiles.repository import ProfileRepository
from production_backend.app.modules.profiles.service import ProfileService
from production_backend.app.workers.agent_run import AgentRunQueueWorker, AgentRunWorker


async def run_agent_worker(
    *,
    settings: Settings | None = None,
    once: bool = False,
    max_cycles: int | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or Settings.from_env()
    resolved_settings.validate_for_startup()
    if not resolved_settings.agent_runtime_worker_enabled:
        return {"status": "disabled", "cycles": 0, "scanned": 0, "processed": 0, "terminal": 0}

    db_engine = create_db_engine(resolved_settings)
    session_factory = create_session_factory(db_engine)
    redis_client = create_redis_client(resolved_settings)
    controls = AgentRunControls(redis_client)
    totals: dict[str, Any] = {"status": "ok", "cycles": 0, "scanned": 0, "processed": 0, "terminal": 0}
    try:
        while True:
            async with session_factory() as session:
                repository = AgentRuntimeRepository(session)
                audit_repository = AuditRepository(session)
                agent_runtime_service = AgentRuntimeService(
                    repository=repository,
                    idempotency_service=IdempotencyService(repository=audit_repository),
                    outbox_service=OutboxService(repository=OutboxRepository(session)),
                    controls=controls,
                    safety_service=AgentSafetyService(repository=repository),
                )
                profile_service = ProfileService(
                    repository=ProfileRepository(session),
                    audit_service=AuditService(repository=audit_repository),
                    idempotency_service=IdempotencyService(repository=audit_repository),
                )
                tool_registry = default_tool_registry()
                tool_executor = ToolExecutor(
                    registry=tool_registry,
                    repository=repository,
                    handlers=build_default_tool_handlers(
                        profile_service=profile_service,
                        agent_runtime_service=agent_runtime_service,
                    ),
                )
                handler = AgentRuntimeExecutor(
                    repository=repository,
                    checkpoint_store=AgentGraphCheckpointStore(repository=repository),
                    state_store=AgentRuntimeStateStore(repository=repository),
                    tool_registry=tool_registry,
                    tool_executor=tool_executor,
                    sdk_runner=OpenAIAgentsSdkRunner(model=resolved_settings.openai_model),
                )
                worker = AgentRunQueueWorker(
                    repository=repository,
                    run_worker=AgentRunWorker(repository=repository, controls=controls, handler=handler),
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
                return totals
            if result.scanned == 0:
                await asyncio.sleep(resolved_settings.agent_runtime_worker_idle_seconds)
    finally:
        await close_redis_client(redis_client)
        await db_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the production agent runtime worker.")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
    parser.add_argument("--max-cycles", type=int, default=None, help="Process at most this many cycles before exiting.")
    args = parser.parse_args()

    result = asyncio.run(run_agent_worker(once=args.once, max_cycles=args.max_cycles))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
