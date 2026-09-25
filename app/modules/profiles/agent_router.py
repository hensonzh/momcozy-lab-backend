from __future__ import annotations


from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError

from ...api.dependencies import require_agent_runtime_client
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..auth import ServiceClient
from ..baby.repository import BabyRecordRepository
from ..records.repository import RecordsRepository
from ..records.service import RecordsService
from .lactation_context import LactationContextService
from .lactation_context_schema import MaternalBabyProfileReadOutput
from .repository import ProfileRepository
from .topical_records import TopicalRecordsQuery, TopicalRecordsReadOutput, TopicalRecordsService


router = SurfaceAPIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    api_surface_metadata=api_surface(
        "internal_service_api",
        owner="profiles",
        clients=["agent-runtime"],
    ),
)


def get_agent_profile_read_service(
    session: AsyncSession = Depends(get_session),
) -> LactationContextService:
    return LactationContextService(
        profile_repository=ProfileRepository(session),
        records_service=RecordsService(repository=RecordsRepository(session)),
        baby_records_repository=BabyRecordRepository(session),
    )




@router.get("/profile", response_model=MaternalBabyProfileReadOutput)
async def read_agent_profile(
    actor_user_id: UUID,
    infant_scope: Literal["current_delivery", "all"] = Query(default="current_delivery"),
    as_of_date: date | None = None,
    timezone: str = Query(default="UTC", min_length=1, max_length=80),
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: LactationContextService = Depends(get_agent_profile_read_service),
) -> MaternalBabyProfileReadOutput:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ApiError(code="validation_failed", message="Use an IANA timezone.", status=422) from exc
    result = await service.read(
        owner_user_id=actor_user_id,
        as_of_date=as_of_date,
        infant_scope=infant_scope,
        timezone=timezone,
    )
    return MaternalBabyProfileReadOutput.model_validate(result)


def get_agent_topical_records_service(session: AsyncSession = Depends(get_session)) -> TopicalRecordsService:
    return TopicalRecordsService(session, ProfileRepository(session))


@router.get("/records", response_model=TopicalRecordsReadOutput)
async def read_agent_topical_records(
    actor_user_id: UUID,
    topic: Literal["feeding", "pumping", "diaper", "pain", "growth"],
    start_date: date,
    end_date: date,
    timezone: str,
    infant_id: UUID | None = None,
    limit: int = 20,
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: TopicalRecordsService = Depends(get_agent_topical_records_service),
) -> TopicalRecordsReadOutput:
    try:
        query = TopicalRecordsQuery(actor_user_id=actor_user_id, topic=topic, infant_id=infant_id,
            start_date=start_date, end_date=end_date, timezone=timezone, limit=limit)
    except ValidationError as exc:
        raise ApiError(code="validation_failed", message="Record query is invalid.", status=422) from exc
    return await service.read(query)
