"""Resolve the Product-owned snapshot and derived image, immediately before analysis."""
from __future__ import annotations

import base64
from datetime import timezone
from hashlib import sha256
from io import BytesIO
from typing import Literal, cast
from uuid import UUID

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ...infrastructure.object_storage import ObjectStorage
from ..baby.profile_models import BabyProfile
from ..stool_photos.models import StoolPhoto
from ..stool_photos.rules import MAX_ORIGINAL_BYTES, prepare_model_image, validate_original
from ..users.models import User
from .models import StoolAnalysis, StoolAnalysisPhoto
from .transport_contract import SpecialtyAnalysisInput, SpecialtyBabyContext, SpecialtyPhotoInput


def _unavailable() -> ApiError:
    return ApiError(code="stool_analysis_context_unavailable", message="Analysis context is unavailable.", status=409)


def _derived_jpeg(original: bytes) -> bytes:
    """Strip source metadata and cap the temporary wire image at 2 MB."""
    with Image.open(BytesIO(prepare_model_image(original))) as image:
        image.thumbnail((2048, 2048))
        for quality in (85, 70, 55):
            output = BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            if len(output.getvalue()) <= 2_000_000:
                return output.getvalue()
    raise _unavailable()


class StoolSpecialtyInputBuilder:
    def __init__(self, *, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session, self.storage = session, storage

    async def build(self, analysis_id: UUID) -> SpecialtyAnalysisInput:
        analysis = await self.session.get(StoolAnalysis, analysis_id)
        if (analysis is None or analysis.status != "running" or analysis.revoked_at is not None
                or analysis.deleted_at is not None or analysis.baby_context_snapshot_id is None
                or analysis.market not in ("CN", "US") or analysis.language not in ("zh", "en")
                or analysis.baby_profile_version is None or analysis.baby_feeding_mode is None
                or analysis.context_captured_at is None):
            raise _unavailable()
        owner = await self.session.get(User, analysis.owner_user_id)
        baby = await self.session.get(BabyProfile, analysis.baby_id)
        if (owner is None or owner.status != "active" or owner.deleted_at is not None
                or baby is None or baby.owner_user_id != analysis.owner_user_id or baby.deleted_at is not None):
            raise _unavailable()
        links = list((await self.session.scalars(select(StoolAnalysisPhoto).where(
            StoolAnalysisPhoto.analysis_id == analysis_id,
        ).order_by(StoolAnalysisPhoto.position))).all())
        if not 1 <= len(links) <= 3 or [link.position for link in links] != list(range(len(links))):
            raise _unavailable()
        photos: list[SpecialtyPhotoInput] = []
        for link in links:
            photo = await self.session.get(StoolPhoto, link.photo_id)
            if (photo is None or photo.owner_user_id != analysis.owner_user_id
                    or photo.deleted_at is not None or photo.purged_at is not None
                    or photo.review_status != "passed" or photo.review_expires_at is None
                    or photo.review_sha256 != link.original_sha256 or photo.sha256 != link.original_sha256):
                raise _unavailable()
            try:
                original = await self.storage.get_bytes(key=photo.object_key)
                if (len(original) > MAX_ORIGINAL_BYTES or len(original) != photo.size_bytes
                        or sha256(original).hexdigest() != photo.sha256):
                    raise _unavailable()
                metadata = validate_original(original)
                if (metadata.content_type != photo.content_type or metadata.width != photo.width
                        or metadata.height != photo.height):
                    raise _unavailable()
                derived = _derived_jpeg(original)
            except Exception as exc:
                raise _unavailable() from exc
            photos.append(SpecialtyPhotoInput(
                photo_id=photo.id, position=link.position, original_sha256=photo.sha256,
                jpeg_b64=base64.b64encode(derived).decode("ascii"),
            ))
        return SpecialtyAnalysisInput(
            analysis_id=analysis.id,
            baby_context=SpecialtyBabyContext(
                baby_context_snapshot_id=analysis.baby_context_snapshot_id,
                profile_version=analysis.baby_profile_version,
                birth_date=analysis.baby_birth_date,
                feeding_mode=analysis.baby_feeding_mode,
                captured_at=(analysis.context_captured_at.replace(tzinfo=timezone.utc)
                             if analysis.context_captured_at.tzinfo is None else analysis.context_captured_at),
            ), market=cast(Literal["CN", "US"], analysis.market),
            language=cast(Literal["zh", "en"], analysis.language), photos=tuple(photos),
        )
