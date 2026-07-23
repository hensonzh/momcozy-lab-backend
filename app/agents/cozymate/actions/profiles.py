from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from app.agent_runtime.actions import AgentActionApplyResult, PermanentActionError
from app.agent_runtime.runs.models import AgentAction
from app.core.errors import ApiError
from app.modules.profiles.service import ProfileService


PROFILE_UPDATE_ACTION = "profile.update"

_PROFILE_UPDATE_FIELDS = {
    "age",
    "estimated_due_date",
    "preferred_name",
}
_INFANT_PROFILE_UPDATE_FIELDS = {
    "birth_date",
    "name",
    "sex_at_birth",
}


class ProfileUpdateActionHandler:
    def __init__(self, *, service: ProfileService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        if set(payload) - {"user", "infants"}:
            raise PermanentActionError("unsupported_profile_updates")

        raw_user_values = payload.get("user")
        if raw_user_values is not None and not isinstance(raw_user_values, dict):
            raise PermanentActionError("invalid_user_profile_updates")
        user_values = {
            field: _date_value(field=field, value=raw_user_values[field], date_field="estimated_due_date")
            for field in _PROFILE_UPDATE_FIELDS
            if isinstance(raw_user_values, dict) and field in raw_user_values
        }
        if isinstance(raw_user_values, dict) and set(raw_user_values) - _PROFILE_UPDATE_FIELDS:
            raise PermanentActionError("unsupported_user_profile_updates")

        infant_updates = _infant_updates(payload.get("infants"))
        if not user_values and not infant_updates:
            raise PermanentActionError("missing_profile_updates")

        try:
            await self.service.update_profile(
                user_id=action.actor_user_id,
                user_values=user_values,
                infant_updates=infant_updates,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentActionError(exc.code) from exc

        details: dict[str, Any] = {}
        if user_values:
            details["user_fields"] = sorted(user_values)
        if infant_updates:
            details["infants"] = [
                {"infant_id": str(update["infant_id"]), "fields": sorted(update["values"])}
                for update in infant_updates
            ]
        return AgentActionApplyResult(
            resource_type="profile",
            resource_id=str(action.actor_user_id),
            details=details,
        )


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
