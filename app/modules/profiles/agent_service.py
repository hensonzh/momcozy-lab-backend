from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, request_hash
from .lactation_context import LactationContextService
from .service import ProfileService


AGENT_PROFILE_UPDATE_ACTION_TYPE = "profile.update"
AGENT_PROFILE_CURRENT_INFANTS_REPLACE_ACTION_TYPE = (
    "profile.current_infants.replace"
)
AGENT_PROFILE_UPDATE_IDEMPOTENCY_SCOPE = "internal.agent.profile.update"
AgentProfileActionType = Literal[
    "profile.update",
    "profile.current_infants.replace",
]

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


@dataclass(frozen=True)
class AgentProfileUpdateResult:
    resource_type: str
    resource_id: str
    details: dict[str, Any]


@dataclass(frozen=True)
class _PreparedProfileUpdate:
    user_values: dict[str, Any]
    maternal_values: dict[str, Any]
    infant_updates: list[dict[str, Any]]
    current_infants_supplied: bool
    current_infants: list[dict[str, Any]]
    expected_current_infants: list[dict[str, Any]] | None
    reference_date: date

    def details(self) -> dict[str, Any]:
        details: dict[str, Any] = {}
        if self.user_values or self.maternal_values:
            details["mother_fields"] = sorted({*self.user_values, *self.maternal_values})
        if self.infant_updates:
            details["infants"] = [
                {
                    "infant_id": str(update["infant_id"]),
                    "fields": sorted(update["values"]),
                }
                for update in self.infant_updates
            ]
        if self.current_infants_supplied:
            details["current_infants_updated"] = True
        return details


