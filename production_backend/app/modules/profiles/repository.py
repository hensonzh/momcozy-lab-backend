from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import InfantProfile, UserProfile


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user_profile(self, *, user_id: UUID) -> UserProfile | None:
        statement = select(UserProfile).where(UserProfile.user_id == user_id)
        return await self.session.scalar(statement)

    async def upsert_user_profile(self, *, user_id: UUID, values: dict[str, Any]) -> UserProfile:
        profile = await self.get_user_profile(user_id=user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id)
            self.session.add(profile)

        for field, value in values.items():
            setattr(profile, field, value)
        await self.session.flush()
        return profile

    async def list_infants(self, *, owner_user_id: UUID) -> list[InfantProfile]:
        statement = (
            select(InfantProfile)
            .where(
                InfantProfile.owner_user_id == owner_user_id,
                InfantProfile.status == "active",
                InfantProfile.deleted_at.is_(None),
            )
            .order_by(InfantProfile.created_at.asc(), InfantProfile.id.asc())
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def get_infant_for_owner(self, *, infant_id: UUID, owner_user_id: UUID) -> InfantProfile | None:
        statement = select(InfantProfile).where(
            InfantProfile.id == infant_id,
            InfantProfile.owner_user_id == owner_user_id,
            InfantProfile.deleted_at.is_(None),
        )
        return await self.session.scalar(statement)

    async def create_infant(
        self,
        *,
        owner_user_id: UUID,
        infant_name: str,
        sex: str,
        birth_date: date | None,
    ) -> InfantProfile:
        infant = InfantProfile(
            owner_user_id=owner_user_id,
            infant_name=infant_name,
            sex=sex,
            birth_date=birth_date,
        )
        self.session.add(infant)
        await self.session.flush()
        return infant
