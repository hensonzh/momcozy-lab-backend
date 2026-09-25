from __future__ import annotations

from typing import Any, cast

from fastapi import Header, Request
from sqlalchemy import text

from ...core.errors import ApiError
from ...core.settings import Settings
from ..surface import SurfaceAPIRouter, api_surface
from ...modules.assets.router import router as assets_router
from ...modules.auth import authenticate_service_key
from ...modules.auth.router import router as auth_router
from ...modules.auth.workbench_router import router as workbench_auth_router
from ...modules.ibclc.router import router as workbench_router
from ...modules.devices.router import router as devices_router
from ...modules.files.router import router as files_router
from ...modules.files.agent_router import router as agent_files_router
from ...modules.files.model_asset_router import router as model_assets_router
from ...modules.invites.router import router as invites_router
from ...modules.notifications.router import router as notifications_router
from ...modules.care.router import router as care_router
from ...modules.appointments.router import router as appointments_router
from ...modules.consultations.router import router as consultations_router
from ...modules.consultations.room_router import router as consultation_rooms_router
from ...modules.documentation.router import router as documentation_router
from ...modules.reports.conversation_router import router as care_conversation_router
from ...modules.reports.router import router as care_reports_router
from ...modules.lactation.router import router as lactation_router
from ...modules.baby.router import router as baby_records_router
from ...modules.baby.profile_router import router as baby_profiles_router
from ...modules.plans.router import router as plans_router
from ...modules.profiles.agent_router import router as agent_profiles_router
from ...modules.profiles.router import router as profiles_router
from ...modules.profiles.me_router import router as me_experience_router
from ...modules.records.router import router as records_router
from ...modules.support.router import router as support_router
from ...modules.voice.router import router as voice_router
from ...modules.schedule.router import router as schedule_router

router = SurfaceAPIRouter(
    prefix="/v1",
    api_surface_metadata=api_surface("infra_probe_api", owner="platform", clients=["load-balancer", "monitoring"]),
)
router.include_router(assets_router)
router.include_router(auth_router)
router.include_router(workbench_auth_router)
router.include_router(workbench_router)
router.include_router(devices_router)
router.include_router(model_assets_router)
router.include_router(files_router)
router.include_router(agent_files_router)
router.include_router(invites_router)
router.include_router(notifications_router)
router.include_router(care_router)
router.include_router(appointments_router)
router.include_router(consultations_router)
router.include_router(consultation_rooms_router)
router.include_router(documentation_router)
router.include_router(care_conversation_router)
router.include_router(care_reports_router)
router.include_router(lactation_router)
router.include_router(baby_records_router)
router.include_router(baby_profiles_router)
router.include_router(plans_router)
router.include_router(agent_profiles_router)
router.include_router(profiles_router)
router.include_router(me_experience_router)
router.include_router(records_router)
router.include_router(support_router)
router.include_router(voice_router)
router.include_router(schedule_router)


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not (settings.is_deployed or settings.readiness_check_infrastructure):
        return {"status": "ok"}

    checks = {
        "database": await _check_database(request),
        "redis": await _check_redis(request),
    }
    return {"status": "ok", "checks": checks}


@router.get("/health/metrics")
async def metrics(request: Request, service_key: str | None = Header(default=None, alias="X-Service-Key")) -> dict[str, Any]:
    _authorize_metrics(request=request, service_key=service_key)
    return cast(dict[str, Any], request.app.state.request_metrics.snapshot())


def _authorize_metrics(*, request: Request, service_key: str | None) -> None:
    settings = cast(Settings, request.app.state.settings)
    if not (settings.is_deployed or settings.metrics_require_service_key):
        return
    if not service_key:
        raise ApiError(code="authentication_required", message="X-Service-Key is required.", status=401)
    authenticate_service_key(service_key, settings)


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
