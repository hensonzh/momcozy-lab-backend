from __future__ import annotations

from uuid import UUID
from typing import cast

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..users.models import User
from ..stool_analyses.models import StoolAnalysis, StoolAnalysisPhoto
from .models import StoolPhoto


class StoolPhotoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_active_owner(self, owner_user_id: UUID) -> bool:
        # Serialize upload with account deletion, which locks this same user row first.
        owner = await self.session.scalar(select(User).where(User.id == owner_user_id).with_for_update())
        return owner is not None and owner.status == "active" and owner.deleted_at is None

    async def get_for_owner(self, photo_id: UUID, owner_user_id: UUID) -> StoolPhoto | None:
        return cast(StoolPhoto | None, await self.session.scalar(select(StoolPhoto).where(
            StoolPhoto.id == photo_id, StoolPhoto.owner_user_id == owner_user_id,
            StoolPhoto.deleted_at.is_(None),
        )))

    async def get_including_deleted_for_owner(self, photo_id: UUID, owner_user_id: UUID) -> StoolPhoto | None:
        return cast(StoolPhoto | None, await self.session.scalar(
            select(StoolPhoto).where(StoolPhoto.id == photo_id, StoolPhoto.owner_user_id == owner_user_id).with_for_update()
        ))

    async def revoke_linked_analyses(self, *, owner_user_id: UUID, photo_id: UUID,
                                     at: datetime) -> None:
        # The delete and analysis tombstones commit in the same transaction. A
        # concurrent submit locks this same photo, so it cannot escape revocation.
        linked_ids = select(StoolAnalysisPhoto.analysis_id).where(
            StoolAnalysisPhoto.photo_id == photo_id,
        )
        await self.session.execute(
            update(StoolAnalysis).where(
                StoolAnalysis.owner_user_id == owner_user_id,
                StoolAnalysis.id.in_(linked_ids),
                StoolAnalysis.deleted_at.is_(None),
            ).values(
                revoked_at=at, deleted_at=at, final_result=None,
                result_schema_version=None, result_validated_at=None,
                context_expires_at=None,
                baby_context_snapshot_id=None, market=None, language=None,
                baby_profile_version=None, baby_birth_date=None,
                baby_feeding_mode=None, context_captured_at=None,
            )
        )
