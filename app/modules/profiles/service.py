from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import InfantProfile, UserProfile
from .repository import ProfileRepository


INFANT_CREATE_IDEMPOTENCY_SCOPE = "profiles.infants.create"
USER_PROFILE_UPDATE_FIELDS = frozenset({"preferred_name", "age", "estimated_due_date"})
INFANT_PROFILE_UPDATE_FIELDS = frozenset(
    {
        "name",
        "sex_at_birth",
        "birth_date",
        "birth_weight_kg",
        "gestational_age_at_birth_days",
    }
)
SEX_AT_BIRTH_VALUES = frozenset({"female", "male", "intersex", "unknown", "undisclosed"})


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
        request_id: str = "",
    ) -> tuple[UserProfile | None, list[InfantProfile]]:
        if not user_values and not infant_updates:
            raise ApiError(code="validation_failed", message="At least one profile update is required.", status=422)

        normalized_user_values = _normalize_user_profile_update(user_values) if user_values else {}
        prepared_infant_updates: list[tuple[InfantProfile, dict[str, Any]]] = []
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
            normalized_values = _normalize_infant_profile_update(values)
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

    async def list_infants(self, *, owner_user_id: UUID) -> list[InfantProfile]:
        return await self.repository.list_infants(owner_user_id=owner_user_id)

    async def create_infant(
        self,
        *,
        owner_user_id: UUID,
        name: str,
        sex_at_birth: str | None = None,
        birth_date: date | None = None,
        birth_weight_kg: float | None = None,
        gestational_age_at_birth_days: int | None = None,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> InfantProfile:
        idempotency_record = None
        normalized_name = name.strip()
        if not normalized_name:
            raise ApiError(code="validation_failed", message="name is required.", status=422)
        if len(normalized_name) > 120:
            raise ApiError(code="validation_failed", message="name is too long.", status=422)
        if sex_at_birth is not None and sex_at_birth not in SEX_AT_BIRTH_VALUES:
            raise ApiError(code="validation_failed", message="sex_at_birth is invalid.", status=422)
        if birth_date is not None and birth_date > date.today():
            raise ApiError(code="validation_failed", message="birth_date must not be in the future.", status=422)
        _validate_birth_weight(birth_weight_kg)
        _validate_gestational_age(gestational_age_at_birth_days)

        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=INFANT_CREATE_IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash(
                    {
                        "name": normalized_name,
                        "sex_at_birth": sex_at_birth,
                        "birth_date": str(birth_date or ""),
                        "birth_weight_kg": birth_weight_kg,
                        "gestational_age_at_birth_days": gestational_age_at_birth_days,
                    }
                ),
                expires_at=_idempotency_expires_at(),
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                return await self._replay_create_infant(
                    owner_user_id=owner_user_id,
                    response_ref=idempotency_record.response_ref,
                )

        infant = await self.repository.create_infant(
            owner_user_id=owner_user_id,
            name=normalized_name,
            sex_at_birth=sex_at_birth,
            birth_date=birth_date,
            birth_weight_kg=birth_weight_kg,
            gestational_age_at_birth_days=gestational_age_at_birth_days,
        )
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(infant.id))
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="profiles.infant.create",
                resource_type="infant_profile",
                resource_id=str(infant.id),
                request_id=request_id,
                details={"name": normalized_name},
            )
        return infant

    async def _replay_create_infant(self, *, owner_user_id: UUID, response_ref: str) -> InfantProfile:
        infant_id = parse_idempotency_response_ref(response_ref)
        infant = await self.repository.get_infant_for_owner(infant_id=infant_id, owner_user_id=owner_user_id)
        if infant is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return infant


def _idempotency_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=24)


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

    if "estimated_due_date" in normalized:
        estimated_due_date = normalized["estimated_due_date"]
        if estimated_due_date is not None and not isinstance(estimated_due_date, date):
            raise ApiError(code="validation_failed", message="estimated_due_date must be a date.", status=422)

    return normalized


def _normalize_infant_profile_update(values: dict[str, Any]) -> dict[str, Any]:
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

    if "sex_at_birth" in normalized:
        sex_at_birth = normalized["sex_at_birth"]
        if sex_at_birth is not None and (not isinstance(sex_at_birth, str) or sex_at_birth not in SEX_AT_BIRTH_VALUES):
            raise ApiError(code="validation_failed", message="sex_at_birth is invalid.", status=422)

    if "birth_date" in normalized:
        birth_date = normalized["birth_date"]
        if birth_date is not None and not isinstance(birth_date, date):
            raise ApiError(code="validation_failed", message="birth_date must be a date.", status=422)
        if birth_date is not None and birth_date > date.today():
            raise ApiError(code="validation_failed", message="birth_date must not be in the future.", status=422)

    if "birth_weight_kg" in normalized:
        _validate_birth_weight(normalized["birth_weight_kg"])

    if "gestational_age_at_birth_days" in normalized:
        _validate_gestational_age(normalized["gestational_age_at_birth_days"])

    return normalized


def _validate_birth_weight(value: Any) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.2 <= value <= 10:
        raise ApiError(
            code="validation_failed",
            message="birth_weight_kg must be between 0.2 and 10.",
            status=422,
        )


def _validate_gestational_age(value: Any) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 140 <= value <= 315:
        raise ApiError(
            code="validation_failed",
            message="gestational_age_at_birth_days must be between 140 and 315.",
            status=422,
        )
