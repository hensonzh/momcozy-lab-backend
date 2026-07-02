from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FileObject


class FileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        owner_user_id: UUID,
        object_key: str,
        original_filename: str,
        content_type: str,
        size_bytes: int,
    ) -> FileObject:
        file_object = FileObject(
            owner_user_id=owner_user_id,
            object_key=object_key,
            original_filename=original_filename,
            content_type=content_type,
            size_bytes=size_bytes,
        )
        self.session.add(file_object)
        await self.session.flush()
        return file_object

    async def get_for_owner(self, *, file_id: UUID, owner_user_id: UUID) -> FileObject | None:
        statement = select(FileObject).where(
            FileObject.id == file_id,
            FileObject.owner_user_id == owner_user_id,
            FileObject.deleted_at.is_(None),
        )
        return await self.session.scalar(statement)

    async def list_for_owner(self, *, owner_user_id: UUID, limit: int) -> list[FileObject]:
        statement = (
            select(FileObject)
            .where(
                FileObject.owner_user_id == owner_user_id,
                FileObject.deleted_at.is_(None),
                FileObject.status == "active",
            )
            .order_by(FileObject.created_at.desc(), FileObject.id.desc())
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def soft_delete_for_owner(
        self,
        *,
        file_id: UUID,
        owner_user_id: UUID,
        deleted_at: datetime,
    ) -> FileObject | None:
        file_object = await self.get_for_owner(file_id=file_id, owner_user_id=owner_user_id)
        if file_object is None:
            return None
        file_object.status = "deleted"
        file_object.deleted_at = deleted_at
        await self.session.flush()
        return file_object
