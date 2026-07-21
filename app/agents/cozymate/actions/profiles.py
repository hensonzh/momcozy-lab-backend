from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agent_runtime.actions import AgentActionApplyResult, PermanentActionError
from app.agent_runtime.runs.models import AgentAction
from app.core.errors import ApiError
from app.modules.profiles.service import ProfileService


PROFILE_UPDATE_ACTION = "profile.update"

_PROFILE_UPDATE_FIELDS = {
    "age",
    "display_name",
    "profile_onboarding_skipped_at",
}


class ProfileUpdateActionHandler:
    def __init__(self, *, service: ProfileService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        values = {
            field: _profile_value(field=field, value=payload[field])
            for field in _PROFILE_UPDATE_FIELDS
            if field in payload
        }
        if not values:
            raise PermanentActionError("missing_profile_updates")

        try:
            profile = await self.service.update_user_profile(
                user_id=action.actor_user_id,
                values=values,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentActionError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="user_profile",
            resource_id=str(profile.id),
            details={"fields": sorted(values)},
        )


def _profile_value(*, field: str, value: Any) -> Any:
    if field != "profile_onboarding_skipped_at" or value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise PermanentActionError("invalid_profile_onboarding_skipped_at") from exc
