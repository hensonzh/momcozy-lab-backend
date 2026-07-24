from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService
from ..records.service import RecordsService
from .models import (
    InfantProfile,
    LactationProfile,
    MaternalProfile,
)
from .lactation_context_schema import MaternalInfantProfileReadOutput
from .repository import (
    LactationInfantContext,
    ProfileRepository,
)


MATERNAL_LACTATION_UPDATE_FIELDS = frozenset(
    {
        "current_infants",
        "delivery_count",
        "current_delivery_method",
        "actual_delivery_date",
        "has_cesarean_history",
        "current_feeding_mode",
    }
)
DELIVERY_METHOD_VALUES = frozenset({"vaginal", "cesarean", "assisted_vaginal", "other", "unknown"})
FEEDING_MODE_VALUES = frozenset(
    {
        "exclusive_breastfeeding",
        "expressed_milk_feeding",
        "mixed_feeding",
        "formula_feeding",
        "unknown",
    }
)


@dataclass(frozen=True)
class MaternalLactationProfileView:
    delivery_count: int | None
    current_delivery_method: str | None
    actual_delivery_date: date | None
    has_cesarean_history: bool | None
    current_feeding_mode: str | None


class LactationContextService:
    """Reads the compact, database-backed context used by lactation analysis."""

    def __init__(
        self,
        *,
        profile_repository: ProfileRepository,
        records_service: RecordsService,
        audit_service: AuditService | None = None,
    ) -> None:
        self.profile_repository = profile_repository
        self.records_service = records_service
        self.audit_service = audit_service

    async def get_maternal_profile(
        self,
        *,
        owner_user_id: UUID,
    ) -> tuple[MaternalLactationProfileView | None, list[dict[str, Any]]]:
        maternal = await self.profile_repository.get_maternal_profile(owner_user_id=owner_user_id)
        lactation = await self.profile_repository.get_lactation_profile(owner_user_id=owner_user_id)
        current_infants = await self.profile_repository.list_current_delivery_infants(owner_user_id=owner_user_id)
        return _profile_view(maternal=maternal, lactation=lactation), [
            {
                "infant_id": link.infant_id,
                "birth_order": link.birth_order,
            }
            for link, _infant in current_infants
        ]

    async def read(
        self,
        *,
        owner_user_id: UUID,
        as_of_date: date | None = None,
        infant_scope: Literal["current_delivery", "all"] = "current_delivery",
    ) -> dict[str, Any]:
        if infant_scope not in {"current_delivery", "all"}:
            raise ApiError(
                code="validation_failed",
                message="infant_scope must be current_delivery or all.",
                status=422,
            )
        today = as_of_date or date.today()
        maternal = await self.profile_repository.get_lactation_mother_context(owner_user_id=owner_user_id)
        current_infants, infant_issues = await self._current_infants(
            owner_user_id=owner_user_id,
        )
        current_by_id = {
            infant.infant_id: birth_order
            for birth_order, infant in current_infants
        }
        selected_infants: list[tuple[int | None, LactationInfantContext, bool]]
        if infant_scope == "all":
            all_infants = await self.profile_repository.list_all_infant_contexts(
                owner_user_id=owner_user_id,
            )
            selected_infants = [
                (
                    current_by_id.get(infant.infant_id),
                    infant,
                    infant.infant_id in current_by_id,
                )
                for infant in all_infants
            ]
        else:
            selected_infants = [
                (birth_order, infant, True)
                for birth_order, infant in current_infants
            ]

        delivery_date = maternal.latest_delivery_date
        has_actual_birth_date = any(
            infant.birth_date is not None
            for _, infant in current_infants
        )
        postpartum_days = _elapsed_days(delivery_date, today=today)
        infant_contexts: list[dict[str, Any]] = []
        issues = list(infant_issues)
        latest_growth_by_infant = await self.records_service.list_latest_growth_by_infant_ids(
            owner_user_id=owner_user_id,
            infant_ids=[infant.infant_id for _, infant, _ in selected_infants],
        )
        for birth_order, infant, is_current_delivery in selected_infants:
            infant_birth_date = infant.birth_date
            infant_age_reference = (
                delivery_date or infant_birth_date
                if is_current_delivery
                else infant_birth_date
            )
            latest_growth = latest_growth_by_infant.get(infant.infant_id)
            infant_contexts.append(
                {
                    "infant_id": str(infant.infant_id),
                    "name": infant.name,
                    "is_current_delivery": is_current_delivery,
                    "birth_order": birth_order,
                    "sex_at_birth": infant.sex_at_birth,
                    "birth_date": (
                        infant_birth_date.isoformat()
                        if infant_birth_date is not None
                        else None
                    ),
                    "age_days": _elapsed_days(infant_age_reference, today=today),
                    "age_months": _elapsed_calendar_months(
                        infant_age_reference,
                        today=today,
                    ),
                    "birth_weight_kg": infant.birth_weight_kg,
                    "gestational_age_at_birth": _gestational_age(infant.gestational_age_at_birth_days),
                    "latest_measurement": _latest_measurement(latest_growth),
                }
            )
            if (
                is_current_delivery
                and delivery_date is not None
                and infant_birth_date is not None
                and delivery_date != infant_birth_date
            ):
                issues.append(
                    {
                        "code": "infant_birth_date_mismatch",
                        "birth_order": birth_order,
                    }
                )
            if infant_birth_date is not None and infant_birth_date > today:
                issues.append(
                    {
                        "code": "infant_birth_date_is_in_future",
                        "birth_order": birth_order,
                    }
                )

        mother = {
            "preferred_name": maternal.preferred_name,
            "age": maternal.age,
            "estimated_due_date": (
                maternal.estimated_due_date.isoformat()
                if (
                    delivery_date is None
                    and not has_actual_birth_date
                    and maternal.estimated_due_date is not None
                )
                else None
            ),
            "delivery_count": maternal.delivery_count,
            "current_delivery_method": maternal.latest_delivery_method,
            "actual_delivery_date": (delivery_date.isoformat() if delivery_date is not None else None),
            "has_cesarean_history": maternal.has_cesarean_history,
            "postpartum_days": postpartum_days,
            "current_feeding_mode": maternal.current_feeding_mode,
        }
        if delivery_date is not None and delivery_date > today:
            issues.append(
                {
                    "code": "actual_delivery_date_is_in_future",
                    "birth_order": None,
                }
            )

        return MaternalInfantProfileReadOutput.model_validate(
            {
                "as_of_date": today,
                "infant_scope": infant_scope,
                "mother": mother,
                "infants": infant_contexts,
                "missing_fields": _missing_fields(
                    mother=mother,
                    infants=[
                        infant
                        for infant in infant_contexts
                        if infant["is_current_delivery"]
                    ],
                ),
                "data_quality_issues": issues,
            }
        ).model_dump(mode="json")

    async def update_maternal_profile(
        self,
        *,
        owner_user_id: UUID,
        values: dict[str, Any],
        anticipated_infant_birth_dates: dict[UUID, date | None] | None = None,
        request_id: str = "",
    ) -> tuple[MaternalLactationProfileView, list[dict[str, Any]]]:
        normalized = _normalize_maternal_update(values)
        existing_maternal = await self.profile_repository.get_maternal_profile(owner_user_id=owner_user_id)
        existing_lactation = await self.profile_repository.get_lactation_profile(owner_user_id=owner_user_id)
        existing_current_infants = await self.profile_repository.list_current_delivery_infants(owner_user_id=owner_user_id)
        current_infants = (
            normalized["current_infants"]
            if "current_infants" in normalized
            else [
                {
                    "infant_id": link.infant_id,
                    "birth_order": link.birth_order,
                }
                for link, _infant in existing_current_infants
            ]
        )
        resolved_infants: list[InfantProfile] = []
        for current_infant in current_infants:
            infant = await self.profile_repository.get_infant_for_owner(
                infant_id=current_infant["infant_id"],
                owner_user_id=owner_user_id,
            )
            if infant is None:
                raise ApiError(
                    code="not_found",
                    message="Current infant profile was not found.",
                    status=404,
                )
            resolved_infants.append(infant)
        delivery_date = (
            normalized["actual_delivery_date"]
            if "actual_delivery_date" in normalized
            else existing_maternal.latest_delivery_date
            if existing_maternal is not None
            else None
        )
        anticipated_birth_dates = anticipated_infant_birth_dates or {}
        for infant in resolved_infants:
            infant_birth_date = (
                anticipated_birth_dates[infant.id]
                if infant.id in anticipated_birth_dates
                else infant.birth_date
            )
            if (
                delivery_date is not None
                and infant_birth_date is not None
                and delivery_date != infant_birth_date
            ):
                raise ApiError(
                    code="validation_failed",
                    message="actual_delivery_date must match the current infant birth_date.",
                    status=422,
                )
        maternal_values: dict[str, Any] = {}
        if "delivery_count" in normalized:
            maternal_values["delivery_count"] = normalized["delivery_count"]
        if "current_delivery_method" in normalized:
            maternal_values["latest_delivery_method"] = normalized["current_delivery_method"]
        if "actual_delivery_date" in normalized:
            maternal_values["latest_delivery_date"] = normalized["actual_delivery_date"]
        if "has_cesarean_history" in normalized:
            maternal_values["has_cesarean_history"] = normalized["has_cesarean_history"]

        maternal_profile = existing_maternal
        if maternal_values or "current_infants" in normalized:
            maternal_profile = await self.profile_repository.upsert_maternal_profile(
                owner_user_id=owner_user_id,
                values=maternal_values,
            )

        lactation_profile = existing_lactation
        if "current_feeding_mode" in normalized:
            lactation_profile = await self.profile_repository.upsert_lactation_profile(
                owner_user_id=owner_user_id,
                values={"current_feeding_mode": normalized["current_feeding_mode"]},
            )
        if "current_infants" in normalized:
            if maternal_profile is not None:
                await self.profile_repository.replace_current_delivery_infants(
                    profile=maternal_profile,
                    current_infants=current_infants,
                )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="profiles.maternal_lactation.update",
                resource_type="lactation_context",
                resource_id=str(owner_user_id),
                request_id=request_id,
                details={"fields": sorted(normalized)},
            )
        view = _profile_view(
            maternal=maternal_profile,
            lactation=lactation_profile,
        )
        if view is None:
            raise RuntimeError("Lactation profile update did not persist any fields.")
        return view, current_infants

    async def _current_infants(
        self,
        *,
        owner_user_id: UUID,
    ) -> tuple[list[tuple[int, LactationInfantContext]], list[dict[str, Any]]]:
        current = await self.profile_repository.list_current_delivery_infant_contexts(owner_user_id=owner_user_id)
        issues: list[dict[str, Any]] = []
        if current:
            return current, issues

        infants = await self.profile_repository.list_infant_context_candidates(
            owner_user_id=owner_user_id,
            limit=2,
        )
        if len(infants) == 1:
            return [(1, infants[0])], issues
        if len(infants) > 1:
            issues.append(
                {
                    "code": "current_infants_not_selected",
                    "birth_order": None,
                }
            )
        return [], issues


