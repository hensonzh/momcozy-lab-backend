"""Explicit owner-scoped erasure of an analysis and all its source originals."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ..audit import AuditService
from ..stool_photos.service import StoolPhotoService
from ..users.models import User
from .models import StoolAnalysis, StoolAnalysisPhoto


class StoolAnalysisDeletionService:
    def __init__(self, *, session: AsyncSession, photos: StoolPhotoService,
                 audit: AuditService) -> None:
        self.session, self.photos, self.audit = session, photos, audit

    async def delete(self, *, owner: UUID, analysis_id: UUID, request_id: str) -> None:
        account = await self.session.scalar(select(User).where(User.id == owner).with_for_update())
        if account is None or account.status != "active" or account.deleted_at is not None:
            raise ApiError(code="stool_analysis_context_unavailable", message="Analysis context is unavailable.", status=409)
        analysis = await self.session.scalar(select(StoolAnalysis).where(
            StoolAnalysis.id == analysis_id, StoolAnalysis.owner_user_id == owner,
        ))
        if analysis is None:
            raise ApiError(code="stool_analysis_context_unavailable", message="Analysis context is unavailable.", status=409)
        photo_ids = (await self.session.scalars(select(StoolAnalysisPhoto.photo_id).where(
            StoolAnalysisPhoto.analysis_id == analysis_id,
        ).order_by(StoolAnalysisPhoto.photo_id))).all()
        if not photo_ids:
            raise ApiError(code="stool_analysis_context_unavailable", message="Analysis context is unavailable.", status=409)
        for photo_id in photo_ids:
            await self.photos.delete(owner=owner, photo_id=photo_id, request_id=request_id)
        now = datetime.now(timezone.utc)
        analysis.revoked_at = analysis.revoked_at or now
        analysis.deleted_at = analysis.deleted_at or now
        analysis.final_result = None
        analysis.result_schema_version = None
        analysis.result_validated_at = None
        analysis.context_expires_at = None
        analysis.baby_context_snapshot_id = None
        analysis.market = None
        analysis.language = None
        analysis.baby_profile_version = None
        analysis.baby_birth_date = None
        analysis.baby_feeding_mode = None
        analysis.context_captured_at = None
        await self.audit.record(actor_user_id=owner, action="stool_analysis.delete",
                                resource_type="stool_analysis", resource_id=str(analysis_id),
                                request_id=request_id, details={"photo_count": len(photo_ids)})
