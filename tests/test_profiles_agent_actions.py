import asyncio
from uuid import uuid4

import pytest

from app.agent_runtime.runs.models import AgentAction
from app.agents.cozymate.actions.profiles import PROFILE_UPDATE_ACTION, ProfileUpdateActionHandler
from app.modules.profiles.models import UserProfile
from app.agent_runtime.actions.errors import PermanentActionError


def test_profile_update_action_handler_updates_profile_through_service() -> None:
    service = FakeProfileService()
    action = _action({"display_name": "Mai", "age": 31})

    result = asyncio.run(ProfileUpdateActionHandler(service=service)(action))

    assert service.kwargs == {
        "user_id": action.actor_user_id,
        "values": {"display_name": "Mai", "age": 31},
        "request_id": f"agent-action:{action.id}",
    }
    assert result.resource_type == "user_profile"
    assert result.resource_id == str(service.profile.id)
    assert result.details == {"fields": ["age", "display_name"]}


def test_profile_update_action_handler_rejects_empty_values() -> None:
    with pytest.raises(PermanentActionError) as exc_info:
        asyncio.run(ProfileUpdateActionHandler(service=FakeProfileService())(_action({})))

    assert exc_info.value.code == "missing_profile_updates"


class FakeProfileService:
    def __init__(self) -> None:
        self.profile = UserProfile(id=uuid4(), user_id=uuid4())
        self.kwargs: dict = {}

    async def update_user_profile(self, **kwargs):
        self.kwargs = kwargs
        self.profile.user_id = kwargs["user_id"]
        for key, value in kwargs["values"].items():
            setattr(self.profile, key, value)
        return self.profile


def _action(apply_payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=PROFILE_UPDATE_ACTION,
        target_type="user_profile",
        target_id="",
        status="confirmed",
        side_effect_level="low",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="profile-action",
        error_code="",
    )
