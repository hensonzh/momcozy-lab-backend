from __future__ import annotations

from .schemas import AgentId, IntentItem, RoutingPlan, RoutingSource


def plan_current_request(*, user_message_text: str = "") -> RoutingPlan:
    """Runtime no longer routes service skills before the model turn."""
    return RoutingPlan(
        target_kind="agent",
        selected_agent_id=AgentId.COZYMATE_SERVICE_AGENT,
        selected_service_skill_id=None,
        intents=[
            IntentItem(
                intent_type=f"{AgentId.COZYMATE_SERVICE_AGENT.value}_request",
                service_skill_id=None,
            )
        ],
        execution_mode="passthrough",
        confidence=1,
        source=RoutingSource.PASSTHROUGH,
        reason_codes=["default_to_cozymate"],
    )
