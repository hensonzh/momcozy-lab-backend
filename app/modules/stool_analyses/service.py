"""Reauthorize one completed stool result and its source images per model call."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ...infrastructure.object_storage import ObjectStorage
from ..auth.models import DeviceSession
from ..baby.profile_models import BabyProfile
from ..stool_photos.models import StoolPhoto
from ..users.models import User
from .context import CompletedStoolAnalysis, StoolContextPhoto, StoolFollowupContext, build_stool_followup_context
from .models import StoolAnalysis, StoolAnalysisPhoto
from .transport_contract import StoolAnalysisResult


def _future(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return False
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value) > now


def _unavailable() -> ApiError:
    return ApiError(code="stool_analysis_context_unavailable", message="Analysis context is unavailable.", status=409)


class StoolAnalysisContextResolver:
    def __init__(self, *, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session = session
        self.storage = storage

    async def resolve(
        self, *, actor_user_id: UUID, baby_id: UUID, thread_id: UUID,
        session_id: UUID, analysis_id: UUID, token_expires_at: datetime,
        now: datetime | None = None,
    ) -> StoolFollowupContext:
        at = now or datetime.now(timezone.utc)
        if token_expires_at.tzinfo is None or not _future(token_expires_at, at):
            raise _unavailable()
        user = await self.session.scalar(select(User).where(User.id == actor_user_id).with_for_update(read=True))
        device = await self.session.scalar(select(DeviceSession).where(
            DeviceSession.id == session_id, DeviceSession.user_id == actor_user_id,
        ).with_for_update(read=True))
        baby = await self.session.scalar(select(BabyProfile).where(
            BabyProfile.id == baby_id, BabyProfile.owner_user_id == actor_user_id,
        ).with_for_update(read=True))
        if (
            user is None or user.status != "active" or user.deleted_at is not None
            or device is None or device.status != "active" or device.revoked_at is not None
            or baby is None or baby.deleted_at is not None
        ):
            raise _unavailable()
        analysis = await self.session.scalar(select(StoolAnalysis).where(
            StoolAnalysis.id == analysis_id, StoolAnalysis.owner_user_id == actor_user_id,
            StoolAnalysis.baby_id == baby_id, StoolAnalysis.thread_id == thread_id,
        ).with_for_update(read=True))
        if (
            analysis is None or analysis.status != "completed" or analysis.deleted_at is not None
            or analysis.revoked_at is not None or analysis.completed_at is None
            or analysis.result_validated_at is None
            or not isinstance(analysis.final_result, dict) or not analysis.final_result
        ):
            raise _unavailable()
        try:
            validated_result = StoolAnalysisResult.model_validate(analysis.final_result)
            if len(json.dumps(validated_result.model_dump(mode="json"), ensure_ascii=False,
                              allow_nan=False).encode("utf-8")) > 16 * 1024:
                raise _unavailable()
        except (TypeError, ValueError) as exc:
            raise _unavailable() from exc
        links = list((await self.session.scalars(select(StoolAnalysisPhoto).where(
            StoolAnalysisPhoto.analysis_id == analysis_id,
        ).order_by(StoolAnalysisPhoto.position).with_for_update(read=True))).all())
        if not 1 <= len(links) <= 3 or [link.position for link in links] != list(range(len(links))):
            raise _unavailable()
        photos: list[StoolPhoto] = []
        for link in links:
            photo = await self.session.scalar(select(StoolPhoto).where(
                StoolPhoto.id == link.photo_id, StoolPhoto.owner_user_id == actor_user_id,
            ).with_for_update(read=True))
            if (
                photo is None or photo.deleted_at is not None or photo.purged_at is not None
                or photo.review_status != "passed" or photo.review_sha256 != link.original_sha256
                or photo.sha256 != link.original_sha256 or photo.review_expires_at is None
            ):
                raise _unavailable()
            photos.append(photo)
        # All scope/status/fingerprint checks run before any object storage read.
        bodies: list[bytes] = []
        try:
            for photo in photos:
                bodies.append(await self.storage.get_bytes(key=photo.object_key))
        except Exception as exc:
            raise _unavailable() from exc
        projection = CompletedStoolAnalysis(
            id=analysis.id, owner_user_id=analysis.owner_user_id, baby_id=analysis.baby_id,
            thread_id=analysis.thread_id, status=analysis.status,
            result_schema_version=analysis.result_schema_version or "",
            result=analysis.final_result, result_validated_at=analysis.result_validated_at,
            photo_ids=tuple(link.photo_id for link in links), deleted_at=analysis.deleted_at,
            revoked_at=analysis.revoked_at,
        )
        try:
            return build_stool_followup_context(
                analysis=projection,
                photos=tuple(StoolContextPhoto(
                    id=photo.id, owner_user_id=photo.owner_user_id, body=body,
                    sha256=photo.sha256, review_sha256=photo.review_sha256,
                    review_status=photo.review_status,
                    review_expires_at=(photo.review_expires_at.replace(tzinfo=timezone.utc)
                                       if photo.review_expires_at and photo.review_expires_at.tzinfo is None
                                       else photo.review_expires_at),
                    deleted_at=photo.deleted_at,
                ) for photo, body in zip(photos, bodies, strict=True)),
                actor_user_id=actor_user_id, baby_id=baby_id, thread_id=thread_id,
            )
        except (ApiError, ValueError, TypeError) as exc:
            raise _unavailable() from exc
