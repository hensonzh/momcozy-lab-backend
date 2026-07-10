from __future__ import annotations

from .schemas import AgentId, IntentItem, RoutingPlan, RoutingSource


def plan_current_request(*, user_message_text: str = "") -> RoutingPlan:
    """First-stage coordinator plan: select only the downstream agent."""
    return RoutingPlan(
        target_kind="agent",
        selected_agent_id=AgentId.COZYMATE_SERVICE_AGENT,
        intents=[
            IntentItem(
                intent_type=f"{AgentId.COZYMATE_SERVICE_AGENT.value}_request",
                agent_id=AgentId.COZYMATE_SERVICE_AGENT,
            )
        ],
        execution_mode="passthrough",
        confidence=1,
        source=RoutingSource.PASSTHROUGH,
        reason_codes=["default_to_cozymate"],
    )
