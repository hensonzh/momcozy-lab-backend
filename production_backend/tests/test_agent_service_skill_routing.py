import asyncio
from uuid import uuid4

from production_backend.app.modules.agent_runtime.routing import (
    DeterministicSkillSignalRouter,
    IntentItem,
    ModelSkillIntentPlanner,
    RoutingContext,
    RoutingPlan,
    RoutingSource,
    ServiceSkillId,
    SkillIntentPlanner,
    SkillRoutingService,
)
from production_backend.app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner, ScriptedSdkBackend, scripted_sdk_response


def test_skill_signal_router_routes_surface_to_service_skill_with_narrow_tool_groups() -> None:
    plan = DeterministicSkillSignalRouter().route(_ctx(message="帮我看看这个清单", app_surface="hospital_bag_page"))

    assert plan is not None
    assert plan.selected_skill_id == ServiceSkillId.BIRTH_PREP
    assert plan.source == RoutingSource.APP_SURFACE
    assert plan.execution_mode == "single"
    assert plan.tool_group_ids == ["general.base", "pregnancy.context", "pregnancy.hospital_bag"]


def test_skill_signal_router_keeps_active_workflow_for_follow_up() -> None:
    plan = DeterministicSkillSignalRouter().route(
        _ctx(message="那昨天呢？", active_service_skill_id=ServiceSkillId.MILK_MANAGEMENT, active_workflow="milk_review")
    )

    assert plan is not None
    assert plan.selected_skill_id == ServiceSkillId.MILK_MANAGEMENT
    assert plan.source == RoutingSource.ACTIVE_WORKFLOW
    assert plan.reason_codes == ["active_skill_sticky"]
    assert plan.tool_group_ids == ["general.base", "lactation.milk_read"]


def test_skill_signal_router_detects_topic_switch_from_active_workflow() -> None:
    plan = DeterministicSkillSignalRouter().route(
        _ctx(message="我的设备蓝牙为什么连不上？", active_service_skill_id=ServiceSkillId.MILK_MANAGEMENT, active_workflow="milk_review")
    )

    assert plan is not None
    assert plan.selected_skill_id == ServiceSkillId.DEVICE_GUIDANCE
    assert plan.source == RoutingSource.LOCAL_HINT
    assert plan.tool_group_ids == ["general.base", "after_sales.device_guidance"]


def test_skill_signal_router_blocks_safety_before_business_routing() -> None:
    plan = DeterministicSkillSignalRouter().route(_ctx(message="我奶量少，而且宝宝发紫，怎么办？", app_surface="milk_dashboard"))

    assert plan is not None
    assert plan.selected_skill_id == ServiceSkillId.HEALTH_CONSULTATION
    assert plan.source == RoutingSource.SAFETY_RULE
    assert plan.execution_mode == "blocked_for_safety"
    assert plan.intents[0].safety_sensitive is True
    assert plan.tool_group_ids == ["general.base", "safety.support"]


def test_skill_signal_router_downgrades_cross_service_multi_intent_to_single_with_note() -> None:
    plan = DeterministicSkillSignalRouter().route(_ctx(message="帮我看看昨天奶量，顺便检查一下吸奶器蓝牙问题"))

    assert plan is not None
    assert plan.selected_skill_id == ServiceSkillId.MILK_MANAGEMENT
    assert plan.source == RoutingSource.COMPLEXITY_RULE
    assert plan.execution_mode == "single_with_note"
    assert [intent.service_skill_id for intent in plan.intents] == [ServiceSkillId.MILK_MANAGEMENT, ServiceSkillId.DEVICE_GUIDANCE]
    assert plan.tool_group_ids == ["general.base", "lactation.milk_read"]


def test_skill_signal_router_routes_postpartum_recovery_text() -> None:
    plan = DeterministicSkillSignalRouter().route(_ctx(message="我想做一个温和的产后康复提醒"))

    assert plan is not None
    assert plan.selected_skill_id == ServiceSkillId.HEALTH_CONSULTATION
    assert plan.source == RoutingSource.LOCAL_HINT


def test_skill_signal_router_routes_ibclc_and_latch_support_to_lactation_handoff_group() -> None:
    plan = DeterministicSkillSignalRouter().route(_ctx(message="我想找 IBCLC 看一下含乳和乳头疼的问题"))

    assert plan is not None
    assert plan.selected_skill_id == ServiceSkillId.MILK_MANAGEMENT
    assert plan.source == RoutingSource.LOCAL_HINT
    assert "lactation.handoff" in plan.tool_group_ids


