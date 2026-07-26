from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from app.agent_runtime.actions import AgentActionApplyResult, PermanentActionError
from app.agent_runtime.runs.models import AgentAction
from app.core.errors import ApiError
from app.modules.profiles.lactation_context import LactationContextService
from app.modules.profiles.service import ProfileService


PROFILE_UPDATE_ACTION = "profile.update"
PROFILE_CURRENT_INFANTS_REPLACE_ACTION = "profile.current_infants.replace"

_PROFILE_UPDATE_FIELDS = {
    "age",
    "estimated_due_date",
    "preferred_name",
}
_MATERNAL_PROFILE_UPDATE_FIELDS = {
    "actual_delivery_date",
    "current_delivery_method",
    "current_feeding_mode",
    "delivery_count",
    "has_cesarean_history",
}
_INFANT_PROFILE_UPDATE_FIELDS = {
    "birth_date",
    "birth_weight_kg",
    "gestational_age_at_birth_days",
    "name",
    "sex_at_birth",
}


class MaternalInfantProfileUpdateActionHandler:
    def __init__(
        self,
        *,
        profile_service: ProfileService,
        lactation_context_service: LactationContextService,
    ) -> None:
        self.profile_service = profile_service
        self.lactation_context_service = lactation_context_service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        if set(payload) - {
            "mother",
            "infants",
            "current_infants",
            "expected_current_infants",
            "reference_date",
        }:
            raise PermanentActionError("unsupported_profile_updates")
        reference_date = _reference_date(payload.get("reference_date"))

        raw_mother_values = payload.get("mother")
        if raw_mother_values is not None and not isinstance(raw_mother_values, dict):
            raise PermanentActionError("invalid_mother_profile_updates")
        if isinstance(raw_mother_values, dict) and set(raw_mother_values) - (
            _PROFILE_UPDATE_FIELDS | _MATERNAL_PROFILE_UPDATE_FIELDS
        ):
            raise PermanentActionError("unsupported_mother_profile_updates")
        user_values = {
            field: _date_value(
                field=field,
                value=raw_mother_values[field],
                date_field="estimated_due_date",
            )
            for field in _PROFILE_UPDATE_FIELDS
            if isinstance(raw_mother_values, dict) and field in raw_mother_values
        }
        maternal_values = {
            field: _date_value(
                field=field,
                value=raw_mother_values[field],
                date_field="actual_delivery_date",
            )
            for field in _MATERNAL_PROFILE_UPDATE_FIELDS
            if isinstance(raw_mother_values, dict) and field in raw_mother_values
        }

        infant_updates = _infant_updates(payload.get("infants"))
        current_infants_supplied = "current_infants" in payload
        current_infants = (
            _current_infant_links(payload["current_infants"])
            if current_infants_supplied
            else []
        )
        expected_current_infants_supplied = "expected_current_infants" in payload
        expected_current_infants = (
            _current_infant_links(payload["expected_current_infants"])
            if expected_current_infants_supplied
            else None
        )
        if expected_current_infants_supplied and not current_infants_supplied:
            raise PermanentActionError("unexpected_current_infant_precondition")
        if (
            action.action_type == PROFILE_CURRENT_INFANTS_REPLACE_ACTION
            and (
                not current_infants_supplied
                or not expected_current_infants_supplied
            )
        ):
            raise PermanentActionError("missing_current_infant_precondition")
        if (
            not user_values
            and not maternal_values
            and not infant_updates
            and not current_infants_supplied
        ):
            raise PermanentActionError("missing_profile_updates")

        anticipated_birth_dates = {
            update["infant_id"]: update["values"]["birth_date"]
            for update in infant_updates
            if "birth_date" in update["values"]
        }
        try:
            if maternal_values or current_infants_supplied:
                lactation_values = dict(maternal_values)
                if current_infants_supplied:
                    lactation_values["current_infants"] = current_infants
                await self.lactation_context_service.update_maternal_profile(
                    owner_user_id=action.actor_user_id,
                    values=lactation_values,
                    anticipated_infant_birth_dates=anticipated_birth_dates,
                    expected_current_infants=expected_current_infants,
                    reference_date=reference_date,
                    request_id=f"agent-action:{action.id}",
                )
            if user_values or infant_updates:
                await self.profile_service.update_profile(
                    user_id=action.actor_user_id,
                    user_values=user_values,
                    infant_updates=infant_updates,
                    reference_date=reference_date,
                    request_id=f"agent-action:{action.id}",
                )
        except ApiError as exc:
            raise PermanentActionError(exc.code) from exc

        details: dict[str, Any] = {}
        if user_values or maternal_values:
            details["mother_fields"] = sorted(
                {*user_values, *maternal_values}
            )
        if infant_updates:
            details["infants"] = [
                {"infant_id": str(update["infant_id"]), "fields": sorted(update["values"])}
                for update in infant_updates
            ]
        if current_infants_supplied:
            details["current_infants_updated"] = True
        return AgentActionApplyResult(
            resource_type="profile",
            resource_id=str(action.actor_user_id),
            details=details,
        )


