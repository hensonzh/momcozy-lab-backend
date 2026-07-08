from __future__ import annotations

from dataclasses import dataclass

from .deterministic import DeterministicSkillSignalRouter
from .schemas import IntentItem, RoutingContext, RoutingPlan, RoutingSource, ServiceSkillId


class SkillIntentPlanner:
    async def classify(self, ctx: RoutingContext) -> RoutingPlan:
        return RoutingPlan(
            selected_skill_id=ServiceSkillId.MAIN_AGENT,
            intents=[IntentItem(intent_type="general_request", service_skill_id=ServiceSkillId.MAIN_AGENT)],
            tool_group_ids=["general.base"],
            execution_mode="single",
            confidence=0.55,
            source=RoutingSource.FALLBACK,
            reason_codes=["model_planner_not_configured"],
        )


@dataclass(frozen=True)
class RoutingPolicy:
    min_model_confidence: float = 0.62

    def normalize(self, plan: RoutingPlan) -> RoutingPlan:
        if plan.safety_flags:
            return plan
        if plan.confidence < self.min_model_confidence:
            return RoutingPlan(
                selected_skill_id=ServiceSkillId.MAIN_AGENT,
                intents=[IntentItem(intent_type="general_request", service_skill_id=ServiceSkillId.MAIN_AGENT)],
                tool_group_ids=["general.base"],
                execution_mode="single",
                confidence=plan.confidence,
                source=plan.source,
                reason_codes=[*plan.reason_codes, "low_confidence_fallback"],
                safety_flags=plan.safety_flags,
                needs_clarification=plan.needs_clarification,
            )
        return plan


class SkillRoutingService:
    def __init__(
        self,
        *,
        deterministic_router: DeterministicSkillSignalRouter | None = None,
        planner: SkillIntentPlanner | None = None,
        policy: RoutingPolicy | None = None,
    ) -> None:
        self.deterministic_router = deterministic_router or DeterministicSkillSignalRouter()
        self.planner = planner or SkillIntentPlanner()
        self.policy = policy or RoutingPolicy()

    async def route(self, ctx: RoutingContext) -> RoutingPlan:
        plan = self.deterministic_router.route(ctx)
        if plan is None:
            plan = await self.planner.classify(ctx)
        return self.policy.normalize(plan)
