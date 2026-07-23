from __future__ import annotations

from datetime import date
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import InfantProfile, UserProfile


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user_profile(self, *, user_id: UUID) -> UserProfile | None:
        statement = select(UserProfile).where(UserProfile.user_id == user_id)
        return cast(UserProfile | None, await self.session.scalar(statement))

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
        return cast(InfantProfile | None, await self.session.scalar(statement))

    async def create_infant(
        self,
        *,
        owner_user_id: UUID,
        name: str,
        sex_at_birth: str | None,
        birth_date: date | None,
    ) -> InfantProfile:
        infant = InfantProfile(
            owner_user_id=owner_user_id,
            name=name,
            sex_at_birth=sex_at_birth,
            birth_date=birth_date,
        )
        self.session.add(infant)
        await self.session.flush()
        return infant

    async def update_infant(self, *, infant: InfantProfile, values: dict[str, Any]) -> InfantProfile:
        for field, value in values.items():
            setattr(infant, field, value)
        await self.session.flush()
        return infant
