from __future__ import annotations

from dataclasses import dataclass

from .deterministic import DeterministicSpecialistRouter
from .schemas import IntentItem, RoutingContext, RoutingPlan, RoutingSource, SpecialistId


class IntentClassifier:
    async def classify(self, ctx: RoutingContext) -> RoutingPlan:
        return RoutingPlan(
            primary_specialist_id=SpecialistId.GENERAL,
            intents=[IntentItem(intent_type="general_request", specialist_id=SpecialistId.GENERAL)],
            execution_mode="single",
            confidence=0.55,
            source=RoutingSource.FALLBACK,
            reason_codes=["classifier_not_configured"],
        )


@dataclass(frozen=True)
class RoutingPolicy:
    min_classifier_confidence: float = 0.62

    def normalize(self, plan: RoutingPlan) -> RoutingPlan:
        if plan.primary_specialist_id == SpecialistId.SAFETY:
            return plan
        if plan.confidence < self.min_classifier_confidence:
            return RoutingPlan(
                primary_specialist_id=SpecialistId.GENERAL,
                intents=[IntentItem(intent_type="general_request", specialist_id=SpecialistId.GENERAL)],
                execution_mode="single",
                confidence=plan.confidence,
                source=plan.source,
                reason_codes=[*plan.reason_codes, "low_confidence_fallback"],
                safety_flags=plan.safety_flags,
                needs_clarification=plan.needs_clarification,
            )
        return plan


class SpecialistRoutingService:
    def __init__(
        self,
        *,
        deterministic_router: DeterministicSpecialistRouter | None = None,
        classifier: IntentClassifier | None = None,
        policy: RoutingPolicy | None = None,
    ) -> None:
        self.deterministic_router = deterministic_router or DeterministicSpecialistRouter()
        self.classifier = classifier or IntentClassifier()
        self.policy = policy or RoutingPolicy()

    async def route(self, ctx: RoutingContext) -> RoutingPlan:
        plan = self.deterministic_router.route(ctx)
        if plan is None:
            plan = await self.classifier.classify(ctx)
        return self.policy.normalize(plan)
