from __future__ import annotations


from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_agent_runtime_client
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..auth import ServiceClient
from ..records.repository import RecordsRepository
from ..records.service import RecordsService
from .lactation_context import LactationContextService
from .lactation_context_schema import MaternalBabyProfileReadOutput
from .repository import ProfileRepository


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
    )




@router.get("/profile", response_model=MaternalBabyProfileReadOutput)
async def read_agent_profile(
    actor_user_id: UUID,
    infant_scope: Literal["current_delivery", "all"] = Query(default="current_delivery"),
    as_of_date: date | None = None,
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: LactationContextService = Depends(get_agent_profile_read_service),
) -> MaternalBabyProfileReadOutput:
    result = await service.read(
        owner_user_id=actor_user_id,
        as_of_date=as_of_date,
        infant_scope=infant_scope,
    )
    return MaternalBabyProfileReadOutput.model_validate(result)
