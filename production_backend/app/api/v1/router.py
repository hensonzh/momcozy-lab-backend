from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Request
from sqlalchemy import text

from ...core.errors import ApiError
from ...modules.agent_runtime.router import router as agent_runtime_router
from ...modules.auth.router import router as auth_router
from ...modules.devices.router import router as devices_router
from ...modules.diary.router import router as diary_router
from ...modules.files.router import router as files_router
from ...modules.notifications.router import router as notifications_router
from ...modules.plans.router import router as plans_router
from ...modules.profiles.router import router as profiles_router
from ...modules.records.router import router as records_router
from ...modules.support.router import router as support_router

router = APIRouter(prefix="/v1")
router.include_router(agent_runtime_router)
router.include_router(auth_router)
router.include_router(devices_router)
router.include_router(diary_router)
router.include_router(files_router)
router.include_router(notifications_router)
router.include_router(plans_router)
router.include_router(profiles_router)
router.include_router(records_router)
router.include_router(support_router)


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


@router.get("/health/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    return cast(dict[str, Any], request.app.state.request_metrics.snapshot())


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