class AgentProfileUpdateService:
    """Product-owned application service for Agent profile mutations."""

    def __init__(
        self,
        *,
        profile_service: ProfileService,
        lactation_context_service: LactationContextService,
        idempotency_service: IdempotencyService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.profile_service = profile_service
        self.lactation_context_service = lactation_context_service
        self.idempotency_service = idempotency_service
        self.audit_service = audit_service

    async def apply(
        self,
        *,
        owner_user_id: UUID,
        action_type: AgentProfileActionType,
        payload: dict[str, Any],
        request_id: str,
    ) -> AgentProfileUpdateResult:
        prepared = _prepare_profile_update(
            payload,
            action_type=action_type,
        )
        return await self._apply_prepared(
            owner_user_id=owner_user_id,
            prepared=prepared,
            request_id=request_id,
        )

    async def apply_idempotent(
        self,
        *,
        owner_user_id: UUID,
        action_type: AgentProfileActionType,
        payload: dict[str, Any],
        idempotency_key: str,
        action_id: UUID,
        run_id: UUID,
        actor_service: str,
        request_id: str,
    ) -> AgentProfileUpdateResult:
        if self.idempotency_service is None:
            raise ApiError(
                code="internal_error",
                message="Profile action idempotency is not configured.",
                status=500,
            )
        if idempotency_key != _action_idempotency_key(action_id):
            raise ApiError(
                code="validation_failed",
                message="Idempotency-Key must be bound to action_id.",
                status=422,
            )
        normalized_actor_service = actor_service.strip()
        if not normalized_actor_service:
            raise ApiError(
                code="validation_failed",
                message="Service actor is required.",
                status=422,
            )
        prepared = _prepare_profile_update(
            payload,
            action_type=action_type,
        )
        decision = await self.idempotency_service.reserve(
            actor_user_id=owner_user_id,
            scope=AGENT_PROFILE_UPDATE_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            request_hash=request_hash(
                {
                    "actor": {
                        "type": "service",
                        "service": normalized_actor_service,
                        "user_id": str(owner_user_id),
                    },
                    "action_id": str(action_id),
                    "run_id": str(run_id),
                    "action_type": action_type,
                    "payload": payload,
                }
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        if decision.status == "replay":
            if decision.record.status != "completed" or not decision.record.response_ref:
                raise ApiError(
                    code="idempotency_in_progress",
                    message="Profile action is still in progress.",
                    status=409,
                )
            if decision.record.response_ref != str(action_id):
                raise ApiError(
                    code="conflict",
                    message="Profile action replay identity is invalid.",
                    status=409,
                )
            return AgentProfileUpdateResult(
                resource_type="profile",
                resource_id=str(owner_user_id),
                details=prepared.details(),
            )

        result = await self._apply_prepared(
            owner_user_id=owner_user_id,
            prepared=prepared,
            request_id=request_id,
        )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=None,
                actor_type="service",
                actor_service=normalized_actor_service,
                action=(
                    "profiles.current_infants.replace"
                    if action_type
                    == AGENT_PROFILE_CURRENT_INFANTS_REPLACE_ACTION_TYPE
                    else "profiles.update"
                ),
                resource_type=result.resource_type,
                resource_id=result.resource_id,
                request_id=request_id,
                details={
                    **result.details,
                    "owner_user_id": str(owner_user_id),
                    "action_id": str(action_id),
                    "run_id": str(run_id),
                    "action_type": action_type,
                },
            )
        await self.idempotency_service.mark_completed(
            record=decision.record,
            response_ref=str(action_id),
        )
        return result

    async def _apply_prepared(
        self,
        *,
        owner_user_id: UUID,
        prepared: _PreparedProfileUpdate,
        request_id: str,
    ) -> AgentProfileUpdateResult:
        anticipated_birth_dates = {
            update["infant_id"]: update["values"]["birth_date"]
            for update in prepared.infant_updates
            if "birth_date" in update["values"]
        }
        if prepared.maternal_values or prepared.current_infants_supplied:
            lactation_values = dict(prepared.maternal_values)
            if prepared.current_infants_supplied:
                lactation_values["current_infants"] = prepared.current_infants
            await self.lactation_context_service.update_maternal_profile(
                owner_user_id=owner_user_id,
                values=lactation_values,
                anticipated_infant_birth_dates=anticipated_birth_dates,
                expected_current_infants=prepared.expected_current_infants,
                reference_date=prepared.reference_date,
                request_id=request_id,
            )
        if prepared.user_values or prepared.infant_updates:
            await self.profile_service.update_profile(
                user_id=owner_user_id,
                user_values=prepared.user_values,
                infant_updates=prepared.infant_updates,
                reference_date=prepared.reference_date,
                request_id=request_id,
            )
        return AgentProfileUpdateResult(
            resource_type="profile",
            resource_id=str(owner_user_id),
            details=prepared.details(),
        )


def _prepare_profile_update(
    payload: dict[str, Any],
    *,
    action_type: AgentProfileActionType,
) -> _PreparedProfileUpdate:
    if set(payload) - {
        "mother",
        "infants",
        "current_infants",
        "expected_current_infants",
        "reference_date",
    }:
        raise _invalid("unsupported_profile_updates")
    reference_date = _reference_date(payload.get("reference_date"))

    raw_mother_values = payload.get("mother")
    if raw_mother_values is not None and not isinstance(raw_mother_values, dict):
        raise _invalid("invalid_mother_profile_updates")
    if isinstance(raw_mother_values, dict) and set(raw_mother_values) - (
        _PROFILE_UPDATE_FIELDS | _MATERNAL_PROFILE_UPDATE_FIELDS
    ):
        raise _invalid("unsupported_mother_profile_updates")
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
    current_infants = _current_infant_links(payload["current_infants"]) if current_infants_supplied else []
    expected_current_infants_supplied = (
        "expected_current_infants" in payload
    )
    expected_current_infants = (
        _current_infant_links(payload["expected_current_infants"])
        if expected_current_infants_supplied
        else None
    )
    if action_type == AGENT_PROFILE_UPDATE_ACTION_TYPE:
        if current_infants_supplied or expected_current_infants_supplied:
            raise _invalid("unexpected_current_infant_relationships")
    elif (
        action_type
        == AGENT_PROFILE_CURRENT_INFANTS_REPLACE_ACTION_TYPE
    ):
        if user_values or maternal_values or infant_updates:
            raise _invalid("unexpected_profile_fields")
        if not current_infants_supplied or not expected_current_infants_supplied:
            raise _invalid("missing_current_infant_precondition")
    else:
        raise _invalid("unsupported_profile_action")
    if not user_values and not maternal_values and not infant_updates and not current_infants_supplied:
        raise _invalid("missing_profile_updates")
    return _PreparedProfileUpdate(
        user_values=user_values,
        maternal_values=maternal_values,
        infant_updates=infant_updates,
        current_infants_supplied=current_infants_supplied,
        current_infants=current_infants,
        expected_current_infants=expected_current_infants,
        reference_date=reference_date,
    )


def _action_idempotency_key(action_id: UUID) -> str:
    return f"agent-action:{action_id}"


def _current_infant_links(raw_links: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_links, list) or len(raw_links) > 10:
        raise _invalid("invalid_current_infants")
    links: list[dict[str, Any]] = []
    infant_ids: set[UUID] = set()
    birth_orders: set[int] = set()
    for raw_link in raw_links:
        if not isinstance(raw_link, dict) or set(raw_link) != {"infant_id", "birth_order"}:
            raise _invalid("invalid_current_infant")
        try:
            infant_id = UUID(str(raw_link["infant_id"]))
        except (TypeError, ValueError) as exc:
            raise _invalid("invalid_infant_id") from exc
        birth_order = raw_link["birth_order"]
        if isinstance(birth_order, bool) or not isinstance(birth_order, int) or not 1 <= birth_order <= 10:
            raise _invalid("invalid_birth_order")
        if infant_id in infant_ids or birth_order in birth_orders:
            raise _invalid("duplicate_current_infant")
        infant_ids.add(infant_id)
        birth_orders.add(birth_order)
        links.append({"infant_id": infant_id, "birth_order": birth_order})
    if sorted(birth_orders) != list(range(1, len(links) + 1)):
        raise _invalid("non_contiguous_birth_order")
    return sorted(links, key=lambda item: item["birth_order"])


def _infant_updates(raw_updates: Any) -> list[dict[str, Any]]:
    if raw_updates is None:
        return []
    if not isinstance(raw_updates, list):
        raise _invalid("invalid_infant_profile_updates")

    updates: list[dict[str, Any]] = []
    seen_ids: set[UUID] = set()
    for raw_update in raw_updates:
        if not isinstance(raw_update, dict) or "infant_id" not in raw_update:
            raise _invalid("invalid_infant_profile_update")
        try:
            infant_id = UUID(str(raw_update["infant_id"]))
        except (TypeError, ValueError) as exc:
            raise _invalid("invalid_infant_id") from exc
        if infant_id in seen_ids:
            raise _invalid("duplicate_infant_update")
        seen_ids.add(infant_id)
        if set(raw_update) - {"infant_id", *_INFANT_PROFILE_UPDATE_FIELDS}:
            raise _invalid("unsupported_infant_profile_updates")
        values = {
            field: _date_value(field=field, value=raw_update[field], date_field="birth_date")
            for field in _INFANT_PROFILE_UPDATE_FIELDS
            if field in raw_update
        }
        if not values:
            raise _invalid("missing_infant_profile_updates")
        updates.append({"infant_id": infant_id, "values": values})
    return updates


def _date_value(*, field: str, value: Any, date_field: str) -> Any:
    if field != date_field or value is None or isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise _invalid(f"invalid_{date_field}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _invalid(f"invalid_{date_field}") from exc


def _reference_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise _invalid("invalid_reference_date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _invalid("invalid_reference_date") from exc


def _invalid(code: str) -> ApiError:
    return ApiError(
        code=code,
        message="Agent profile update payload is invalid.",
        status=422,
    )
