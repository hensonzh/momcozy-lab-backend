from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_object_storage, require_current_user
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ...infrastructure.object_storage import ObjectStorage
from ..audit import AuditService, IdempotencyService, OutboxService
from ..audit.repository import AuditRepository, OutboxRepository
from ..auth import CurrentUser
from .repository import FileRepository
from .schemas import FileListResponse, FileRead
from .service import FileService


router = APIRouter(prefix="/files", tags=["files"])


def get_file_service(
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
    )


@router.post("/upload", response_model=FileRead, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: CurrentUser = Depends(require_current_user),
    service: FileService = Depends(get_file_service),
) -> FileRead:
    body = await file.read()
    created = await service.upload(
        owner_user_id=current_user.user_id,
        filename=file.filename or "",
        body=body,
        content_type=file.content_type or "application/octet-stream",
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=_normalize_idempotency_key(idempotency_key),
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


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: UUID,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: CurrentUser = Depends(require_current_user),
    service: FileService = Depends(get_file_service),
) -> Response:
    await service.delete_for_owner(
        file_id=file_id,
        owner_user_id=current_user.user_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=_normalize_idempotency_key(idempotency_key),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _normalize_idempotency_key(value: str | None) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    if len(key) > 255:
        raise ApiError(code="validation_failed", message="Idempotency-Key is too long.", status=422)
    return key
