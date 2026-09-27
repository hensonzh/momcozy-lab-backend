from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, request_hash
from ..baby.profile_models import BabyProfile
from ..profiles.models import LactationProfile, MaternalProfile, UserProfile, MaternalCurrentDeliveryInfant
from ..profiles.me_models import MePreferences
from ..profiles.feeding_methods import feeding_mode_for_methods
from ..users.models import User
from .models import OnboardingConfirmation
from .schemas import OnboardingProfileInput, OnboardingStateOutput


class OnboardingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(self, owner_user_id: UUID) -> OnboardingStateOutput:
        confirmation = await self.session.get(OnboardingConfirmation, owner_user_id)
        if confirmation is None:
            return OnboardingStateOutput(status="required", profile_confirmed=False)
        return OnboardingStateOutput(
            status="completed", profile_confirmed=True,
            primary_infant_id=confirmation.primary_infant_id,
        )

    async def confirm(
        self, owner_user_id: UUID, payload: OnboardingProfileInput, request_id: str
    ) -> OnboardingStateOutput:
        # Serialize concurrent retries and all profile writes under the same user row.
        owner = await self.session.scalar(
            select(User.id).where(
                User.id == owner_user_id, User.status == "active", User.deleted_at.is_(None)
            ).with_for_update()
        )
        if owner is None:
            raise ApiError(code="not_found", message="Account not found.", status=404)
        digest = request_hash(payload.model_dump(mode="json"))
        existing = await self.session.get(OnboardingConfirmation, owner_user_id)
        if existing is not None:
            if existing.request_hash != digest:
                raise ApiError(
                    code="version_conflict",
                    message="Onboarding is already complete; edit your profile instead.",
                    status=409,
                )
            return await self.read(owner_user_id)

        user_profile = await self.session.scalar(
            select(UserProfile).where(UserProfile.user_id == owner_user_id)
        )
        if user_profile is None:
            user_profile = UserProfile(user_id=owner_user_id)
            self.session.add(user_profile)
        user_profile.preferred_name = payload.display_name
        user_profile.age = payload.age

        maternal = await self.session.scalar(
            select(MaternalProfile).where(MaternalProfile.owner_user_id == owner_user_id)
        )
        if maternal is not None and await self.session.scalar(
            select(MaternalCurrentDeliveryInfant.infant_id)
            .where(MaternalCurrentDeliveryInfant.maternal_profile_id == maternal.id)
            .limit(1)
        ) is not None:
            raise ApiError(
                code="version_conflict",
                message="Current baby profiles already exist; edit your profile instead.",
                status=409,
            )
        if maternal is None:
            maternal = MaternalProfile(owner_user_id=owner_user_id)
            self.session.add(maternal)
        maternal.delivery_count = payload.delivery_count
        maternal.latest_delivery_date = payload.delivery_date
        maternal.latest_delivery_method = (
            "assisted_vaginal" if payload.delivery_type == "assisted" else payload.delivery_type
        )
        maternal.has_cesarean_history = (
            False if payload.delivery_count == 1 else payload.has_cesarean_history
        )
        preferences = await self.session.get(MePreferences, owner_user_id)
        if preferences is None:
            preferences = MePreferences(
                owner_user_id=owner_user_id, profile={}, concerns=[], record_order=[]
            )
            self.session.add(preferences)
        feeding_mode = feeding_mode_for_methods(payload.feeding_methods)
        preferences.profile = {
            **preferences.profile,
            "gestation_weeks": payload.gestation_weeks,
            "gestation_days": payload.gestation_days,
            "baby_count": payload.infant_count,
            "feeding_methods": payload.feeding_methods,
        }
        lactation = await self.session.scalar(
            select(LactationProfile).where(LactationProfile.owner_user_id == owner_user_id)
        )
        if lactation is None:
            lactation = LactationProfile(owner_user_id=owner_user_id)
            self.session.add(lactation)
        lactation.current_feeding_mode = feeding_mode
        await self.session.flush()

        infants = [
            BabyProfile(
                owner_user_id=owner_user_id,
                name=infant.nickname or f"Baby {index}",
                sex=infant.sex or "unspecified",
                birth_date=payload.delivery_date,
                feeding_mode=feeding_mode,
            )
            for index, infant in enumerate(payload.infants, start=1)
        ]
        self.session.add_all(infants)
        await self.session.flush()
        self.session.add_all(
            MaternalCurrentDeliveryInfant(
                maternal_profile_id=maternal.id, infant_id=infant.id, birth_order=index
            )
            for index, infant in enumerate(infants, start=1)
        )
        self.session.add(
            OnboardingConfirmation(
                owner_user_id=owner_user_id,
                primary_infant_id=infants[0].id,
                request_hash=digest,
            )
        )
        await AuditService(repository=AuditRepository(self.session)).record(
            actor_user_id=owner_user_id,
            action="onboarding.profile.confirm",
            resource_type="onboarding_confirmation",
            resource_id=str(owner_user_id),
            request_id=request_id,
            details={"infant_count": len(infants)},
        )
        await self.session.flush()
        return OnboardingStateOutput(
            status="completed", profile_confirmed=True, primary_infant_id=infants[0].id
        )
