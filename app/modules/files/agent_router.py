from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import (
    get_object_storage,
    require_agent_runtime_client,
)
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.settings import AGENT_FILE_URL_MINIMUM_REMAINING_SECONDS
from ...infrastructure.db import get_session
from ...infrastructure.object_storage import ObjectStorage
from ..auth import ServiceClient
from .agent_contracts import AgentFileResolveRequest, AgentFileResolveResponse
from .agent_service import AgentFileAccessService
from .agent_url_cache import AgentFileUrlCache
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
    object_storage: ObjectStorage = Depends(get_object_storage),
) -> AgentFileAccessService:
    settings = request.app.state.settings
    return AgentFileAccessService(
        repository=FileRepository(session),
        object_storage=object_storage,
        url_ttl_seconds=settings.agent_image_signed_url_ttl_seconds,
        url_cache=AgentFileUrlCache(
            redis_client=getattr(request.app.state, "redis_client", None),
            reuse_ttl_seconds=settings.agent_file_url_reuse_ttl_seconds,
            minimum_remaining_seconds=AGENT_FILE_URL_MINIMUM_REMAINING_SECONDS,
        ),
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
