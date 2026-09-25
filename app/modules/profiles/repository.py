from __future__ import annotations

from ..baby.profile_models import BabyProfile

from dataclasses import dataclass
from datetime import date
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..users.models import User
from .me_models import MePreferences
from .models import (
    LactationProfile,
    MaternalCurrentDeliveryInfant,
    MaternalProfile,
    UserProfile,
)


@dataclass(frozen=True)
class LactationMotherContext:
    preferred_name: str | None
    age: int | None
    delivery_count: int | None
    latest_delivery_method: str | None
    latest_delivery_date: date | None
    has_cesarean_history: bool | None
    current_feeding_mode: str | None


@dataclass(frozen=True)
class LactationInfantContext:
    infant_id: UUID
    name: str
    sex: str
    feeding_mode: str = "unknown"
    birth_date: date | None = None


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_profile_owner(self, *, owner_user_id: UUID) -> None:
        await self.session.scalar(
            select(User.id)
            .where(User.id == owner_user_id)
            .with_for_update()
        )

    async def get_me_preferences(self, *, owner_user_id: UUID) -> MePreferences | None:
        return await self.session.get(MePreferences, owner_user_id)

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

    async def get_maternal_profile(
        self,
        *,
        owner_user_id: UUID,
    ) -> MaternalProfile | None:
        statement = select(MaternalProfile).where(MaternalProfile.owner_user_id == owner_user_id)
        return cast(
            MaternalProfile | None,
            await self.session.scalar(statement),
        )

    async def upsert_maternal_profile(
        self,
        *,
        owner_user_id: UUID,
        values: dict[str, Any],
    ) -> MaternalProfile:
        profile = await self.get_maternal_profile(owner_user_id=owner_user_id)
        if profile is None:
            profile = MaternalProfile(owner_user_id=owner_user_id)
            self.session.add(profile)
        for field, value in values.items():
            setattr(profile, field, value)
        await self.session.flush()
        return profile

    async def get_lactation_profile(
        self,
        *,
        owner_user_id: UUID,
    ) -> LactationProfile | None:
        statement = select(LactationProfile).where(LactationProfile.owner_user_id == owner_user_id)
        return cast(
            LactationProfile | None,
            await self.session.scalar(statement),
        )

    async def upsert_lactation_profile(
        self,
        *,
        owner_user_id: UUID,
        values: dict[str, Any],
    ) -> LactationProfile:
        profile = await self.get_lactation_profile(owner_user_id=owner_user_id)
        if profile is None:
            profile = LactationProfile(owner_user_id=owner_user_id)
            self.session.add(profile)
        for field, value in values.items():
            setattr(profile, field, value)
        await self.session.flush()
        return profile

    async def list_current_delivery_infants(
        self,
        *,
        owner_user_id: UUID,
    ) -> list[tuple[MaternalCurrentDeliveryInfant, BabyProfile]]:
        statement = (
            select(MaternalCurrentDeliveryInfant, BabyProfile)
            .join(
                MaternalProfile,
                MaternalProfile.id == MaternalCurrentDeliveryInfant.maternal_profile_id,
            )
            .join(
                BabyProfile,
                BabyProfile.id == MaternalCurrentDeliveryInfant.infant_id,
            )
            .where(
                MaternalProfile.owner_user_id == owner_user_id,
                BabyProfile.owner_user_id == owner_user_id,
                BabyProfile.deleted_at.is_(None),
            )
            .order_by(
                MaternalCurrentDeliveryInfant.birth_order.asc(),
                MaternalCurrentDeliveryInfant.infant_id.asc(),
            )
        )
        result = await self.session.execute(statement)
        return [
            (
                cast(MaternalCurrentDeliveryInfant, row[0]),
                cast(BabyProfile, row[1]),
            )
            for row in result.all()
        ]

    async def replace_current_delivery_infants(
        self,
        *,
        profile: MaternalProfile,
        current_infants: list[dict[str, Any]],
    ) -> None:
        await self.session.execute(
            delete(MaternalCurrentDeliveryInfant).where(MaternalCurrentDeliveryInfant.maternal_profile_id == profile.id)
        )
        self.session.add_all(
            [
                MaternalCurrentDeliveryInfant(
                    maternal_profile_id=profile.id,
                    infant_id=item["infant_id"],
                    birth_order=item["birth_order"],
                )
                for item in current_infants
            ]
        )
        await self.session.flush()

    async def is_current_delivery_infant(
        self,
        *,
        owner_user_id: UUID,
        infant_id: UUID,
    ) -> bool:
        statement = (
            select(MaternalCurrentDeliveryInfant.infant_id)
            .join(
                MaternalProfile,
                MaternalProfile.id == MaternalCurrentDeliveryInfant.maternal_profile_id,
            )
            .join(BabyProfile, BabyProfile.id == MaternalCurrentDeliveryInfant.infant_id)
            .where(
                MaternalProfile.owner_user_id == owner_user_id,
                BabyProfile.owner_user_id == owner_user_id,
                BabyProfile.deleted_at.is_(None),
                MaternalCurrentDeliveryInfant.infant_id == infant_id,
            )
        )
        return await self.session.scalar(statement) is not None

    async def get_lactation_mother_context(
        self,
        *,
        owner_user_id: UUID,
    ) -> LactationMotherContext:
        statement = (
            select(
                UserProfile.preferred_name.label("preferred_name"),
                UserProfile.age.label("age"),
                MaternalProfile.delivery_count.label("delivery_count"),
                MaternalProfile.latest_delivery_method.label("latest_delivery_method"),
                MaternalProfile.latest_delivery_date.label("latest_delivery_date"),
                MaternalProfile.has_cesarean_history.label("has_cesarean_history"),
                LactationProfile.current_feeding_mode.label("current_feeding_mode"),
            )
            .select_from(User)
            .outerjoin(UserProfile, UserProfile.user_id == User.id)
            .outerjoin(MaternalProfile, MaternalProfile.owner_user_id == User.id)
            .outerjoin(LactationProfile, LactationProfile.owner_user_id == User.id)
            .where(User.id == owner_user_id)
        )
        row = (await self.session.execute(statement)).one_or_none()
        if row is None:
            return LactationMotherContext(
                preferred_name=None,
                age=None,
                delivery_count=None,
                latest_delivery_method=None,
                latest_delivery_date=None,
                has_cesarean_history=None,
                current_feeding_mode=None,
            )
        return LactationMotherContext(
            preferred_name=row.preferred_name,
            age=row.age,
            delivery_count=row.delivery_count,
            latest_delivery_method=row.latest_delivery_method,
            latest_delivery_date=row.latest_delivery_date,
            has_cesarean_history=row.has_cesarean_history,
            current_feeding_mode=row.current_feeding_mode,
        )

    async def list_current_delivery_infant_contexts(
        self,
        *,
        owner_user_id: UUID,
    ) -> list[tuple[int, LactationInfantContext]]:
        statement = (
            select(
                MaternalCurrentDeliveryInfant.birth_order.label("birth_order"),
                BabyProfile.id.label("infant_id"),
                BabyProfile.name.label("name"),
                BabyProfile.sex.label("sex"),
                BabyProfile.feeding_mode.label("feeding_mode"),
                BabyProfile.birth_date.label("birth_date"),
            )
            .join(
                MaternalProfile,
                MaternalProfile.id == MaternalCurrentDeliveryInfant.maternal_profile_id,
            )
            .join(
                BabyProfile,
                BabyProfile.id == MaternalCurrentDeliveryInfant.infant_id,
            )
            .where(
                MaternalProfile.owner_user_id == owner_user_id,
                BabyProfile.owner_user_id == owner_user_id,
                BabyProfile.deleted_at.is_(None),
            )
            .order_by(MaternalCurrentDeliveryInfant.birth_order.asc())
        )
        rows = (await self.session.execute(statement)).all()
        return [
            (
                row.birth_order,
                LactationInfantContext(
                    infant_id=row.infant_id,
                    name=row.name,
                    sex=row.sex,
                    feeding_mode=row.feeding_mode,
                    birth_date=row.birth_date,
                ),
            )
            for row in rows
        ]

    async def list_infant_context_candidates(
        self,
        *,
        owner_user_id: UUID,
        limit: int = 2,
    ) -> list[LactationInfantContext]:
        statement = (
            select(
                BabyProfile.id.label("infant_id"),
                BabyProfile.name.label("name"),
                BabyProfile.sex.label("sex"),
                BabyProfile.feeding_mode.label("feeding_mode"),
                BabyProfile.birth_date.label("birth_date"),
            )
            .where(
                BabyProfile.owner_user_id == owner_user_id,
                BabyProfile.deleted_at.is_(None),
            )
            .order_by(BabyProfile.created_at.asc(), BabyProfile.id.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            LactationInfantContext(
                infant_id=row.infant_id,
                name=row.name,
                sex=row.sex,
                    feeding_mode=row.feeding_mode,
                birth_date=row.birth_date,
            )
            for row in rows
        ]

    async def list_all_infant_contexts(
        self,
        *,
        owner_user_id: UUID,
    ) -> list[LactationInfantContext]:
        statement = (
            select(
                BabyProfile.id.label("infant_id"),
                BabyProfile.name.label("name"),
                BabyProfile.sex.label("sex"),
                BabyProfile.feeding_mode.label("feeding_mode"),
                BabyProfile.birth_date.label("birth_date"),
            )
            .where(
                BabyProfile.owner_user_id == owner_user_id,
                BabyProfile.deleted_at.is_(None),
            )
            .order_by(BabyProfile.created_at.asc(), BabyProfile.id.asc())
        )
        rows = (await self.session.execute(statement)).all()
        return [
            LactationInfantContext(
                infant_id=row.infant_id,
                name=row.name,
                sex=row.sex,
                    feeding_mode=row.feeding_mode,
                birth_date=row.birth_date,
            )
            for row in rows
        ]


    async def get_infant_for_owner(self, *, infant_id: UUID, owner_user_id: UUID) -> BabyProfile | None:
        statement = select(BabyProfile).where(
            BabyProfile.id == infant_id,
            BabyProfile.owner_user_id == owner_user_id,
            BabyProfile.deleted_at.is_(None),
        )
        return cast(BabyProfile | None, await self.session.scalar(statement))


    async def update_infant(self, *, infant: BabyProfile, values: dict[str, Any]) -> BabyProfile:
        for field, value in values.items():
            setattr(infant, field, value)
        infant.version += 1
        await self.session.flush()
        return infant
