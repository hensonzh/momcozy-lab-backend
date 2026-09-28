from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_object_storage, require_agent_runtime_client
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ...infrastructure.object_storage import ObjectStorage
from ..auth import ServiceClient
from .agent_asset_capability import AgentAssetCapabilityStore
from .agent_contracts import AgentFileResolveRequest, AgentFileResolveResponse
from .agent_service import AgentFileAccessService, AgentLocalImageService
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


def get_agent_local_image_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
    object_storage: ObjectStorage = Depends(get_object_storage),
) -> AgentLocalImageService:
    return AgentLocalImageService(
        repository=FileRepository(session),
        object_storage=object_storage,
        max_bytes=request.app.state.settings.file_upload_max_bytes,
    )


@router.get("/files/{file_id}/model-image", response_class=Response)
async def get_local_model_image(
    file_id: UUID,
    request: Request,
    actor_user_id: UUID = Query(...),
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: AgentLocalImageService = Depends(get_agent_local_image_service),
) -> Response:
    if request.app.state.settings.app_env != "local":
        raise ApiError(code="not_found", message="Not found.", status=404)
    image = await service.fetch(owner_user_id=actor_user_id, file_id=file_id)
    return Response(
        content=image.body,
        media_type=image.content_type,
        headers={"Cache-Control": "private, no-store"},
    )
