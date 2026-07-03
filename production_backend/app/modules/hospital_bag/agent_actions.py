from __future__ import annotations

from typing import Any

from ...workers.errors import PermanentJobError
from ..agent_runtime.action_outbox import AgentActionApplyResult
from ..agent_runtime.models import AgentAction


HOSPITAL_BAG_CART_UPDATE_ACTION = "hospital_bag.cart.update"


class HospitalBagCartUpdateActionHandler:
    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        cart_update = _cart_update_payload(payload)
        if not cart_update:
            raise PermanentJobError("missing_cart_update")

        return AgentActionApplyResult(
            resource_type="hospital_bag_cart",
            resource_id=action.target_id or str(action.id),
            details={
                "cart_update": cart_update,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


def _cart_update_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cart_update = payload.get("cart_update")
    if isinstance(cart_update, dict):
        return cart_update
    updated_cart = payload.get("updated_cart")
    if isinstance(updated_cart, dict):
        return {"updated_cart": updated_cart}
    return {}
