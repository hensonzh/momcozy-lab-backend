from __future__ import annotations

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..auth import CurrentUser
from .schemas import OnboardingProfileInput, OnboardingStateOutput
from .service import OnboardingService


router = SurfaceAPIRouter(
    prefix="/onboarding/me",
    tags=["onboarding"],
    api_surface_metadata=api_surface("public_app_api", owner="onboarding", clients=["flutter"]),
)


def get_onboarding_service(session: AsyncSession = Depends(get_session)) -> OnboardingService:
    return OnboardingService(session)


@router.get("", response_model=OnboardingStateOutput)
async def read_onboarding(
    response: Response,
    current_user: CurrentUser = Depends(require_current_user),
    service: OnboardingService = Depends(get_onboarding_service),
) -> OnboardingStateOutput:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.read(current_user.user_id)


@router.put("/profile", response_model=OnboardingStateOutput)
async def confirm_onboarding(
    payload: OnboardingProfileInput,
    request: Request,
    response: Response,
    current_user: CurrentUser = Depends(require_current_user),
    service: OnboardingService = Depends(get_onboarding_service),
) -> OnboardingStateOutput:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.confirm(
        current_user.user_id,
        payload,
        str(getattr(request.state, "request_id", "") or ""),
    )
