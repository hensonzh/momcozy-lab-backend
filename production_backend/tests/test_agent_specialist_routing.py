import asyncio
from uuid import uuid4

from production_backend.app.modules.agent_runtime.routing import (
    DeterministicSpecialistRouter,
    IntentItem,
    RoutingContext,
    RoutingPlan,
    RoutingSource,
    SpecialistId,
    SpecialistRoutingService,
)
from production_backend.app.modules.agent_runtime.routing.service import IntentClassifier


def test_deterministic_router_routes_surface_to_scene_specialist() -> None:
    plan = DeterministicSpecialistRouter().route(_ctx(message="帮我看看这个清单", app_surface="hospital_bag_page"))

    assert plan is not None
    assert plan.primary_specialist_id == SpecialistId.PREGNANCY
    assert plan.source == RoutingSource.APP_SURFACE
    assert plan.execution_mode == "single"


def test_deterministic_router_keeps_active_workflow_for_follow_up() -> None:
    plan = DeterministicSpecialistRouter().route(
        _ctx(message="那昨天呢？", active_specialist_id=SpecialistId.LACTATION, active_workflow="milk_review")
    )

    assert plan is not None
    assert plan.primary_specialist_id == SpecialistId.LACTATION
    assert plan.source == RoutingSource.ACTIVE_WORKFLOW
    assert plan.reason_codes == ["active_workflow_sticky"]


def test_deterministic_router_detects_topic_switch_from_active_workflow() -> None:
    plan = DeterministicSpecialistRouter().route(
        _ctx(message="我的设备蓝牙为什么连不上？", active_specialist_id=SpecialistId.LACTATION, active_workflow="milk_review")
    )

    assert plan is not None
    assert plan.primary_specialist_id == SpecialistId.AFTER_SALES
    assert plan.source == RoutingSource.KEYWORD_FAST_PATH


def test_deterministic_router_blocks_safety_before_business_routing() -> None:
    plan = DeterministicSpecialistRouter().route(_ctx(message="我奶量少，而且宝宝发紫，怎么办？", app_surface="milk_dashboard"))

    assert plan is not None
    assert plan.primary_specialist_id == SpecialistId.SAFETY
    assert plan.source == RoutingSource.SAFETY_RULE
    assert plan.execution_mode == "blocked_for_safety"
    assert plan.intents[0].safety_sensitive is True


def test_deterministic_router_downgrades_cross_scene_multi_intent_to_single_with_note() -> None:
    plan = DeterministicSpecialistRouter().route(_ctx(message="帮我看看昨天奶量，顺便检查一下吸奶器蓝牙问题"))

    assert plan is not None
    assert plan.primary_specialist_id == SpecialistId.LACTATION
    assert plan.source == RoutingSource.COMPLEXITY_RULE
    assert plan.execution_mode == "single_with_note"
    assert [intent.specialist_id for intent in plan.intents] == [SpecialistId.LACTATION, SpecialistId.AFTER_SALES]


def test_deterministic_router_routes_postpartum_recovery_text() -> None:
    plan = DeterministicSpecialistRouter().route(_ctx(message="我想做一个温和的产后康复提醒"))

    assert plan is not None
    assert plan.primary_specialist_id == SpecialistId.POSTPARTUM
    assert plan.source == RoutingSource.KEYWORD_FAST_PATH


def test_deterministic_router_routes_ibclc_and_latch_support_to_lactation() -> None:
    plan = DeterministicSpecialistRouter().route(_ctx(message="我想找 IBCLC 看一下含乳和乳头疼的问题"))

    assert plan is not None
    assert plan.primary_specialist_id == SpecialistId.LACTATION
    assert plan.source == RoutingSource.KEYWORD_FAST_PATH


def test_deterministic_router_routes_birth_communication_to_pregnancy_service() -> None:
    plan = DeterministicSpecialistRouter().route(_ctx(message="帮我整理一份给护士看的分娩沟通单"))

    assert plan is not None
    assert plan.primary_specialist_id == SpecialistId.PREGNANCY
    assert plan.source == RoutingSource.KEYWORD_FAST_PATH


def test_routing_service_uses_classifier_for_ambiguous_text() -> None:
    service = SpecialistRoutingService(classifier=FakeClassifier(SpecialistId.POSTPARTUM, confidence=0.87))

    plan = asyncio.run(service.route(_ctx(message="最近状态有点不太舒服，帮我看看")))

    assert plan.primary_specialist_id == SpecialistId.POSTPARTUM
    assert plan.source == RoutingSource.LLM_CLASSIFIER


def test_routing_service_falls_back_to_general_for_low_confidence_classifier() -> None:
    service = SpecialistRoutingService(classifier=FakeClassifier(SpecialistId.POSTPARTUM, confidence=0.41))

    plan = asyncio.run(service.route(_ctx(message="最近状态有点怪")))

    assert plan.primary_specialist_id == SpecialistId.GENERAL
    assert plan.source == RoutingSource.LLM_CLASSIFIER
    assert "low_confidence_fallback" in plan.reason_codes


class FakeClassifier(IntentClassifier):
    def __init__(self, specialist_id: SpecialistId, *, confidence: float) -> None:
        self.specialist_id = specialist_id
        self.confidence = confidence

    async def classify(self, ctx: RoutingContext) -> RoutingPlan:
        return RoutingPlan(
            primary_specialist_id=self.specialist_id,
            intents=[IntentItem(intent_type=f"{self.specialist_id.value}_request", specialist_id=self.specialist_id)],
            execution_mode="single",
            confidence=self.confidence,
            source=RoutingSource.LLM_CLASSIFIER,
            reason_codes=["classifier"],
        )


def _ctx(
    *,
    message: str,
    app_surface: str | None = None,
    active_specialist_id: SpecialistId | None = None,
    active_workflow: str | None = None,
) -> RoutingContext:
    return RoutingContext(
        run_id=uuid4(),
        thread_id=uuid4(),
        actor_user_id=uuid4(),
        message=message,
        app_surface=app_surface,
        active_specialist_id=active_specialist_id,
        active_workflow=active_workflow,
    )
