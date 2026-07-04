from __future__ import annotations

from typing import Any

from ...core.errors import ApiError
from ...workers.errors import PermanentJobError
from ..agent_runtime.action_outbox import AgentActionApplyResult
from ..agent_runtime.models import AgentAction
from .service import PlansService


MILK_PLAN_CREATE_ACTION = "plans.milk_plan.create"


class MilkPlanCreateActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        title = _text(payload, "title")
        if not title:
            raise PermanentJobError("missing_plan_title")

        plan_payload = payload.get("payload")
        if not isinstance(plan_payload, dict):
            plan_payload = {}
        try:
            plan = await self.service.create_plan(
                owner_user_id=action.actor_user_id,
                plan_type="milk_management",
                title=title,
                summary=_text(payload, "summary"),
                source="agent_action",
                payload={
                    **plan_payload,
                    "agent_action_id": str(action.id),
                    "agent_run_id": str(action.run_id),
                },
                request_id=f"agent-action:{action.id}",
                idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan.id),
            details={
                "plan_type": plan.plan_type,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()
