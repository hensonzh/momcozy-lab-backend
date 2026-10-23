"""Configuration-gated public App contract for the independent stool-analysis flow."""
from __future__ import annotations

import json
from datetime import datetime
from collections.abc import AsyncIterator
from typing import Any, Literal, cast
from uuid import UUID

import httpx
from fastapi import Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_object_storage, require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.agent_runtime import RuntimeConversationGateway
from ...infrastructure.db import get_session
from ...infrastructure.object_storage import ObjectStorage
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from ..stool_photos.repository import StoolPhotoRepository
from ..stool_photos.service import StoolPhotoService
from .events import StoolAnalysisEventLedger
from .submission import StoolAnalysisSubmission
from .photo_viewer import StoolAnalysisPhotoViewer
from .deletion import StoolAnalysisDeletionService

router = SurfaceAPIRouter(
    prefix="/stool-analyses", tags=["stool-analyses"],
    api_surface_metadata=api_surface("public_app_api", owner="stool-analyses", clients=["flutter"]),
)


class StoolAnalysisCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baby_id: UUID
    thread_id: UUID
    photo_ids: list[UUID] = Field(min_length=1, max_length=3)


class StoolAnalysisRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    result_schema_version: Literal["stool.result.v1"] | None = None
    result: dict[str, Any] | None = None


class StoolAnalysisHistoryItem(StoolAnalysisRead):
    baby_id: UUID
    created_at: datetime


class StoolAnalysisHistoryPage(BaseModel):
    items: list[StoolAnalysisHistoryItem]
    next_cursor: UUID | None


class StoolAnalysisPhotoRead(BaseModel):
    photo_id: UUID
    position: int = Field(ge=0, le=2)


class StoolAnalysisPhotosRead(BaseModel):
    items: list[StoolAnalysisPhotoRead]


def analysis_available(request: Request) -> bool:
    return bool(request.app.state.settings.stool_analysis_enabled)


def _require_available(available: bool) -> None:
    if not available:
        raise ApiError(code="stool_analysis_unavailable", message="Stool analysis is unavailable.", status=503)


def require_analysis_available(available: bool = Depends(analysis_available)) -> None:
    _require_available(available)


def _trusted_market(request: Request) -> Literal["CN", "US"]:
    market = request.app.state.settings.stool_specialty_market
    if market not in ("CN", "US"):
        raise ApiError(code="stool_analysis_unavailable", message="Stool analysis is unavailable.", status=503)
    return cast(Literal["CN", "US"], market)


async def get_stool_conversation_gateway(
    request: Request,
) -> AsyncIterator[RuntimeConversationGateway]:
    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
        yield RuntimeConversationGateway(client, request.app.state.settings.agent_runtime_url)


async def verify_stool_thread(
    payload: StoolAnalysisCreate,
    request: Request,
    user: CurrentUser = Depends(require_current_user),
    _guard: None = Depends(require_analysis_available),
    gateway: RuntimeConversationGateway = Depends(get_stool_conversation_gateway),
) -> UUID:
    # No regional context means no owner lookup or admission, even in test-enabled deployments.
    _trusted_market(request)
    # Agent owns thread IDs; its owner-scoped endpoint validates the same bearer.
    await gateway.verify_owner(user.user_id, payload.thread_id,
                               request.headers.get("Authorization", ""))
    return payload.thread_id


async def verify_stool_history_thread(
    thread_id: UUID,
    request: Request,
    user: CurrentUser = Depends(require_current_user),
    _guard: None = Depends(require_analysis_available),
    gateway: RuntimeConversationGateway = Depends(get_stool_conversation_gateway),
) -> UUID:
    await gateway.verify_owner(user.user_id, thread_id,
                               request.headers.get("Authorization", ""))
    return thread_id


def get_submission_service(
    guard: None = Depends(require_analysis_available),
    session: AsyncSession = Depends(get_session),
    storage: ObjectStorage = Depends(get_object_storage),
) -> StoolAnalysisSubmission:
    repository = AuditRepository(session)
    return StoolAnalysisSubmission(
        session=session, storage=storage,
        idempotency=IdempotencyService(repository=repository), audit=AuditService(repository=repository),
    )


def get_event_ledger(guard: None = Depends(require_analysis_available),
                     session: AsyncSession = Depends(get_session)) -> StoolAnalysisEventLedger:
    return StoolAnalysisEventLedger(session)


@router.post("", status_code=202, response_model=StoolAnalysisRead, response_model_exclude_none=True)
async def submit_stool_analysis(
    payload: StoolAnalysisCreate, request: Request, response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    language: Literal["zh", "en"] = Header(default="en", alias="Accept-Language"),
    user: CurrentUser = Depends(require_current_user),
    available: bool = Depends(analysis_available),
    verified_thread_id: UUID = Depends(verify_stool_thread),
    service: StoolAnalysisSubmission = Depends(get_submission_service),
) -> StoolAnalysisRead:
    response.headers["Cache-Control"] = "private, no-store"
    _require_available(available)
    market = _trusted_market(request)
    if verified_thread_id != payload.thread_id:
        raise ApiError(code="stool_thread_binding_unavailable", message="Thread binding is unavailable.", status=409)
    analysis = await service.prepare(
        owner=user.user_id, baby_id=payload.baby_id, trusted_thread_id=verified_thread_id,
        photo_ids=payload.photo_ids, idempotency_key=idempotency_key,
        trusted_market=market, language=language,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return StoolAnalysisRead.model_validate({
        "analysis_id": analysis.id, "status": analysis.status,
        "result_schema_version": getattr(analysis, "result_schema_version", None),
        "result": getattr(analysis, "final_result", None),
    })


@router.get("", response_model=StoolAnalysisHistoryPage)
async def list_stool_analyses(
    thread_id: UUID, response: Response,
    cursor: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=50),
    user: CurrentUser = Depends(require_current_user),
    available: bool = Depends(analysis_available),
    verified_thread_id: UUID = Depends(verify_stool_history_thread),
    ledger: StoolAnalysisEventLedger = Depends(get_event_ledger),
) -> StoolAnalysisHistoryPage:
    response.headers["Cache-Control"] = "private, no-store"
    _require_available(available)
    if verified_thread_id != thread_id:
        raise ApiError(code="stool_thread_binding_unavailable", message="Thread binding is unavailable.", status=409)
    rows, next_cursor = await ledger.list_for_thread(
        owner=user.user_id, thread_id=verified_thread_id, cursor=cursor, limit=limit,
    )
    return StoolAnalysisHistoryPage(items=[StoolAnalysisHistoryItem.model_validate({
        "analysis_id": row.id, "baby_id": row.baby_id,
        "status": row.status, "created_at": row.created_at,
        "result_schema_version": getattr(row, "result_schema_version", None),
        "result": getattr(row, "final_result", None),
    }) for row in rows], next_cursor=next_cursor)


