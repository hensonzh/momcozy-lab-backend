from __future__ import annotations

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_object_storage
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ...infrastructure.object_storage import ObjectStorage
from .agent_asset_capability import AgentAssetCapabilityStore
from .agent_service import AgentModelAssetService
from .repository import FileRepository


router = SurfaceAPIRouter(
    prefix="/model-assets",
    tags=["model-assets"],
    api_surface_metadata=api_surface(
        "public_app_api",
        owner="files",
        clients=["openai-responses"],
        notes=(
            "Opaque bearer capability used only for model input fetches."
        ),
    ),
)


def get_agent_model_asset_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
    object_storage: ObjectStorage = Depends(get_object_storage),
) -> AgentModelAssetService:
    settings = request.app.state.settings
    return AgentModelAssetService(
        repository=FileRepository(session),
        object_storage=object_storage,
        capability_store=AgentAssetCapabilityStore(
            redis_client=getattr(
                request.app.state,
                "redis_client",
                None,
            ),
            inactivity_ttl_seconds=(
                settings.agent_model_asset_inactivity_ttl_seconds
            ),
        ),
    )


@router.get(
    "/{token}",
    response_class=Response,
    responses={
        200: {
            "description": (
                "Authorized model asset bytes. Content-Type matches "
                "the validated image or PDF."
            ),
            "content": {
                content_type: {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                }
                for content_type in (
                    "image/gif",
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                    "application/pdf",
                )
            },
        },
        404: {
            "description": (
                "Capability is unknown, expired, revoked, or no "
                "longer matches authoritative file state."
            )
        },
        429: {"description": "Rate limit exceeded."},
        503: {
            "description": (
                "Capability storage or object storage is unavailable."
            )
        },
    },
)
async def get_agent_model_asset(
    token: str,
    service: AgentModelAssetService = Depends(
        get_agent_model_asset_service
    ),
) -> Response:
    asset = await service.fetch(token=token)
    return Response(
        content=asset.body,
        media_type=asset.content_type,
        headers={
            "Cache-Control": "private, no-store, max-age=0",
        },
    )