def test_skill_signal_router_routes_birth_communication_to_pregnancy_service() -> None:
    plan = DeterministicSkillSignalRouter().route(_ctx(message="帮我整理一份给护士看的分娩沟通单"))

    assert plan is not None
    assert plan.selected_skill_id == ServiceSkillId.BIRTH_PREP
    assert plan.source == RoutingSource.LOCAL_HINT
    assert "pregnancy.labor_communication" in plan.tool_group_ids


def test_routing_service_uses_model_planner_for_ambiguous_text() -> None:
    service = SkillRoutingService(planner=FakePlanner(ServiceSkillId.HEALTH_CONSULTATION, confidence=0.87))

    plan = asyncio.run(service.route(_ctx(message="最近状态有点不太舒服，帮我看看")))

    assert plan.selected_skill_id == ServiceSkillId.HEALTH_CONSULTATION
    assert plan.source == RoutingSource.MODEL_PLANNER
    assert plan.tool_group_ids == ["general.base", "postpartum.context"]


def test_routing_service_falls_back_to_general_for_low_confidence_model_plan() -> None:
    service = SkillRoutingService(planner=FakePlanner(ServiceSkillId.HEALTH_CONSULTATION, confidence=0.41))

    plan = asyncio.run(service.route(_ctx(message="最近状态有点怪")))

    assert plan.selected_skill_id == ServiceSkillId.MAIN_AGENT
    assert plan.source == RoutingSource.MODEL_PLANNER
    assert plan.tool_group_ids == ["general.base"]
    assert "low_confidence_fallback" in plan.reason_codes


def test_model_skill_intent_planner_selects_skill_and_filters_tool_groups() -> None:
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text=(
                    '{"service_skill_id":"birth-prep","intent_type":"hospital_bag_request",'
                    '"tool_group_ids":["general.base","pregnancy.hospital_bag","lactation.milk_read","unknown.group"],'
                    '"confidence":0.91,"needs_clarification":false,"reason_codes":["model_semantic_match"]}'
                )
            )
        ]
    )
    planner = ModelSkillIntentPlanner(sdk_runner=OpenAIAgentsSdkRunner(backend=backend))

    plan = asyncio.run(planner.classify(_ctx(message="我想准备去医院要带的东西")))

    assert plan.selected_skill_id == ServiceSkillId.BIRTH_PREP
    assert plan.source == RoutingSource.MODEL_PLANNER
    assert plan.confidence == 0.91
    assert plan.reason_codes == ["model_semantic_match"]
    assert plan.tool_group_ids == ["general.base", "pregnancy.hospital_bag", "pregnancy.context"]
    assert "候选服务技能" in backend.requests[0].instructions
    assert backend.requests[0].tools == ()


def test_model_skill_intent_planner_falls_back_for_invalid_json() -> None:
    backend = ScriptedSdkBackend([scripted_sdk_response(final_text="不是 JSON")])
    planner = ModelSkillIntentPlanner(sdk_runner=OpenAIAgentsSdkRunner(backend=backend))

    plan = asyncio.run(planner.classify(_ctx(message="帮我看看这个事情")))

    assert plan.selected_skill_id == ServiceSkillId.MAIN_AGENT
    assert plan.source == RoutingSource.MODEL_PLANNER
    assert plan.tool_group_ids == ["general.base"]
    assert plan.reason_codes == ["model_planner_invalid_json"]


class FakePlanner(SkillIntentPlanner):
    def __init__(self, skill_id: ServiceSkillId, *, confidence: float) -> None:
        self.skill_id = skill_id
        self.confidence = confidence

    async def classify(self, ctx: RoutingContext) -> RoutingPlan:
        return RoutingPlan(
            selected_skill_id=self.skill_id,
            intents=[IntentItem(intent_type=f"{self.skill_id.value}_request", service_skill_id=self.skill_id)],
            tool_group_ids=["general.base", "postpartum.context"]
            if self.skill_id == ServiceSkillId.HEALTH_CONSULTATION
            else ["general.base"],
            execution_mode="single",
            confidence=self.confidence,
            source=RoutingSource.MODEL_PLANNER,
            reason_codes=["model_planner"],
        )


def _ctx(
    *,
    message: str,
    app_surface: str | None = None,
    active_service_skill_id: ServiceSkillId | None = None,
    active_workflow: str | None = None,
) -> RoutingContext:
    return RoutingContext(
        run_id=uuid4(),
        thread_id=uuid4(),
        actor_user_id=uuid4(),
        message=message,
        app_surface=app_surface,
        active_service_skill_id=active_service_skill_id,
        active_workflow=active_workflow,
    )
