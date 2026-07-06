from __future__ import annotations

from uuid import UUID

from fastapi import Depends, File, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_object_storage, optional_idempotency_key, require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ...infrastructure.object_storage import ObjectStorage
from ..audit import AuditService, IdempotencyService, OutboxService
from ..audit.repository import AuditRepository, OutboxRepository
from ..auth import CurrentUser
from .repository import FileRepository
from .schemas import FileListResponse, FileRead
from .service import FileService
from .vision_service import FileVisionService
from .vision_streaming import encode_file_vision_sse_events


router = SurfaceAPIRouter(
    prefix="/files",
    tags=["files"],
    api_surface_metadata=api_surface("public_app_api", owner="files", clients=["flutter"]),
)
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


def get_file_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
    object_storage: ObjectStorage = Depends(get_object_storage),
) -> FileService:
    audit_repository = AuditRepository(session)
    return FileService(
        repository=FileRepository(session),
        object_storage=object_storage,
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
        outbox_service=OutboxService(repository=OutboxRepository(session)),
        max_upload_bytes=request.app.state.settings.file_upload_max_bytes,
    )


def get_file_vision_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
    object_storage: ObjectStorage = Depends(get_object_storage),
) -> FileVisionService:
    return FileVisionService(
        repository=FileRepository(session),
        object_storage=object_storage,
        settings=request.app.state.settings,
    )


@router.post("/upload", response_model=FileRead, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: FileService = Depends(get_file_service),
) -> FileRead:
    body = await _read_upload_body(file=file, max_bytes=request.app.state.settings.file_upload_max_bytes)
    created = await service.upload(
        owner_user_id=current_user.user_id,
        filename=file.filename or "",
        body=body,
        content_type=file.content_type or "application/octet-stream",
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return FileRead.model_validate(created)


@router.get("", response_model=FileListResponse)
async def list_files(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: FileService = Depends(get_file_service),
) -> FileListResponse:
    files = await service.list_for_owner(owner_user_id=current_user.user_id, limit=limit)
    return FileListResponse(items=[FileRead.model_validate(file_object) for file_object in files])


@router.get("/{file_id}", response_model=FileRead)
async def get_file(
    file_id: UUID,
    current_user: CurrentUser = Depends(require_current_user),
    service: FileService = Depends(get_file_service),
) -> FileRead:
    file_object = await service.get_for_owner(file_id=file_id, owner_user_id=current_user.user_id)
    return FileRead.model_validate(file_object)


@router.get(
    "/{file_id}/vision/events/stream",
    openapi_extra=api_surface("runtime_stream_api", owner="files", clients=["flutter"]),
)
async def stream_file_vision_events(
    file_id: UUID,
    current_user: CurrentUser = Depends(require_current_user),
    service: FileVisionService = Depends(get_file_vision_service),
) -> StreamingResponse:
    events = await service.events_for_owner(file_id=file_id, owner_user_id=current_user.user_id)
    return StreamingResponse(
        iter([encode_file_vision_sse_events(events)]),
        media_type="text/event-stream",
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: UUID,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: FileService = Depends(get_file_service),
) -> Response:
    await service.delete_for_owner(
        file_id=file_id,
        owner_user_id=current_user.user_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _read_upload_body(*, file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ApiError(
                code="payload_too_large",
                message="Uploaded file is too large.",
                status=413,
                details={"max_bytes": max_bytes},
            )
        chunks.append(chunk)
    return b"".join(chunks)
