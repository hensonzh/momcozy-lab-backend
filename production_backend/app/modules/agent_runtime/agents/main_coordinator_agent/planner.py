from __future__ import annotations

from .schemas import IntentItem, RoutingPlan, RoutingSource, ServiceSkillId


def plan_current_request() -> RoutingPlan:
    """First-stage coordinator plan: pass every request through to CozyMate."""
    return RoutingPlan(
        selected_skill_id=ServiceSkillId.COZYMATE_SERVICE_AGENT,
        intents=[
            IntentItem(
                intent_type="cozymate_service_request",
                service_skill_id=ServiceSkillId.COZYMATE_SERVICE_AGENT,
            )
        ],
        execution_mode="passthrough",
        confidence=1,
        source=RoutingSource.PASSTHROUGH,
        reason_codes=["delegate_to_cozymate"],
    )