def _current_infant_links(raw_links: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_links, list) or len(raw_links) > 10:
        raise PermanentActionError("invalid_current_infants")
    links: list[dict[str, Any]] = []
    infant_ids: set[UUID] = set()
    birth_orders: set[int] = set()
    for raw_link in raw_links:
        if not isinstance(raw_link, dict) or set(raw_link) != {
            "infant_id",
            "birth_order",
        }:
            raise PermanentActionError("invalid_current_infant")
        try:
            infant_id = UUID(str(raw_link["infant_id"]))
        except (TypeError, ValueError) as exc:
            raise PermanentActionError("invalid_infant_id") from exc
        birth_order = raw_link["birth_order"]
        if (
            isinstance(birth_order, bool)
            or not isinstance(birth_order, int)
            or not 1 <= birth_order <= 10
        ):
            raise PermanentActionError("invalid_birth_order")
        if infant_id in infant_ids or birth_order in birth_orders:
            raise PermanentActionError("duplicate_current_infant")
        infant_ids.add(infant_id)
        birth_orders.add(birth_order)
        links.append({"infant_id": infant_id, "birth_order": birth_order})
    if sorted(birth_orders) != list(range(1, len(links) + 1)):
        raise PermanentActionError("non_contiguous_birth_order")
    return sorted(links, key=lambda item: item["birth_order"])


def _infant_updates(raw_updates: Any) -> list[dict[str, Any]]:
    if raw_updates is None:
        return []
    if not isinstance(raw_updates, list):
        raise PermanentActionError("invalid_infant_profile_updates")

    updates: list[dict[str, Any]] = []
    seen_ids: set[UUID] = set()
    for raw_update in raw_updates:
        if not isinstance(raw_update, dict) or "infant_id" not in raw_update:
            raise PermanentActionError("invalid_infant_profile_update")
        try:
            infant_id = UUID(str(raw_update["infant_id"]))
        except (TypeError, ValueError) as exc:
            raise PermanentActionError("invalid_infant_id") from exc
        if infant_id in seen_ids:
            raise PermanentActionError("duplicate_infant_update")
        seen_ids.add(infant_id)
        if set(raw_update) - {"infant_id", *_INFANT_PROFILE_UPDATE_FIELDS}:
            raise PermanentActionError("unsupported_infant_profile_updates")
        values = {
            field: _date_value(field=field, value=raw_update[field], date_field="birth_date")
            for field in _INFANT_PROFILE_UPDATE_FIELDS
            if field in raw_update
        }
        if not values:
            raise PermanentActionError("missing_infant_profile_updates")
        updates.append({"infant_id": infant_id, "values": values})
    return updates


def _date_value(*, field: str, value: Any, date_field: str) -> Any:
    if field != date_field or value is None or isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise PermanentActionError(f"invalid_{date_field}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PermanentActionError(f"invalid_{date_field}") from exc


def _reference_date(value: Any) -> date:
    if value is None:
        return date.today()
    parsed = _date_value(
        field="reference_date",
        value=value,
        date_field="reference_date",
    )
    if not isinstance(parsed, date):
        raise PermanentActionError("invalid_reference_date")
    return parsed
