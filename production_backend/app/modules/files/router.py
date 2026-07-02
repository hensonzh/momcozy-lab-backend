from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_object_storage, require_current_user
from ...infrastructure.db import get_session
from ...infrastructure.object_storage import ObjectStorage
from ..auth import CurrentUser
from .repository import FileRepository
from .schemas import FileRead
from .service import FileService


router = APIRouter(prefix="/files", tags=["files"])


def get_file_service(
    session: AsyncSession = Depends(get_session),
    object_storage: ObjectStorage = Depends(get_object_storage),
) -> FileService:
    return FileService(repository=FileRepository(session), object_storage=object_storage)


@router.post("/upload", response_model=FileRead, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_current_user),
    service: FileService = Depends(get_file_service),
) -> FileRead:
    body = await file.read()
    created = await service.upload(
        owner_user_id=current_user.user_id,
        filename=file.filename or "",
        body=body,
        content_type=file.content_type or "application/octet-stream",
    )
    return FileRead.model_validate(created)


@router.get("/{file_id}", response_model=FileRead)
async def get_file(
    file_id: UUID,
    current_user: CurrentUser = Depends(require_current_user),
    service: FileService = Depends(get_file_service),
) -> FileRead:
    file_object = await service.get_for_owner(file_id=file_id, owner_user_id=current_user.user_id)
    return FileRead.model_validate(file_object)
