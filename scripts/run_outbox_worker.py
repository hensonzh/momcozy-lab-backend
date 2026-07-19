from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from app.core.metrics import RequestMetrics
from app.core.settings import Settings
from app.infrastructure.db.session import create_db_engine, create_session_factory, run_after_commit_callbacks
from app.infrastructure.object_storage.factory import create_object_storage
from app.modules.audit import OutboxService
from app.modules.audit.repository import OutboxRepository
from app.workers.outbox import OutboxWorker
from app.workers.registry import build_outbox_handlers
from scripts.worker_runtime import install_stop_signal_handlers, sleep_until_stop


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
    metrics = RequestMetrics()
    totals: dict[str, Any] = {"status": "ok", "cycles": 0, "processed": 0}
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                totals["status"] = "stopping"
                return _with_metrics(totals, metrics)
            async with session_factory() as session:
                worker = OutboxWorker(
                    service=OutboxService(repository=OutboxRepository(session)),
                    handlers=build_outbox_handlers(object_storage=object_storage),
                    lease_seconds=resolved_settings.outbox_worker_lease_seconds,
                    metrics=metrics,
                )
                try:
                    processed = await worker.run_once()
                    await session.commit()
                    await run_after_commit_callbacks(session)
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