def _normalize_maternal_update(values: dict[str, Any]) -> dict[str, Any]:
    if not values:
        raise ApiError(
            code="validation_failed",
            message="At least one maternal lactation field is required.",
            status=422,
        )
    if set(values) - MATERNAL_LACTATION_UPDATE_FIELDS:
        raise ApiError(
            code="validation_failed",
            message="Unsupported maternal lactation field.",
            status=422,
        )

    normalized = dict(values)
    if "current_infants" in normalized:
        normalized["current_infants"] = _normalize_current_infants(normalized["current_infants"])
    delivery_count = normalized.get("delivery_count")
    if delivery_count is not None and (
        isinstance(delivery_count, bool) or not isinstance(delivery_count, int) or not 1 <= delivery_count <= 20
    ):
        raise ApiError(
            code="validation_failed",
            message="delivery_count must be between 1 and 20.",
            status=422,
        )
    delivery_method = normalized.get("current_delivery_method")
    if delivery_method is not None and delivery_method not in DELIVERY_METHOD_VALUES:
        raise ApiError(
            code="validation_failed",
            message="current_delivery_method is invalid.",
            status=422,
        )
    actual_delivery_date = normalized.get("actual_delivery_date")
    if actual_delivery_date is not None and (not isinstance(actual_delivery_date, date) or actual_delivery_date > date.today()):
        raise ApiError(
            code="validation_failed",
            message="actual_delivery_date must not be in the future.",
            status=422,
        )
    cesarean_history = normalized.get("has_cesarean_history")
    if cesarean_history is not None and not isinstance(cesarean_history, bool):
        raise ApiError(
            code="validation_failed",
            message="has_cesarean_history must be a boolean or null.",
            status=422,
        )
    feeding_mode = normalized.get("current_feeding_mode")
    if feeding_mode is not None and feeding_mode not in FEEDING_MODE_VALUES:
        raise ApiError(
            code="validation_failed",
            message="current_feeding_mode is invalid.",
            status=422,
        )
    return normalized


