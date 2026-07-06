from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import optional_idempotency_key, require_current_user
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .models import UserProfile
from .repository import ProfileRepository
from .schemas import (
    InfantProfileCreate,
    InfantProfileListResponse,
    InfantProfileRead,
    UserProfileRead,
    UserProfileUpdate,
)
from .service import ProfileService


router = APIRouter(prefix="/profile", tags=["profile"])


def get_profile_service(session: AsyncSession = Depends(get_session)) -> ProfileService:
    audit_repository = AuditRepository(session)
    return ProfileService(
        repository=ProfileRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )


@router.get("/me", response_model=UserProfileRead)
async def get_my_profile(
    current_user: CurrentUser = Depends(require_current_user),
    service: ProfileService = Depends(get_profile_service),
) -> UserProfileRead:
    profile = await service.get_user_profile(user_id=current_user.user_id)
    return _profile_read(profile, current_user.user_id)


@router.put("/me", response_model=UserProfileRead)
async def update_my_profile(
    payload: UserProfileUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: ProfileService = Depends(get_profile_service),
) -> UserProfileRead:
    values = payload.model_dump(exclude_unset=True)
    profile = await service.update_user_profile(
        user_id=current_user.user_id,
        values=values,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return _profile_read(profile, current_user.user_id)


@router.get("/infants", response_model=InfantProfileListResponse)
async def list_my_infants(
    current_user: CurrentUser = Depends(require_current_user),
    service: ProfileService = Depends(get_profile_service),
) -> InfantProfileListResponse:
    infants = await service.list_infants(owner_user_id=current_user.user_id)
    return InfantProfileListResponse(items=[InfantProfileRead.model_validate(infant) for infant in infants])


@router.post("/infants", response_model=InfantProfileRead, status_code=status.HTTP_201_CREATED)
async def create_my_infant(
    payload: InfantProfileCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: ProfileService = Depends(get_profile_service),
) -> InfantProfileRead:
    infant = await service.create_infant(
        owner_user_id=current_user.user_id,
        infant_name=payload.infant_name,
        sex=payload.sex or "",
        birth_date=payload.birth_date,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return InfantProfileRead.model_validate(infant)


def _profile_read(profile: UserProfile | None, user_id: UUID) -> UserProfileRead:
    if profile is None:
        return UserProfileRead(user_id=user_id)
    return UserProfileRead(
        user_id=user_id,
        display_name=profile.display_name or "",
        age=profile.age,
        delivery_date=profile.delivery_date,
        lactation_advice=profile.lactation_advice or "",
        feeding_advice=profile.feeding_advice or "",
        profile_onboarding_complete=bool(profile.profile_onboarding_completed_at or (profile.display_name and profile.age)),
        profile_onboarding_skipped=bool(profile.profile_onboarding_skipped_at),
    )
