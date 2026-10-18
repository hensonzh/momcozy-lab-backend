from __future__ import annotations

from typing import cast
from uuid import UUID, uuid5

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
from .models import ChatFirstOnboarding, OnboardingConfirmation
from .schemas import ChatFirstOnboardingInput, ChatFirstStateOutput, FamilyRole, OnboardingProfileInput, OnboardingStateOutput


# These map the ten selectable questions to the already-readable Me experience
# contract. Keep the selection IDs separate from the prose shown by a client.
CHAT_FIRST_CONCERNS = {
    "milk-supply": ("supply", "奶量够不够"),
    "feeding-enough": ("intake", "宝宝吃饱了吗"),
    "crying": ("other", "宝宝为什么哭"),
    "stool": ("other", "宝宝便便情况"),
    "baby-sleep": ("other", "宝宝睡得好吗"),
    "growth": ("other", "宝宝生长发育"),
    "recovery": ("comfort", "我的身体恢复"),
    "mood": ("comfort", "情绪起伏或压力大"),
    "fatigue": ("comfort", "睡不够、很疲惫"),
    "support": ("other", "照顾宝宝缺少帮手"),
}


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

    async def read_chat_first(self, owner_user_id: UUID) -> ChatFirstStateOutput:
        legacy = await self.session.get(OnboardingConfirmation, owner_user_id)
        if legacy is not None:
            return ChatFirstStateOutput(
                status="completed", can_enter_app=True, role="mother",
                profile_scope="legacy_full", primary_infant_id=legacy.primary_infant_id,
            )
        completion = await self.session.get(ChatFirstOnboarding, owner_user_id)
        if completion is not None:
            return ChatFirstStateOutput(
                status="completed", can_enter_app=True, role=cast(FamilyRole, completion.role),
                profile_scope="chat_first",
            )
        return ChatFirstStateOutput(status="required", can_enter_app=False, profile_scope="none")

    async def complete_chat_first(
        self, owner_user_id: UUID, payload: ChatFirstOnboardingInput, request_id: str
    ) -> ChatFirstStateOutput:
        # The owner lock serializes competing attempts and concurrent profile edits.
        owner = await self.session.scalar(
            select(User.id).where(
                User.id == owner_user_id, User.status == "active", User.deleted_at.is_(None)
            ).with_for_update()
        )
        if owner is None:
            raise ApiError(code="not_found", message="Account not found.", status=404)
        if await self.session.get(OnboardingConfirmation, owner_user_id) is not None:
            raise ApiError(
                code="version_conflict", message="Onboarding is already complete.", status=409,
            )
        digest = request_hash(payload.model_dump(mode="json"))
        existing = await self.session.get(ChatFirstOnboarding, owner_user_id)
        if existing is not None:
            if existing.request_hash != digest:
                raise ApiError(
                    code="version_conflict", message="Onboarding is already complete.", status=409,
                )
            return await self.read_chat_first(owner_user_id)

        if payload.role == "mother":
            await self._save_chat_first_mother(owner_user_id, payload)
        self.session.add(ChatFirstOnboarding(
            owner_user_id=owner_user_id, role=payload.role, request_hash=digest,
        ))
        await AuditService(repository=AuditRepository(self.session)).record(
            actor_user_id=owner_user_id,
            action="onboarding.chat_first.complete",
            resource_type="chat_first_onboarding",
            resource_id=str(owner_user_id),
            request_id=request_id,
            details={"role": payload.role},
        )
        await self.session.flush()
        return await self.read_chat_first(owner_user_id)

    async def _save_chat_first_mother(
        self, owner_user_id: UUID, payload: ChatFirstOnboardingInput
    ) -> None:
        # The schema requires these values for mothers. No invented preferred
        # name, feeding method, delivery method, or baby profile is written.
        assert payload.age is not None and payload.delivery_count is not None
        assert payload.delivery_date is not None and payload.gestation_weeks is not None
        assert payload.gestation_days is not None and payload.baby_count is not None
        user_profile = await self.session.scalar(
            select(UserProfile).where(UserProfile.user_id == owner_user_id)
        )
        if user_profile is None:
            user_profile = UserProfile(user_id=owner_user_id)
            self.session.add(user_profile)
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
                code="version_conflict", message="Current baby profiles already exist; edit your profile instead.", status=409,
            )
        if maternal is None:
            maternal = MaternalProfile(owner_user_id=owner_user_id)
            self.session.add(maternal)
        maternal.delivery_count = payload.delivery_count
        maternal.latest_delivery_date = payload.delivery_date
        preferences = await self.session.get(MePreferences, owner_user_id)
        if preferences is None:
            preferences = MePreferences(
                owner_user_id=owner_user_id, profile={}, concerns=[], record_order=[],
            )
            self.session.add(preferences)
        preferences.profile = {
            **preferences.profile,
            "gestation_weeks": payload.gestation_weeks,
            "gestation_days": payload.gestation_days,
            "baby_count": payload.baby_count,
        }
        selected = [
            {
                "id": str(uuid5(owner_user_id, "momcozy-onboarding:" + key)),
                "issues": [CHAT_FIRST_CONCERNS[key][0]],
                "note": CHAT_FIRST_CONCERNS[key][1],
                "reminder": False,
                "ended": False,
            }
            for key in payload.concerns
        ]
        existing_ids = {item["id"] for item in selected}
        previous = [item for item in preferences.concerns if item["id"] not in existing_ids]
        if len(previous) + len(selected) > 100:
            raise ApiError(code="validation_failed", message="Concern limit reached.", status=422)
        preferences.concerns = [*previous, *selected]

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
        chat_first = await self.session.get(ChatFirstOnboarding, owner_user_id)
        if chat_first is not None and chat_first.role != "mother":
            raise ApiError(
                code="version_conflict", message="This role has no maternal onboarding.", status=409,
            )
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
