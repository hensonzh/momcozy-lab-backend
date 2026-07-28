from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_agent_runtime_client
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..auth import ServiceClient
from .agent_asset_capability import AgentAssetCapabilityStore
from .agent_contracts import AgentFileResolveRequest, AgentFileResolveResponse
from .agent_service import AgentFileAccessService
from .repository import FileRepository


router = SurfaceAPIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    api_surface_metadata=api_surface(
        "internal_service_api",
        owner="files",
        clients=["agent-runtime"],
    ),
)


def get_agent_file_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentFileAccessService:
    settings = request.app.state.settings
    return AgentFileAccessService(
        repository=FileRepository(session),
        capability_store=AgentAssetCapabilityStore(
            redis_client=getattr(request.app.state, "redis_client", None),
            inactivity_ttl_seconds=(
                settings.agent_model_asset_inactivity_ttl_seconds
            ),
        ),
        public_base_url=settings.agent_model_asset_public_base_url,
    )


@router.post(
    "/files/resolve",
    response_model=AgentFileResolveResponse,
)
async def resolve_agent_file(
    payload: AgentFileResolveRequest,
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: AgentFileAccessService = Depends(get_agent_file_service),
) -> AgentFileResolveResponse:
    result = await service.resolve(
        owner_user_id=payload.actor_user_id,
        file_id=payload.file_id,
        purpose=payload.purpose,
    )
    return AgentFileResolveResponse.model_validate(result)
