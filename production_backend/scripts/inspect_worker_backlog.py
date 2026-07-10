from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from production_backend.app.core.settings import Settings  # noqa: E402
from production_backend.app.infrastructure.db.session import create_db_engine, create_session_factory  # noqa: E402
from production_backend.app.modules.agent_runtime.models import AgentRun  # noqa: E402
from production_backend.app.modules.audit.models import OutboxJob  # noqa: E402


async def inspect_worker_backlog(*, settings: Settings | None = None) -> dict[str, Any]:
    resolved_settings = settings or Settings.from_env()
    resolved_settings.validate_for_startup()
    engine = create_db_engine(resolved_settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            now = _utcnow()
            stale_cutoff = now - timedelta(seconds=resolved_settings.agent_runtime_interrupt_running_older_than_seconds)
            return {
                "status": "ok",
                "outbox": {
                    "by_status": await _outbox_by_status(session),
                    "by_type_status": await _outbox_by_type_status(session),
                    "due_or_expired_locked": await _due_or_expired_locked_outbox_count(session, now=now),
                },
                "agent_runs": {
                    "by_status": await _agent_runs_by_status(session),
                    "stale_running": await _stale_running_agent_run_count(session, cutoff=stale_cutoff),
                    "stale_running_cutoff": stale_cutoff.isoformat(),
                },
            }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect durable worker backlog without mutating state.")
    parser.parse_args()
    print(json.dumps(asyncio.run(inspect_worker_backlog()), indent=2, sort_keys=True))


async def _outbox_by_status(session: AsyncSession) -> dict[str, int]:
    statement = select(OutboxJob.status, func.count()).group_by(OutboxJob.status).order_by(OutboxJob.status)
    rows = (await session.execute(statement)).all()
    return _status_counts_from_rows((str(status), int(count)) for status, count in rows)


async def _outbox_by_type_status(session: AsyncSession) -> list[dict[str, object]]:
    statement = (
        select(OutboxJob.job_type, OutboxJob.status, func.count())
        .group_by(OutboxJob.job_type, OutboxJob.status)
        .order_by(OutboxJob.job_type, OutboxJob.status)
    )
    rows = (await session.execute(statement)).all()
    return [
        {
            "job_type": str(job_type),
            "status": str(status),
            "count": int(count),
        }
        for job_type, status, count in rows
    ]


async def _due_or_expired_locked_outbox_count(session: AsyncSession, *, now: datetime) -> int:
    statement = select(func.count()).select_from(OutboxJob).where(
        or_(
            and_(OutboxJob.status == "queued", OutboxJob.next_attempt_at <= now),
            and_(OutboxJob.status == "locked", OutboxJob.locked_until <= now),
        )
    )
    count = await session.scalar(statement)
    return int(count or 0)


async def _agent_runs_by_status(session: AsyncSession) -> dict[str, int]:
    statement = select(AgentRun.status, func.count()).group_by(AgentRun.status).order_by(AgentRun.status)
    rows = (await session.execute(statement)).all()
    return _status_counts_from_rows((str(status), int(count)) for status, count in rows)


async def _stale_running_agent_run_count(session: AsyncSession, *, cutoff: datetime) -> int:
    statement = select(func.count()).select_from(AgentRun).where(
        AgentRun.status == "running",
        or_(AgentRun.started_at.is_(None), AgentRun.started_at <= cutoff),
    )
    count = await session.scalar(statement)
    return int(count or 0)


def _status_counts_from_rows(rows: Iterable[tuple[str, int]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for status, count in rows:
        counts[str(status)] = int(count)
    return counts


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    main()
