from __future__ import annotations

from typing import Any

from app.agent_runtime.actions import AgentActionApplyResult, AgentApplicationEvent, PermanentActionError
from app.agent_runtime.runs.models import AgentAction


HOSPITAL_BAG_CART_UPDATE_ACTION = "hospital_bag.cart.update"
HOSPITAL_BAG_CART_CHANGED_EVENT = "hospital_bag.cart.changed"


class HospitalBagCartUpdateActionHandler:
    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        cart_update = _cart_update_payload(payload)
        if not cart_update:
            raise PermanentActionError("missing_cart_update")

        return AgentActionApplyResult(
            resource_type="hospital_bag_cart",
            resource_id=action.target_id or str(action.id),
            details={
                "cart_update": cart_update,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
            application_events=(
                AgentApplicationEvent(
                    event_type=HOSPITAL_BAG_CART_CHANGED_EVENT,
                    payload={
                        "operation": "updated",
                        "source": "agent_action",
                        "cart_update": cart_update,
                    },
                ),
            ),
        )


def _cart_update_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cart_update = payload.get("cart_update")
    if isinstance(cart_update, dict):
        return cart_update
    updated_cart = payload.get("updated_cart")
    if isinstance(updated_cart, dict):
        return {"updated_cart": updated_cart}
    return {}
