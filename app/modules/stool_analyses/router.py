"""Owner-scoped internal resolver for completed stool analyses."""
from __future__ import annotations

import base64
from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_object_storage, require_agent_runtime_client
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ...infrastructure.object_storage import ObjectStorage
from ..auth import ServiceClient
from .service import StoolAnalysisContextResolver

router = SurfaceAPIRouter(
    prefix="/internal/agent/stool-analyses", tags=["internal-stool-analyses"],
    api_surface_metadata=api_surface("internal_service_api", owner="stool-analyses", clients=["agent-runtime"]),
)


class StoolContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    actor_user_id: UUID
    session_id: UUID
    baby_id: UUID
    thread_id: UUID
    token_expires_at: AwareDatetime


class StoolContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    owner_user_id: UUID
    baby_id: UUID
    thread_id: UUID
    status: Literal["completed"]
    result_schema_version: Literal["stool.result.v1"]
    result: dict[str, Any]
    images_b64: list[str] = Field(min_length=1, max_length=3)


def get_context_service(
    session: AsyncSession = Depends(get_session),
    storage: ObjectStorage = Depends(get_object_storage),
) -> StoolAnalysisContextResolver:
    return StoolAnalysisContextResolver(session=session, storage=storage)


@router.post("/context/resolve", response_model=StoolContextResponse)
async def resolve_stool_context(
    payload: StoolContextRequest, response: Response,
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: StoolAnalysisContextResolver = Depends(get_context_service),
) -> StoolContextResponse:
    response.headers["Cache-Control"] = "private, no-store"
    resolved = await service.resolve(
        analysis_id=payload.analysis_id, actor_user_id=payload.actor_user_id, session_id=payload.session_id,
        baby_id=payload.baby_id, thread_id=payload.thread_id,
        token_expires_at=payload.token_expires_at,
    )
    return StoolContextResponse(
        analysis_id=resolved.analysis_id, owner_user_id=payload.actor_user_id,
        baby_id=payload.baby_id, thread_id=payload.thread_id, status="completed",
        result_schema_version="stool.result.v1",
        result=dict(resolved.result),
        images_b64=[base64.b64encode(image).decode("ascii") for image in resolved.images],
    )
