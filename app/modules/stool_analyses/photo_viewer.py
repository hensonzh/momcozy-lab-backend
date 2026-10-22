"""Owner-scoped, ephemeral thumbnails for an analysis; never publish original URLs."""
from __future__ import annotations

from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ...infrastructure.object_storage import ObjectStorage
from ..stool_photos.models import StoolPhoto
from ..stool_photos.rules import prepare_stool_thumbnail
from .events import StoolNonmedicalEventLedger
from .models import StoolAnalysisPhoto


def _unavailable() -> ApiError:
    return ApiError(code="stool_analysis_context_unavailable", message="Analysis context is unavailable.", status=409)


class StoolAnalysisPhotoViewer:
    def __init__(self, *, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session, self.storage = session, storage

    async def _refs(self, *, owner: UUID, analysis_id: UUID) -> list[tuple[StoolAnalysisPhoto, StoolPhoto]]:
        await StoolNonmedicalEventLedger(self.session).get_analysis(owner=owner, analysis_id=analysis_id)
        rows = (await self.session.execute(select(StoolAnalysisPhoto, StoolPhoto)
            .join(StoolPhoto, StoolPhoto.id == StoolAnalysisPhoto.photo_id)
            .where(StoolAnalysisPhoto.analysis_id == analysis_id)
            .order_by(StoolAnalysisPhoto.position))).all()
        if not 1 <= len(rows) <= 3 or any(
            link.position != index or photo.owner_user_id != owner or
            photo.deleted_at is not None or photo.purged_at is not None or
            photo.review_status != "passed" or photo.review_expires_at is None or
            photo.review_sha256 != photo.sha256 or
            link.original_sha256 != photo.sha256
            for index, (link, photo) in enumerate(rows)
        ):
            raise _unavailable()
        return [(link, photo) for link, photo in rows]

    async def list_refs(self, *, owner: UUID, analysis_id: UUID) -> list[tuple[UUID, int]]:
        return [(photo.id, link.position) for link, photo in await self._refs(owner=owner, analysis_id=analysis_id)]

    async def thumbnail(self, *, owner: UUID, analysis_id: UUID, photo_id: UUID) -> bytes:
        refs = await self._refs(owner=owner, analysis_id=analysis_id)
        photo = next((photo for _, photo in refs if photo.id == photo_id), None)
        if photo is None:
            raise _unavailable()
        try:
            original = await self.storage.get_bytes(key=photo.object_key)
            if len(original) != photo.size_bytes or sha256(original).hexdigest() != photo.sha256:
                raise _unavailable()
            return prepare_stool_thumbnail(
                original, content_type=photo.content_type, width=photo.width, height=photo.height,
            )
        except Exception as exc:
            raise _unavailable() from exc
