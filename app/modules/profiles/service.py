from __future__ import annotations

from ..baby.profile_models import BabyProfile

from datetime import date
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService
from .models import UserProfile
from .repository import ProfileRepository


USER_PROFILE_UPDATE_FIELDS = frozenset({"preferred_name", "age"})
INFANT_PROFILE_UPDATE_FIELDS = frozenset(
    {
        "name",
        "sex",
        "feeding_mode",
        "birth_date",
    }
)
BABY_SEX_VALUES = frozenset({"female", "male", "unspecified"})


class ProfileService:
    def __init__(
        self,
        *,
        repository: ProfileRepository,
        audit_service: AuditService | None = None,
        idempotency_service: IdempotencyService | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.idempotency_service = idempotency_service

    async def get_user_profile(self, *, user_id: UUID) -> UserProfile | None:
        return await self.repository.get_user_profile(user_id=user_id)

    async def update_user_profile(self, *, user_id: UUID, values: dict[str, Any], request_id: str = "") -> UserProfile:
        normalized_values = _normalize_user_profile_update(values)
        await self.repository.lock_profile_owner(owner_user_id=user_id)
        profile = await self.repository.upsert_user_profile(user_id=user_id, values=normalized_values)
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=user_id,
                action="profiles.user.update",
                resource_type="user_profile",
                resource_id=str(profile.id),
                request_id=request_id,
                details={"fields": sorted(normalized_values)},
            )
        return profile

    async def update_profile(
        self,
        *,
        user_id: UUID,
        user_values: dict[str, Any] | None = None,
        infant_updates: list[dict[str, Any]] | None = None,
        reference_date: date | None = None,
        request_id: str = "",
    ) -> tuple[UserProfile | None, list[BabyProfile]]:
        if not user_values and not infant_updates:
            raise ApiError(code="validation_failed", message="At least one profile update is required.", status=422)

        await self.repository.lock_profile_owner(owner_user_id=user_id)
        normalized_user_values = _normalize_user_profile_update(user_values) if user_values else {}
        prepared_infant_updates: list[tuple[BabyProfile, dict[str, Any]]] = []
        seen_infant_ids: set[UUID] = set()
        for update in infant_updates or []:
            if not isinstance(update, dict) or set(update) != {"infant_id", "values"}:
                raise ApiError(code="validation_failed", message="Infant update is invalid.", status=422)
            infant_id = update["infant_id"]
            if not isinstance(infant_id, UUID):
                raise ApiError(code="validation_failed", message="infant_id must be a UUID.", status=422)
            if infant_id in seen_infant_ids:
                raise ApiError(code="validation_failed", message="Each infant may be updated only once.", status=422)
            seen_infant_ids.add(infant_id)
            values = update["values"]
            if not isinstance(values, dict):
                raise ApiError(code="validation_failed", message="Infant update values are invalid.", status=422)
            normalized_values = _normalize_infant_profile_update(
                values,
                reference_date=reference_date or date.today(),
            )
            infant = await self.repository.get_infant_for_owner(
                infant_id=infant_id,
                owner_user_id=user_id,
            )
            if infant is None:
                raise ApiError(code="not_found", message="Infant profile was not found.", status=404)
            updated_birth_date = normalized_values.get("birth_date")
            if updated_birth_date is not None:
                maternal = await self.repository.get_maternal_profile(owner_user_id=user_id)
                if (
                    maternal is not None
                    and await self.repository.is_current_delivery_infant(
                        owner_user_id=user_id,
                        infant_id=infant_id,
                    )
                    and maternal.latest_delivery_date is not None
                    and maternal.latest_delivery_date != updated_birth_date
                ):
                    raise ApiError(
                        code="validation_failed",
                        message="birth_date must match the current actual_delivery_date.",
                        status=422,
                    )
            prepared_infant_updates.append((infant, normalized_values))

        profile = None
        if normalized_user_values:
            profile = await self.repository.upsert_user_profile(user_id=user_id, values=normalized_user_values)
        updated_infants = [await self.repository.update_infant(infant=infant, values=values) for infant, values in prepared_infant_updates]

        if self.audit_service is not None:
            details: dict[str, Any] = {}
            if normalized_user_values:
                details["user_fields"] = sorted(normalized_user_values)
            if prepared_infant_updates:
                details["infants"] = [{"infant_id": str(infant.id), "fields": sorted(values)} for infant, values in prepared_infant_updates]
            await self.audit_service.record(
                actor_user_id=user_id,
                action="profiles.update",
                resource_type="profile",
                resource_id=str(user_id),
                request_id=request_id,
                details=details,
            )
        return profile, updated_infants







def _normalize_user_profile_update(values: dict[str, Any]) -> dict[str, Any]:
    if not values:
        raise ApiError(code="validation_failed", message="At least one profile field is required.", status=422)
    unknown_fields = set(values) - USER_PROFILE_UPDATE_FIELDS
    if unknown_fields:
        raise ApiError(code="validation_failed", message="Unsupported profile field.", status=422)

    normalized = dict(values)
    if "preferred_name" in normalized:
        preferred_name = normalized["preferred_name"]
        if preferred_name is not None:
            if not isinstance(preferred_name, str) or not preferred_name.strip():
                raise ApiError(code="validation_failed", message="preferred_name must not be blank.", status=422)
            normalized["preferred_name"] = preferred_name.strip()
            if len(normalized["preferred_name"]) > 120:
                raise ApiError(code="validation_failed", message="preferred_name is too long.", status=422)

    if "age" in normalized:
        age = normalized["age"]
        if age is not None and (isinstance(age, bool) or not isinstance(age, int) or not 12 <= age <= 70):
            raise ApiError(code="validation_failed", message="age must be between 12 and 70.", status=422)

    return normalized


def _normalize_infant_profile_update(
    values: dict[str, Any],
    *,
    reference_date: date,
) -> dict[str, Any]:
    if not values:
        raise ApiError(code="validation_failed", message="At least one infant profile field is required.", status=422)
    if set(values) - INFANT_PROFILE_UPDATE_FIELDS:
        raise ApiError(code="validation_failed", message="Unsupported infant profile field.", status=422)

    normalized = dict(values)
    if "name" in normalized:
        name = normalized["name"]
        if not isinstance(name, str) or not name.strip():
            raise ApiError(code="validation_failed", message="name must not be blank.", status=422)
        normalized["name"] = name.strip()
        if len(normalized["name"]) > 120:
            raise ApiError(code="validation_failed", message="name is too long.", status=422)

    if "sex" in normalized:
        sex = normalized["sex"]
        if not isinstance(sex, str) or sex not in BABY_SEX_VALUES:
            raise ApiError(code="validation_failed", message="sex is invalid.", status=422)

    if "feeding_mode" in normalized and normalized["feeding_mode"] not in {"exclusive_breastfeeding", "expressed_milk_feeding", "mixed_feeding", "formula_feeding", "unknown"}:
        raise ApiError(code="validation_failed", message="feeding_mode is invalid.", status=422)

    if "birth_date" in normalized:
        birth_date = normalized["birth_date"]
        if birth_date is not None and not isinstance(birth_date, date):
            raise ApiError(code="validation_failed", message="birth_date must be a date.", status=422)
        if birth_date is not None and birth_date > reference_date:
            raise ApiError(code="validation_failed", message="birth_date must not be in the future.", status=422)



    return normalized