def _normalize_current_infants(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 10:
        raise ApiError(
            code="validation_failed",
            message="current_infants must be a list with at most 10 items.",
            status=422,
        )
    normalized: list[dict[str, Any]] = []
    infant_ids: set[UUID] = set()
    birth_orders: set[int] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"infant_id", "birth_order"}:
            raise ApiError(
                code="validation_failed",
                message="Each current infant link is invalid.",
                status=422,
            )
        infant_id = item["infant_id"]
        birth_order = item["birth_order"]
        if not isinstance(infant_id, UUID):
            raise ApiError(
                code="validation_failed",
                message="current infant_id must be a UUID.",
                status=422,
            )
        if isinstance(birth_order, bool) or not isinstance(birth_order, int) or not 1 <= birth_order <= 10:
            raise ApiError(
                code="validation_failed",
                message="birth_order must be between 1 and 10.",
                status=422,
            )
        if infant_id in infant_ids or birth_order in birth_orders:
            raise ApiError(
                code="validation_failed",
                message="Current infant IDs and birth orders must be unique.",
                status=422,
            )
        infant_ids.add(infant_id)
        birth_orders.add(birth_order)
        normalized.append(
            {
                "infant_id": infant_id,
                "birth_order": birth_order,
            }
        )
    if sorted(birth_orders) != list(range(1, len(normalized) + 1)):
        raise ApiError(
            code="validation_failed",
            message="birth_order must be contiguous starting at 1.",
            status=422,
        )
    return sorted(normalized, key=lambda item: item["birth_order"])


