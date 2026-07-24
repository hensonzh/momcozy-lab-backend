import asyncio
from datetime import date
from uuid import uuid4

import pytest

from app.agent_runtime.runs.models import AgentAction
from app.agents.cozymate.actions.profiles import (
    PROFILE_UPDATE_ACTION,
    MaternalInfantProfileUpdateActionHandler,
)
from app.agent_runtime.actions.errors import PermanentActionError


def test_maternal_infant_profile_update_action_composes_profile_services() -> None:
    profile_service = FakeProfileService()
    lactation_service = FakeLactationContextService()
    infant_id = uuid4()
    action = _action(
        {
            "mother": {
                "preferred_name": "Mai",
                "age": 31,
                "estimated_due_date": "2026-09-20",
                "delivery_count": 2,
                "current_delivery_method": "cesarean",
                "actual_delivery_date": "2026-01-10",
                "has_cesarean_history": True,
                "current_feeding_mode": "mixed_feeding",
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
            "current_infants": [{"infant_id": str(infant_id), "birth_order": 1}],
        }
    )

    result = asyncio.run(
        MaternalInfantProfileUpdateActionHandler(
            profile_service=profile_service,
            lactation_context_service=lactation_service,
        )(action)
    )

    assert lactation_service.kwargs == {
        "owner_user_id": action.actor_user_id,
        "values": {
            "delivery_count": 2,
            "current_delivery_method": "cesarean",
            "actual_delivery_date": date(2026, 1, 10),
            "has_cesarean_history": True,
            "current_feeding_mode": "mixed_feeding",
            "current_infants": [{"infant_id": infant_id, "birth_order": 1}],
        },
        "anticipated_infant_birth_dates": {infant_id: date(2026, 1, 10)},
        "request_id": f"agent-action:{action.id}",
    }
    assert profile_service.kwargs == {
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
        "mother_fields": [
            "actual_delivery_date",
            "age",
            "current_delivery_method",
            "current_feeding_mode",
            "delivery_count",
            "estimated_due_date",
            "has_cesarean_history",
            "preferred_name",
        ],
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
        "current_infants_updated": True,
    }


def test_maternal_infant_profile_update_action_rejects_empty_values() -> None:
    with pytest.raises(PermanentActionError) as exc_info:
        asyncio.run(
            MaternalInfantProfileUpdateActionHandler(
                profile_service=FakeProfileService(),
                lactation_context_service=FakeLactationContextService(),
            )(_action({}))
        )

    assert exc_info.value.code == "missing_profile_updates"


class FakeProfileService:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def update_profile(self, **kwargs):
        self.kwargs = kwargs
        return None, []


class FakeLactationContextService:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def update_maternal_profile(self, **kwargs):
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
