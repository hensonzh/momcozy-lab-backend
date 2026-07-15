from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from production_backend.app.core.settings import Settings  # noqa: E402
from production_backend.app.infrastructure.db.session import create_db_engine, create_session_factory  # noqa: E402
from production_backend.app.infrastructure.redis.client import close_redis_client, create_redis_client  # noqa: E402
from production_backend.app.modules.agent_runtime.models import AgentRun  # noqa: E402
from production_backend.app.modules.agent_runtime.repository import AgentRuntimeRepository  # noqa: E402
from production_backend.app.modules.agent_runtime.run_lifecycle.controls import AgentRunControls  # noqa: E402


RECOVERY_ERROR_CODE = "runtime_interrupted"


async def recover_stuck_agent_runs(
    *,
    settings: Settings | None = None,
    older_than_seconds: int | None = None,
    limit: int = 20,
    apply: bool = False,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be positive")
    resolved_settings = settings or Settings.from_env()
    resolved_settings.validate_for_startup()
    threshold_seconds = older_than_seconds or resolved_settings.agent_runtime_interrupt_running_older_than_seconds
    cutoff = _stuck_run_cutoff(older_than_seconds=threshold_seconds)
    engine = create_db_engine(resolved_settings)
    session_factory = create_session_factory(engine)
    redis_client = create_redis_client(resolved_settings) if apply else None
    controls = AgentRunControls(redis_client) if redis_client is not None else None

    try:
        async with session_factory() as session:
            repository = AgentRuntimeRepository(session)
            runs = await _list_stuck_running_runs(session=session, cutoff=cutoff, limit=limit)
            recovered: list[dict[str, object]] = []
            for run in runs:
                recovered.append(
                    {
                        "run_id": str(run.id),
                        "thread_id": str(run.thread_id),
                        "status": run.status,
                        "started_at": run.started_at.isoformat() if run.started_at else None,
                    }
                )
                if not apply:
                    continue
                failed = await repository.mark_run_failed(
                    run=run,
                    completed_at=_utcnow(),
                    error_code=RECOVERY_ERROR_CODE,
                    error_details={"interrupted_by": "recover_stuck_agent_runs.py"},
                )
                await repository.append_event(
                    thread_id=failed.thread_id,
                    run_id=failed.id,
                    event_type="run.failed",
                    payload={"code": RECOVERY_ERROR_CODE, "interrupted": True},
                )
                if controls is not None:
                    await controls.clear_active_run(thread_id=failed.thread_id, run_id=failed.id)
                    await controls.clear_cancel(run_id=failed.id)
            if apply:
                await session.commit()
            return {
                "status": "ok",
                "mode": "apply" if apply else "dry_run",
                "cutoff": cutoff.isoformat(),
                "matched": len(runs),
                "recovered": len(runs) if apply else 0,
                "runs": recovered,
            }
    finally:
        if redis_client is not None:
            await close_redis_client(redis_client)
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover agent runs stuck in running status by marking them failed.")
    parser.add_argument("--older-than-seconds", type=int, default=None, help="Only recover running runs older than this threshold.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of stuck runs to inspect or recover.")
    parser.add_argument("--apply", action="store_true", help="Mutate state. Without this flag the script is a dry run.")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                recover_stuck_agent_runs(
                    older_than_seconds=args.older_than_seconds,
                    limit=args.limit,
                    apply=args.apply,
                )
            ),
            indent=2,
            sort_keys=True,
        )
    )


async def _list_stuck_running_runs(*, session: AsyncSession, cutoff: datetime, limit: int) -> list[AgentRun]:
    statement = (
        select(AgentRun)
        .where(
            AgentRun.status == "running",
            or_(AgentRun.started_at.is_(None), AgentRun.started_at <= cutoff),
        )
        .order_by(AgentRun.started_at.asc().nullsfirst(), AgentRun.created_at.asc(), AgentRun.id.asc())
        .limit(limit)
    )
    result = await session.scalars(statement)
    return list(result.all())


def _stuck_run_cutoff(*, older_than_seconds: int) -> datetime:
    if older_than_seconds < 1:
        raise ValueError("older_than_seconds must be positive")
    return _utcnow() - timedelta(seconds=older_than_seconds)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    main()