def _profile_view(
    *,
    maternal: MaternalProfile | None,
    lactation: LactationProfile | None,
) -> MaternalLactationProfileView | None:
    if maternal is None and lactation is None:
        return None
    return MaternalLactationProfileView(
        delivery_count=maternal.delivery_count if maternal is not None else None,
        current_delivery_method=(maternal.latest_delivery_method if maternal is not None else None),
        actual_delivery_date=(maternal.latest_delivery_date if maternal is not None else None),
        has_cesarean_history=(maternal.has_cesarean_history if maternal is not None else None),
        current_feeding_mode=(lactation.current_feeding_mode if lactation is not None else None),
    )


def _elapsed_days(value: date | None, *, today: date) -> int | None:
    if value is None or value > today:
        return None
    return (today - value).days


def _elapsed_calendar_months(value: date | None, *, today: date) -> int | None:
    if value is None or value > today:
        return None
    months = (today.year - value.year) * 12 + today.month - value.month
    if today.day < value.day:
        months -= 1
    return months


def _gestational_age(total_days: int | None) -> dict[str, Any] | None:
    if total_days is None:
        return None
    return {
        "total_days": total_days,
        "weeks": total_days // 7,
        "days": total_days % 7,
        "is_preterm": total_days < 37 * 7,
    }


def _latest_measurement(record: Any | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "weight_kg": record.weight_kg,
        "height_cm": record.height_cm,
        "head_circumference_cm": record.head_cm,
        "measured_at": record.measured_at.isoformat(),
    }


def _missing_fields(
    *,
    mother: dict[str, Any],
    infants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mother_codes = {
        "age": "mother_age_missing",
        "delivery_count": "mother_delivery_count_missing",
        "current_delivery_method": "mother_current_delivery_method_missing",
        "actual_delivery_date": "mother_actual_delivery_date_missing",
        "has_cesarean_history": "mother_cesarean_history_missing",
        "postpartum_days": "mother_postpartum_days_unavailable",
        "current_feeding_mode": "mother_current_feeding_mode_missing",
    }
    infant_codes = {
        "sex_at_birth": "infant_sex_at_birth_missing",
        "age_days": "infant_age_days_unavailable",
        "age_months": "infant_age_months_unavailable",
        "birth_weight_kg": "infant_birth_weight_missing",
        "gestational_age_at_birth": "infant_gestational_age_missing",
        "latest_measurement": "infant_latest_measurement_missing",
    }
    missing = [
        {
            "code": code,
            "birth_order": None,
        }
        for field, code in mother_codes.items()
        if mother[field] is None
    ]
    if not infants:
        missing.append(
            {
                "code": "current_infant_profiles_missing",
                "birth_order": None,
            }
        )
    for infant in infants:
        birth_order = infant["birth_order"]
        for field, code in infant_codes.items():
            if infant[field] is None:
                missing.append(
                    {
                        "code": code,
                        "birth_order": birth_order,
                    }
                )
    return missing
