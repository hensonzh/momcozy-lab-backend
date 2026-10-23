from __future__ import annotations

from uuid import UUID

from fastapi import Depends, File, Header, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_object_storage, require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ...infrastructure.object_storage import ObjectStorage
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .repository import StoolPhotoRepository
from .rules import MAX_ORIGINAL_BYTES
from .schemas import StoolPhotoRead
from .service import StoolPhotoService

router = SurfaceAPIRouter(prefix="/stool-photos", tags=["stool-photos"],
                          api_surface_metadata=api_surface("public_app_api", owner="stool-photos", clients=["flutter"]))


def get_service(session: AsyncSession = Depends(get_session),
                storage: ObjectStorage = Depends(get_object_storage)) -> StoolPhotoService:
    audit = AuditRepository(session)
    return StoolPhotoService(repository=StoolPhotoRepository(session), storage=storage,
                             idempotency=IdempotencyService(repository=audit), audit=AuditService(repository=audit))


def stool_photo_review_available(request: Request) -> bool:
    settings = request.app.state.settings
    return bool(settings.stool_analysis_ready)


@router.post("", response_model=StoolPhotoRead, status_code=202)
async def upload_stool_photo(
    request: Request, file: UploadFile = File(...),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    user: CurrentUser = Depends(require_current_user), service: StoolPhotoService = Depends(get_service),
    review_available: bool = Depends(stool_photo_review_available),
) -> StoolPhotoRead:
    if not review_available:
        raise ApiError(code="stool_photo_review_unavailable", message="Photo review is unavailable.", status=503)
    # Cap reads before allocation; filename/MIME are not trusted as format evidence.
    body = await file.read(MAX_ORIGINAL_BYTES + 1)
    if len(body) > MAX_ORIGINAL_BYTES:
        raise ApiError(code="payload_too_large", message="Original photo exceeds 10 MB.", status=413)
    photo = await service.upload(owner=user.user_id, body=body, key=idempotency_key,
                                 request_id=str(getattr(request.state, "request_id", "") or ""))
    return StoolPhotoRead.model_validate(photo)


@router.get("/capability")
async def stool_photo_capability(
    _request: Request,
    _user: CurrentUser = Depends(require_current_user),
    review_available: bool = Depends(stool_photo_review_available),
) -> dict[str, bool]:
    settings = _request.app.state.settings
    return {
        "review_available": review_available,
        "analysis_available": settings.stool_analysis_ready,
        "followup_available": bool(settings.agent_runtime_service_api_key),
    }


@router.get("/{photo_id}/review", response_model=StoolPhotoRead)
async def get_stool_photo_review(photo_id: UUID, user: CurrentUser = Depends(require_current_user),
                                 service: StoolPhotoService = Depends(get_service)) -> StoolPhotoRead:
    return StoolPhotoRead.model_validate(await service.get(owner=user.user_id, photo_id=photo_id))


@router.delete("/{photo_id}", status_code=204)
async def delete_stool_photo(photo_id: UUID, request: Request, user: CurrentUser = Depends(require_current_user),
                             service: StoolPhotoService = Depends(get_service)) -> None:
    await service.delete(owner=user.user_id, photo_id=photo_id,
                         request_id=str(getattr(request.state, "request_id", "") or ""))
