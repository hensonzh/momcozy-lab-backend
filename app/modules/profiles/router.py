from __future__ import annotations

from typing import Any

from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import optional_idempotency_key, require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from ..records.repository import RecordsRepository
from ..records.service import RecordsService
from .lactation_context import (
    LactationContextService,
    MaternalLactationProfileView,
)
from .models import UserProfile
from .repository import ProfileRepository
from .schemas import (
    InfantProfileCreate,
    InfantProfileListResponse,
    InfantProfileRead,
    MaternalLactationProfileRead,
    MaternalLactationProfileUpdate,
    UserProfileRead,
    UserProfileUpdate,
)
from .service import ProfileService


router = SurfaceAPIRouter(
    prefix="/profile",
    tags=["profile"],
    api_surface_metadata=api_surface("public_app_api", owner="profiles", clients=["flutter"]),
)


def get_profile_service(session: AsyncSession = Depends(get_session)) -> ProfileService:
    audit_repository = AuditRepository(session)
    return ProfileService(
        repository=ProfileRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )


def get_lactation_context_service(
    session: AsyncSession = Depends(get_session),
) -> LactationContextService:
    audit_repository = AuditRepository(session)
    return LactationContextService(
        profile_repository=ProfileRepository(session),
        records_service=RecordsService(repository=RecordsRepository(session)),
        audit_service=AuditService(repository=audit_repository),
    )


@router.get("/me", response_model=UserProfileRead)
async def get_my_profile(
    current_user: CurrentUser = Depends(require_current_user),
    service: ProfileService = Depends(get_profile_service),
) -> UserProfileRead:
    profile = await service.get_user_profile(user_id=current_user.user_id)
    return _profile_read(profile)


@router.patch("/me", response_model=UserProfileRead)
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
    return _profile_read(profile)


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
        name=payload.name,
        sex_at_birth=payload.sex_at_birth,
        birth_date=payload.birth_date,
        birth_weight_kg=payload.birth_weight_kg,
        gestational_age_at_birth_days=payload.gestational_age_at_birth_days,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return InfantProfileRead.model_validate(infant)


@router.get("/lactation", response_model=MaternalLactationProfileRead)
async def get_my_maternal_lactation_profile(
    current_user: CurrentUser = Depends(require_current_user),
    service: LactationContextService = Depends(get_lactation_context_service),
) -> MaternalLactationProfileRead:
    profile, current_infants = await service.get_maternal_profile(owner_user_id=current_user.user_id)
    return _maternal_lactation_profile_read(
        profile=profile,
        current_infants=current_infants,
    )


@router.patch("/lactation", response_model=MaternalLactationProfileRead)
async def update_my_maternal_lactation_profile(
    payload: MaternalLactationProfileUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: LactationContextService = Depends(get_lactation_context_service),
) -> MaternalLactationProfileRead:
    profile, current_infants = await service.update_maternal_profile(
        owner_user_id=current_user.user_id,
        values=payload.model_dump(exclude_unset=True),
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return _maternal_lactation_profile_read(
        profile=profile,
        current_infants=current_infants,
    )


def _profile_read(profile: UserProfile | None) -> UserProfileRead:
    if profile is None:
        return UserProfileRead()
    return UserProfileRead(
        preferred_name=profile.preferred_name,
        age=profile.age,
        estimated_due_date=profile.estimated_due_date,
    )


def _maternal_lactation_profile_read(
    *,
    profile: MaternalLactationProfileView | None,
    current_infants: list[dict[str, Any]],
) -> MaternalLactationProfileRead:
    values: dict[str, Any] = {"current_infants": current_infants}
    if profile is None:
        return MaternalLactationProfileRead.model_validate(values)
    values.update(
        {
            "delivery_count": profile.delivery_count,
            "current_delivery_method": profile.current_delivery_method,
            "actual_delivery_date": profile.actual_delivery_date,
            "has_cesarean_history": profile.has_cesarean_history,
            "current_feeding_mode": profile.current_feeding_mode,
        }
    )
    return MaternalLactationProfileRead.model_validate(values)
