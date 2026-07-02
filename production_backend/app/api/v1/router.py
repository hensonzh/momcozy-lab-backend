from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from ...core.errors import ApiError

router = APIRouter(prefix="/v1")


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not (settings.is_production or settings.readiness_check_infrastructure):
        return {"status": "ok"}

    checks = {
        "database": await _check_database(request),
        "redis": await _check_redis(request),
    }
    return {"status": "ok", "checks": checks}


async def _check_database(request: Request) -> str:
    try:
        async with request.app.state.db_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise ApiError(
            code="dependency_failed",
            message="Database readiness check failed.",
            status=503,
        ) from exc
    return "ok"


async def _check_redis(request: Request) -> str:
    try:
        await request.app.state.redis_client.ping()
    except Exception as exc:
        raise ApiError(
            code="dependency_failed",
            message="Redis readiness check failed.",
            status=503,
        ) from exc
    return "ok"