@router.get("/{analysis_id}", response_model=StoolAnalysisRead, response_model_exclude_none=True)
async def get_stool_analysis(
    analysis_id: UUID, response: Response,
    user: CurrentUser = Depends(require_current_user),
    available: bool = Depends(analysis_available),
    ledger: StoolAnalysisEventLedger = Depends(get_event_ledger),
) -> StoolAnalysisRead:
    response.headers["Cache-Control"] = "private, no-store"
    _require_available(available)
    analysis = await ledger.get_analysis(owner=user.user_id, analysis_id=analysis_id)
    return StoolAnalysisRead.model_validate({
        "analysis_id": analysis.id, "status": analysis.status,
        "result_schema_version": getattr(analysis, "result_schema_version", None),
        "result": getattr(analysis, "final_result", None),
    })


def get_photo_viewer(
    guard: None = Depends(require_analysis_available),
    session: AsyncSession = Depends(get_session),
    storage: ObjectStorage = Depends(get_object_storage),
) -> StoolAnalysisPhotoViewer:
    return StoolAnalysisPhotoViewer(session=session, storage=storage)


@router.get("/{analysis_id}/photos", response_model=StoolAnalysisPhotosRead)
async def list_stool_analysis_photos(
    analysis_id: UUID, response: Response,
    user: CurrentUser = Depends(require_current_user),
    available: bool = Depends(analysis_available),
    viewer: StoolAnalysisPhotoViewer = Depends(get_photo_viewer),
) -> StoolAnalysisPhotosRead:
    response.headers["Cache-Control"] = "private, no-store"
    _require_available(available)
    refs = await viewer.list_refs(owner=user.user_id, analysis_id=analysis_id)
    return StoolAnalysisPhotosRead(items=[
        StoolAnalysisPhotoRead(photo_id=photo_id, position=position)
        for photo_id, position in refs
    ])


@router.get(
    "/{analysis_id}/photos/{photo_id}/thumbnail", response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def get_stool_analysis_thumbnail(
    analysis_id: UUID, photo_id: UUID,
    user: CurrentUser = Depends(require_current_user),
    available: bool = Depends(analysis_available),
    viewer: StoolAnalysisPhotoViewer = Depends(get_photo_viewer),
) -> Response:
    _require_available(available)
    jpeg = await viewer.thumbnail(owner=user.user_id, analysis_id=analysis_id, photo_id=photo_id)
    return Response(content=jpeg, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


def get_deletion_service(
    session: AsyncSession = Depends(get_session),
    storage: ObjectStorage = Depends(get_object_storage),
) -> StoolAnalysisDeletionService:
    audit_repository = AuditRepository(session)
    audit = AuditService(repository=audit_repository)
    photos = StoolPhotoService(
        repository=StoolPhotoRepository(session), storage=storage,
        idempotency=IdempotencyService(repository=audit_repository), audit=audit,
    )
    return StoolAnalysisDeletionService(session=session, photos=photos, audit=audit)


@router.delete("/{analysis_id}", status_code=204)
async def delete_stool_analysis(
    analysis_id: UUID, request: Request, response: Response,
    user: CurrentUser = Depends(require_current_user),
    service: StoolAnalysisDeletionService = Depends(get_deletion_service),
) -> None:
    # Erasure must remain available even while submission/clinical reads are disabled.
    response.headers["Cache-Control"] = "private, no-store"
    await service.delete(owner=user.user_id, analysis_id=analysis_id,
                         request_id=str(getattr(request.state, "request_id", "") or ""))


@router.get(
    "/{analysis_id}/events/stream",
    openapi_extra=api_surface("runtime_stream_api", owner="stool-analyses", clients=["flutter"]),
)
async def stream_stool_analysis_events(
    analysis_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(require_current_user),
    available: bool = Depends(analysis_available),
    ledger: StoolAnalysisEventLedger = Depends(get_event_ledger),
) -> StreamingResponse:
    _require_available(available)
    # Materialize a bounded committed page under authorization before streaming.
    events = await ledger.list_after(owner=user.user_id, analysis_id=analysis_id, after=after_sequence)
    frames = []
    for item in events:
        data = json.dumps({
            "analysis_id": str(item.analysis_id), "sequence": item.sequence,
            "schema_version": item.schema_version, "event_type": item.event_type,
            **({"payload": getattr(item, "payload", None)}
               if getattr(item, "payload", None) is not None else {}),
        }, separators=(",", ":"), ensure_ascii=False)
        frames.append(f"id: {item.sequence}\nevent: {item.event_type}\ndata: {data}\n\n")
    return StreamingResponse(
        iter(frames), media_type="text/event-stream",
        headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
    )
