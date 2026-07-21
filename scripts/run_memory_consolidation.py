from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.metrics import RequestMetrics
from app.core.settings import Settings
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.agent_runtime.context.memory import (
    AgentMemoryExtractor,
    AgentMemoryRepository,
    AgentMemoryService,
    MemoryConsolidationApplyResult,
    MemoryConsolidationBatch,
    MemoryConsolidationPreparation,
    apply_memory_consolidation,
    fail_memory_consolidation,
    prepare_memory_consolidation,
)
from app.agent_runtime.context.memory.consolidation import MemoryCandidate
from app.agent_runtime.providers import create_agent_model_runner
from scripts.worker_runtime import install_stop_signal_handlers, sleep_until_stop


LOGGER = logging.getLogger("production_backend.agent_memory_consolidation")


async def run_memory_consolidation_once(
    *,
    settings: Settings | None = None,
    source_date: date | None = None,
    now: datetime | None = None,
    session_factory: Any | None = None,
    repository_factory: Callable[[Any], AgentMemoryRepository] = AgentMemoryRepository,
    extractor: AgentMemoryExtractor | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or Settings.from_env()
    if not resolved_settings.agent_memory_consolidation_enabled:
        return _empty_result(status="disabled", source_date=source_date)
    resolved_settings.validate_for_startup()

    resolved_now = _aware_utc(now or datetime.now(timezone.utc))
    resolved_source_date = source_date or _previous_local_date(
        now=resolved_now,
        timezone_name=resolved_settings.agent_memory_consolidation_timezone,
    )
    range_start, range_end = local_day_utc_bounds(
        source_date=resolved_source_date,
        timezone_name=resolved_settings.agent_memory_consolidation_timezone,
    )
    owned_engine = None
    if session_factory is None:
        owned_engine = create_db_engine(resolved_settings)
        session_factory = create_session_factory(owned_engine)

    metrics = RequestMetrics()
    resolved_extractor = extractor or AgentMemoryExtractor(
        model_runner=create_agent_model_runner(
            settings=resolved_settings,
            metrics=metrics,
            model=resolved_settings.agent_memory_consolidation_model,
            max_turns=1,
            timeout_seconds=resolved_settings.agent_memory_consolidation_timeout_seconds,
            reasoning_effort="none",
            metrics_node_name="nightly_memory_consolidation",
        )
    )
    totals = _empty_result(status="ok", source_date=resolved_source_date)
    try:
        owner_user_ids = await _list_owner_user_ids(
            session_factory=session_factory,
            repository_factory=repository_factory,
            range_start=range_start,
            range_end=range_end,
            limit=resolved_settings.agent_memory_consolidation_max_users,
        )
        totals["owners"] = len(owner_user_ids)
        for owner_user_id in owner_user_ids:
            batch = None
            try:
                preparation = await _prepare_owner(
                    session_factory=session_factory,
                    repository_factory=repository_factory,
                    owner_user_id=owner_user_id,
                    source_date=resolved_source_date,
                    range_start=range_start,
                    range_end=range_end,
                    extractor_version=resolved_settings.agent_memory_consolidation_extractor_version,
                    message_limit=resolved_settings.agent_memory_consolidation_message_limit,
                )
                batch = preparation.batch
                if preparation.status != "ready" or batch is None:
                    totals["skipped"] += 1
                    continue

                candidates = await resolved_extractor.extract(batch=batch)
                result = await _apply_owner(
                    session_factory=session_factory,
                    repository_factory=repository_factory,
                    batch=batch,
                    candidates=candidates,
                )
                totals["processed"] += 1
                if result.status == "completed":
                    totals["completed"] += 1
                    totals["upserted"] += result.upserted_count
                    totals["archived"] += result.archived_count
                    totals["rejected"] += result.rejected_count
                else:
                    totals["skipped"] += 1
            except Exception as exc:
                totals["failed"] += 1
                LOGGER.exception(
                    "Nightly memory consolidation failed for owner.",
                    extra={"owner_user_id": str(owner_user_id), "source_date": resolved_source_date.isoformat()},
                )
                if batch is not None:
                    await _record_failure(
                        session_factory=session_factory,
                        repository_factory=repository_factory,
                        run_id=batch.run_id,
                        error_code=type(exc).__name__,
                    )
        totals["metrics"] = metrics.snapshot()
        return totals
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()


async def run_memory_consolidation_worker(
    *,
    settings: Settings | None = None,
    once: bool = False,
    source_date: date | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or Settings.from_env()
    if once:
        return await run_memory_consolidation_once(settings=resolved_settings, source_date=source_date)
    if not resolved_settings.agent_memory_consolidation_enabled:
        return _empty_result(status="disabled", source_date=source_date)

    last_result = await run_memory_consolidation_once(settings=resolved_settings)
    while stop_event is None or not stop_event.is_set():
        delay = seconds_until_next_run(
            now=datetime.now(timezone.utc),
            timezone_name=resolved_settings.agent_memory_consolidation_timezone,
            schedule_hour=resolved_settings.agent_memory_consolidation_hour,
        )
        await sleep_until_stop(seconds=delay, stop_event=stop_event)
        if stop_event is not None and stop_event.is_set():
            break
        last_result = await run_memory_consolidation_once(settings=resolved_settings)
    return {**last_result, "status": "stopping"}


def local_day_utc_bounds(*, source_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    local_start = datetime.combine(source_date, time.min, tzinfo=zone)
    local_end = datetime.combine(source_date + timedelta(days=1), time.min, tzinfo=zone)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def seconds_until_next_run(*, now: datetime, timezone_name: str, schedule_hour: int) -> float:
    zone = ZoneInfo(timezone_name)
    local_now = _aware_utc(now).astimezone(zone)
    next_run = datetime.combine(local_now.date(), time(hour=schedule_hour), tzinfo=zone)
    if next_run <= local_now:
        next_run = datetime.combine(local_now.date() + timedelta(days=1), time(hour=schedule_hour), tzinfo=zone)
    return max(0.0, (next_run.astimezone(timezone.utc) - local_now.astimezone(timezone.utc)).total_seconds())


async def _list_owner_user_ids(
    *,
    session_factory: Any,
    repository_factory: Callable[[Any], AgentMemoryRepository],
    range_start: datetime,
    range_end: datetime,
    limit: int,
) -> list[Any]:
    async with session_factory() as session:
        repository = repository_factory(session)
        return await repository.list_conversation_owner_ids(
            range_start=range_start,
            range_end=range_end,
            limit=limit,
        )


async def _prepare_owner(
    *,
    session_factory: Any,
    repository_factory: Callable[[Any], AgentMemoryRepository],
    owner_user_id: Any,
    source_date: date,
    range_start: datetime,
    range_end: datetime,
    extractor_version: str,
    message_limit: int,
) -> MemoryConsolidationPreparation:
    async with session_factory() as session:
        repository = repository_factory(session)
        try:
            preparation = await prepare_memory_consolidation(
                repository=repository,
                owner_user_id=owner_user_id,
                source_date=source_date,
                range_start=range_start,
                range_end=range_end,
                extractor_version=extractor_version,
                message_limit=message_limit,
            )
            await session.commit()
            return preparation
        except Exception:
            await session.rollback()
            raise


async def _apply_owner(
    *,
    session_factory: Any,
    repository_factory: Callable[[Any], AgentMemoryRepository],
    batch: MemoryConsolidationBatch,
    candidates: list[MemoryCandidate],
) -> MemoryConsolidationApplyResult:
    async with session_factory() as session:
        repository = repository_factory(session)
        try:
            result = await apply_memory_consolidation(
                repository=repository,
                memory_service=AgentMemoryService(repository=repository),
                batch=batch,
                candidates=candidates,
            )
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


async def _record_failure(
    *,
    session_factory: Any,
    repository_factory: Callable[[Any], AgentMemoryRepository],
    run_id: Any,
    error_code: str,
) -> None:
    try:
        async with session_factory() as session:
            repository = repository_factory(session)
            await fail_memory_consolidation(
                repository=repository,
                run_id=run_id,
                error_code=error_code,
            )
            await session.commit()
    except Exception:
        LOGGER.exception("Failed to record nightly memory consolidation failure.", extra={"run_id": str(run_id)})


def _previous_local_date(*, now: datetime, timezone_name: str) -> date:
    return _aware_utc(now).astimezone(ZoneInfo(timezone_name)).date() - timedelta(days=1)


def _aware_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _empty_result(*, status: str, source_date: date | None) -> dict[str, Any]:
    return {
        "status": status,
        "source_date": source_date.isoformat() if source_date is not None else "",
        "owners": 0,
        "processed": 0,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "upserted": 0,
        "archived": 0,
        "rejected": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run nightly agent memory consolidation.")
    parser.add_argument("--once", action="store_true", help="Process one local source date and exit.")
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="Local source date in YYYY-MM-DD format.")
    args = parser.parse_args()
    asyncio.run(_run_from_cli(once=args.once, source_date=args.date))


async def _run_from_cli(*, once: bool, source_date: date | None) -> None:
    stop_event = asyncio.Event()
    install_stop_signal_handlers(stop_event)
    result = await run_memory_consolidation_worker(
        once=once,
        source_date=source_date,
        stop_event=stop_event,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
