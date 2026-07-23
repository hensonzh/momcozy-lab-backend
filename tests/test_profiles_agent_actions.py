import asyncio
from datetime import date
from uuid import uuid4

import pytest

from app.agent_runtime.runs.models import AgentAction
from app.agents.cozymate.actions.profiles import PROFILE_UPDATE_ACTION, ProfileUpdateActionHandler
from app.agent_runtime.actions.errors import PermanentActionError


def test_profile_update_action_handler_updates_profile_through_service() -> None:
    service = FakeProfileService()
    infant_id = uuid4()
    action = _action(
        {
            "user": {
                "preferred_name": "Mai",
                "age": 31,
                "estimated_due_date": "2026-09-20",
            },
            "infants": [
                {
                    "infant_id": str(infant_id),
                    "name": "Nori",
                    "sex_at_birth": "female",
                    "birth_date": "2026-01-10",
                    "birth_weight_kg": 3.2,
                    "gestational_age_at_birth_days": 258,
                }
            ],
        }
    )

    result = asyncio.run(ProfileUpdateActionHandler(service=service)(action))

    assert service.kwargs == {
        "user_id": action.actor_user_id,
        "user_values": {
            "preferred_name": "Mai",
            "age": 31,
            "estimated_due_date": date(2026, 9, 20),
        },
        "infant_updates": [
            {
                "infant_id": infant_id,
                "values": {
                    "name": "Nori",
                    "sex_at_birth": "female",
                    "birth_date": date(2026, 1, 10),
                    "birth_weight_kg": 3.2,
                    "gestational_age_at_birth_days": 258,
                },
            }
        ],
        "request_id": f"agent-action:{action.id}",
    }
    assert result.resource_type == "profile"
    assert result.resource_id == str(action.actor_user_id)
    assert result.details == {
        "user_fields": ["age", "estimated_due_date", "preferred_name"],
        "infants": [
            {
                "infant_id": str(infant_id),
                "fields": [
                    "birth_date",
                    "birth_weight_kg",
                    "gestational_age_at_birth_days",
                    "name",
                    "sex_at_birth",
                ],
            }
        ],
    }


def test_profile_update_action_handler_rejects_empty_values() -> None:
    with pytest.raises(PermanentActionError) as exc_info:
        asyncio.run(ProfileUpdateActionHandler(service=FakeProfileService())(_action({})))

    assert exc_info.value.code == "missing_profile_updates"


class FakeProfileService:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def update_profile(self, **kwargs):
        self.kwargs = kwargs
        return None, []


def _action(apply_payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=PROFILE_UPDATE_ACTION,
        target_type="profile",
        target_id="",
        status="confirmed",
        side_effect_level="low",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="profile-action",
        error_code="",
    )
