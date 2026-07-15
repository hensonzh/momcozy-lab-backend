import asyncio
from uuid import uuid4

import pytest

from production_backend.app.modules.agent_runtime.models import AgentAction
from production_backend.app.modules.hospital_bag import HOSPITAL_BAG_CART_UPDATE_ACTION, HospitalBagCartUpdateActionHandler
from production_backend.app.modules.hospital_bag.agent_actions import HOSPITAL_BAG_CART_CHANGED_EVENT
from production_backend.app.workers.errors import PermanentJobError


def test_hospital_bag_cart_update_action_handler_applies_confirmed_cart_delta() -> None:
    action = _action(
        apply_payload={
            "cart_update": {
                "set_checked": [{"item_id": "nursing-bra", "checked": True}],
                "add_items": [{"item_id": "charger", "label": "Phone charger"}],
            }
        }
    )

    result = asyncio.run(HospitalBagCartUpdateActionHandler()(action))

    assert result.resource_type == "hospital_bag_cart"
    assert result.resource_id == str(action.id)
    assert result.details["cart_update"]["set_checked"][0]["item_id"] == "nursing-bra"
    assert result.details["agent_action_id"] == str(action.id)
    assert result.details["agent_run_id"] == str(action.run_id)
    assert result.application_events[0].event_type == HOSPITAL_BAG_CART_CHANGED_EVENT
    assert result.application_events[0].payload == {
        "operation": "updated",
        "source": "agent_action",
        "cart_update": action.apply_payload["cart_update"],
    }


def test_hospital_bag_cart_update_action_handler_rejects_missing_cart_delta() -> None:
    action = _action(apply_payload={})

    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(HospitalBagCartUpdateActionHandler()(action))

    assert exc_info.value.code == "missing_cart_update"


def _action(*, apply_payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=HOSPITAL_BAG_CART_UPDATE_ACTION,
        target_type="hospital_bag_cart",
        target_id="",
        status="confirmed",
        side_effect_level="low",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="idem-action",
        error_code="",
    )
