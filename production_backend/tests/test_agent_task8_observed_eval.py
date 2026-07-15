import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.actions.executor import AgentActionExecutor
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.device_guidance import (
    DEVICE_ELECTRICAL_HAZARD_RESPONSE,
)
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.health_guidance import (
    COMPLEX_HEALTH_SEARCH_UNAVAILABLE_RESPONSE,
    HEALTH_GUIDANCE_ALLOWED_DOMAINS,
)
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools import (
    DeviceGuidanceReadToolHandler,
    DeviceUnboxingAdvanceToolHandler,
    HospitalBagCardCreateToolHandler,
    HospitalBagCartUpdateProposeToolHandler,
    HospitalBagFormCreateToolHandler,
    IbclcConsultCardCreateToolHandler,
    MilkAnalysisEvaluateToolHandler,
    MilkAnalysisIntakeToolHandler,
    MilkPlanProposeToolHandler,
    MilkScheduleRescheduleProposeToolHandler,
    PregnancyPlanIntakeAdvanceToolHandler,
    PregnancyPlanIntakeAnalyzeToolHandler,
    PregnancyPlanIntakeStartToolHandler,
    PregnancyPlanProposeToolHandler,
    ToolExecutor,
    default_tool_registry,
)
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools.pregnancy_plan_flow import (
    PREGNANCY_PLAN_URGENT_RESPONSE,
)
from production_backend.app.modules.agent_runtime.evals.service import (
    AgentEvalRuntimeClient,
    AgentEvalRuntimeTraceCollector,
    AgentEvalTrace,
)
from production_backend.app.modules.agent_runtime.models import (
    AgentAction,
    AgentArtifact,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentSafetyEvent,
    AgentThread,
    AgentToolCall,
    AgentToolOutput,
    AgentWorkflowState,
)
from production_backend.app.modules.agent_runtime.run_lifecycle.executor import AgentRuntimeExecutor
from production_backend.app.modules.agent_runtime.safety import AgentSafetyService
from production_backend.app.modules.agent_runtime.sdk import (
    OpenAIAgentsSdkRunner,
    SdkNodeRequest,
    SdkNodeResult,
    ScriptedSdkBackend,
    scripted_sdk_response,
    scripted_tool_invocation,
)
from production_backend.app.modules.agent_runtime.service import AgentRuntimeService
from production_backend.app.modules.assets.models import ProductAsset
from production_backend.app.modules.hospital_bag.agent_actions import HospitalBagCartUpdateActionHandler
from production_backend.app.modules.plans.agent_actions import (
    MilkPlanCreateActionHandler,
    MilkScheduleRescheduleActionHandler,
    PregnancyPlanCreateActionHandler,
)
from production_backend.app.modules.plans.models import Plan, PlanTask
from production_backend.app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from production_backend.app.modules.records.schemas import MilkTrendDayRead, MilkTrendListResponse


EMPTY_CASE = {
    "suite": "task8_observed_runtime",
    "name": "runtime-generated trace",
    "expected_tool_calls": [],
    "forbidden_tool_calls": [],
    "expected_safety_decision": "allow",
    "expected_behavior": {},
}


def test_observed_pregnancy_plan_creates_durable_form_then_applies_one_plan() -> None:
    scenario = ObservedScenario()
    handlers = scenario.pregnancy_handlers()

    started = scenario.run_turn(
        text="我现在32周，想制定孕期计划。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation("load_service_skill", {"service_skill_id": "birth-prep"}),
            scripted_tool_invocation("pregnancy.plan_intake.start", {}),
        ),
        final_text="请先填写孕期基本信息表。",
    )

    _assert_tools(started.trace, "load_service_skill", "pregnancy.plan_intake.start")
    _assert_artifact_events(started.trace, "form")
    workflow = scenario.workflow("pregnancy_plan")
    assert workflow.status == "collecting"
    assert workflow.state["phase"] == "collecting_intake"
    assert started.trace.actions == []

    form_artifact_id = workflow.state["source_form_artifact_id"]
    analyzed = scenario.run_turn(
        text="我已经提交信息。",
        handlers=handlers,
        attachments=[
            {
                "type": "form_submission",
                "submission_id": "pregnancy-submission-1",
                "artifact_id": form_artifact_id,
                "form_id": "birth_journey_basic_info_intake",
                "values": {
                    "current_week": "32周",
                    "ivf": "否",
                    "fetus_count": "单胎",
                    "age": 31,
                    "first_birth": "是",
                    "birth_path": "顺产",
                },
                "verified": True,
            }
        ],
        tool_invocations=(scripted_tool_invocation("pregnancy.plan_intake.analyze", {}),),
        final_text="我已完成分析，请继续补充。",
    )
    _assert_tools(analyzed.trace, "pregnancy.plan_intake.analyze")
    assert scenario.workflow("pregnancy_plan").state["source_form_submission_id"] == "pregnancy-submission-1"

    skipped = scenario.run_turn(
        text="暂时没有产检记录，先跳过。",
        handlers=handlers,
        tool_invocations=(scripted_tool_invocation("pregnancy.plan_intake.advance", {"action": "skip_checkup_records"}),),
        final_text="还有其他需要补充的信息吗？",
    )
    _assert_tools(skipped.trace, "pregnancy.plan_intake.advance")
    assert scenario.workflow("pregnancy_plan").state["phase"] == "final_plan_confirmation"

    created = scenario.run_turn(
        text="没有更多信息，请生成。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation("pregnancy.plan_intake.advance", {"action": "confirm_ready_to_generate"}),
            scripted_tool_invocation(
                "pregnancy.plan.propose",
                {"summary": "按孕周安排产检、待产和日常准备。"},
            ),
        ),
        final_text="孕期计划已生成。",
    )

    _assert_tools(created.trace, "pregnancy.plan_intake.advance", "pregnancy.plan.propose")
    _assert_actions(created.trace, ("pregnancy.plan.create", "applied", "plan"))
    _assert_event_types(created.trace, required={"action.applied", "pregnancy_plan.changed", "artifact.created"})
    _assert_event_types(created.trace, forbidden={"action.confirmation_required"})
    _assert_artifact_events(created.trace, "birth_journey_plan_card")
    assert len(scenario.plans.plans) == 1
    assert scenario.plans.plans[0].plan_type == "pregnancy"
    assert scenario.workflow("pregnancy_plan").status == "completed"


def test_observed_pregnancy_plan_urgent_turn_has_no_model_tool_or_side_effect() -> None:
    scenario = ObservedScenario()
    scenario.seed_pregnancy_workflow(
        phase="awaiting_additional_information",
        state={"analysis_run_id": "analysis-1", "plan_context": {"current_week": "32周"}},
    )

    result = scenario.run_turn(
        text="我现在大量出血",
        handlers=scenario.pregnancy_handlers(),
        tool_invocations=(scripted_tool_invocation("pregnancy.plan.propose", {}),),
        final_text="不应返回这段模型文本。",
    )

    assert result.execution_result.final_text == PREGNANCY_PLAN_URGENT_RESPONSE
    assert result.trace.tool_calls == []
    assert result.trace.actions == []
    assert scenario.repository.artifacts == []
    assert scenario.workflow("pregnancy_plan").status == "failed"


def test_observed_hospital_bag_form_card_and_cart_use_runtime_ledgers() -> None:
    scenario = ObservedScenario()
    handlers = scenario.hospital_bag_handlers()
    form = scenario.run_turn(
        text="帮我准备待产包。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation("load_service_skill", {"service_skill_id": "birth-prep"}),
            scripted_tool_invocation("hospital_bag_form_create", {}),
        ),
        final_text="请填写待产包信息。",
    )
    _assert_tools(form.trace, "load_service_skill", "hospital_bag_form_create")
    _assert_artifact_events(form.trace, "form")
    workflow = scenario.workflow("hospital_bag")
    form_artifact_id = workflow.state["source_form_artifact_id"]

    card = scenario.run_turn(
        text="表单已提交。",
        handlers=handlers,
        attachments=[
            {
                "type": "form_submission",
                "submission_id": "hospital-submission-1",
                "artifact_id": form_artifact_id,
                "form_id": "hospital_bag_intake",
                "values": {
                    "due_date_or_week": "36周",
                    "first_birth": "是",
                    "fetus_count": "单胎",
                    "pregnancy_history_or_notes": ["没有"],
                    "birth_path": "顺产",
                    "feeding_intention": "亲喂母乳",
                    "return_to_work_timing": "3个月后",
                    "support_person": "有人全天帮忙",
                    "top_worries": ["怕漏买"],
                },
                "verified": True,
            }
        ],
        tool_invocations=(scripted_tool_invocation("hospital_bag_card_create", {}),),
        final_text="待产包清单已生成。",
    )
    _assert_tools(card.trace, "hospital_bag_card_create")
    _assert_artifact_events(card.trace, "hospital_bag_card")
    assert scenario.workflow("hospital_bag").status == "completed"
    assert card.trace.actions == []

    cart = scenario.run_turn(
        text="把待产包购物车恢复成默认清单。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation(
                "hospital_bag_cart_update",
                {"action": "reset_cart"},
            ),
        ),
        final_text="已更新待产包。",
    )
    _assert_tools(cart.trace, "hospital_bag_cart_update")
    _assert_actions(cart.trace, ("hospital_bag.cart.update", "applied", "hospital_bag_cart"))
    _assert_event_types(cart.trace, required={"action.applied", "hospital_bag.cart.changed"})
    _assert_event_types(cart.trace, forbidden={"action.confirmation_required"})


def test_observed_milk_analysis_plan_and_schedule_persist_real_action_lifecycles() -> None:
    scenario = ObservedScenario()
    handlers = scenario.milk_handlers()
    started = scenario.run_turn(
        text="帮我完整分析奶量。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation("load_service_skill", {"service_skill_id": "milk-management"}),
            scripted_tool_invocation("records.milk_analysis.intake", {"action": "start"}),
        ),
        final_text="先确认宝宝近 24 小时的湿尿布。",
    )
    _assert_tools(started.trace, "load_service_skill", "records.milk_analysis.intake")
    assert scenario.workflow("milk_analysis").active_step == "infant_wet_diapers"

    answers = (
        "24 小时有 7 片湿尿布",
        "精神不错，吃奶后能安稳",
        "最近体重增长正常",
        "没有发热、寒战、红肿、硬块或疼痛加重",
        "吸完后舒服，没有持续胀痛",
    )
    for answer in answers:
        turn = scenario.run_turn(
            text=answer,
            handlers=handlers,
            tool_invocations=(scripted_tool_invocation("records.milk_analysis.intake", {"action": "answer"}),),
            final_text="继续下一项。",
        )
        _assert_tools(turn.trace, "records.milk_analysis.intake")
        assert turn.trace.tool_calls[0]["safe_args"] == {"action": "answer"}

    assert scenario.workflow("milk_analysis").active_step == "ready_to_evaluate"
    evaluated = scenario.run_turn(
        text="请给我分析结果。",
        handlers=handlers,
        tool_invocations=(scripted_tool_invocation("records.milk_analysis.evaluate", {}),),
        final_text="分析完成，可以制定温和的稳奶计划。",
    )
    _assert_tools(evaluated.trace, "records.milk_analysis.evaluate")
    _assert_artifact_events(evaluated.trace, "milk_analysis_card")
    milk_workflow = scenario.workflow("milk_analysis")
    assert milk_workflow.state["phase"] == "assessment_complete"
    assert milk_workflow.state["assessment"]["plan_decision"]["can_start_plan"] is True
    assert evaluated.trace.actions == []

    plan_turn = scenario.run_turn(
        text="按分析结果做一份本周稳奶计划。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation(
                "plans.milk_plan.propose",
                {
                    "direction": "maintain",
                    "days": 1,
                    "preferred_pumping_times": ["08:00", "11:00", "14:00"],
                },
            ),
        ),
        final_text="请确认后创建计划。",
    )
    _assert_tools(plan_turn.trace, "plans.milk_plan.propose")
    _assert_actions(plan_turn.trace, ("plans.milk_plan.create", "confirmation_required", "plan"))
    _assert_event_types(plan_turn.trace, required={"action.confirmation_required", "artifact.created"})
    _assert_artifact_events(plan_turn.trace, "milk_plan_preview")

    plan_trace = scenario.confirm_and_apply(plan_turn.trace.actions[0]["action_type"])
    _assert_actions(plan_trace, ("plans.milk_plan.create", "applied", "plan"))
    _assert_event_types(plan_trace, required={"action.confirmed", "action.applied", "milk_plan.changed"})
    changed = _event_by_type(plan_trace, "milk_plan.changed")
    assert changed["payload"]["operation"] == "created"
    assert len(scenario.plans.plans) == 1
    assert len(scenario.plans.tasks) == 3

    plan = scenario.plans.plans[0]
    plan_date = scenario.plans.tasks[0].task_date
    assert plan_date is not None
    plan_date_text = plan_date.isoformat()
    schedule_turn = scenario.run_turn(
        text="明天 10:30 到 12:30 开会，把冲突的吸奶安排挪开。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation(
                "plans.milk_schedule.propose",
                {
                    "plan_id": str(plan.id),
                    "calendar_events": [
                        {
                            "date": plan_date_text,
                            "start_time": "10:30",
                            "end_time": "12:30",
                            "title": "会议",
                        }
                    ],
                },
            ),
        ),
        final_text="请确认日程调整。",
    )
    _assert_tools(schedule_turn.trace, "plans.milk_schedule.propose")
    _assert_actions(schedule_turn.trace, ("plans.milk_schedule.reschedule", "confirmation_required", "plan"))
    _assert_artifact_events(schedule_turn.trace, "milk_schedule_reschedule_preview")
    _assert_event_types(schedule_turn.trace, required={"action.confirmation_required"})

    schedule_trace = scenario.confirm_and_apply(schedule_turn.trace.actions[0]["action_type"])
    _assert_actions(schedule_trace, ("plans.milk_schedule.reschedule", "applied", "plan"))
    rescheduled = _event_by_type(schedule_trace, "milk_plan.changed")
    assert rescheduled["payload"]["operation"] == "rescheduled"
    assert rescheduled["payload"]["created_calendar_event_count"] == 1
    assert [task.task_time for task in scenario.plans.tasks] == ["08:00", "10:00", "14:00", "10:30"]
    assert scenario.plans.tasks[-1].title == "会议"
    assert scenario.plans.tasks[-1].plan_id is None


def test_observed_milk_red_flags_block_plan_action_and_artifact() -> None:
    scenario = ObservedScenario()
    handlers = scenario.milk_handlers()
    scenario.run_turn(
        text="分析奶量。",
        handlers=handlers,
        tool_invocations=(scripted_tool_invocation("records.milk_analysis.intake", {"action": "start"}),),
        final_text="开始分析。",
    )
    for answer in (
        "24 小时有 7 片湿尿布",
        "精神不错，吃奶后能安稳",
        "最近体重增长正常",
        "没有发热，不过寒战",
        "吸完后仍然很痛",
    ):
        scenario.run_turn(
            text=answer,
            handlers=handlers,
            tool_invocations=(scripted_tool_invocation("records.milk_analysis.intake", {"action": "answer"}),),
            final_text="继续。",
        )
    scenario.run_turn(
        text="给我结论。",
        handlers=handlers,
        tool_invocations=(scripted_tool_invocation("records.milk_analysis.evaluate", {}),),
        final_text="请先联系专业人员。",
    )
    assert scenario.workflow("milk_analysis").state["assessment"]["plan_decision"] == {
        "can_start_plan": False,
        "recommended_direction": None,
        "reason": "maternal_red_flags_require_professional_support",
    }
    artifact_count = len(scenario.repository.artifacts)

    with pytest.raises(ApiError, match="latest milk analysis does not allow a plan"):
        scenario.run_turn(
            text="还是直接给我追奶计划。",
            handlers=handlers,
            tool_invocations=(
                scripted_tool_invocation(
                    "plans.milk_plan.propose",
                    {"direction": "increase"},
                ),
            ),
            final_text="不应成功。",
        )

    failed_run = scenario.repository.runs[-1]
    assert [(call.tool_name, call.status) for call in scenario.repository.tool_calls_for(failed_run.id)] == [
        ("plans.milk_plan.propose", "failed")
    ]
    assert scenario.repository.actions_for(failed_run.id) == []
    assert len(scenario.repository.artifacts) == artifact_count
    assert "tool.failed" in {event.event_type for event in scenario.repository.events_for(failed_run.id)}


def test_observed_device_unboxing_advances_exactly_one_persisted_step() -> None:
    scenario = ObservedScenario()
    handlers = scenario.device_handlers()
    started = scenario.run_turn(
        text="Air1 刚开箱，从哪里开始？",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation("load_service_skill", {"service_skill_id": "device-guidance"}),
            scripted_tool_invocation("devices.unboxing.advance", {"model": "Air1", "action": "start"}),
        ),
        final_text="先核对包装内的部件。",
    )
    _assert_tools(started.trace, "load_service_skill", "devices.unboxing.advance")
    assert scenario.workflow("device_unboxing").active_step == "guide.parts"

    advanced = scenario.run_turn(
        text="部件核对好了，继续。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation(
                "devices.unboxing.advance",
                {"model": "Air1", "action": "complete_current"},
            ),
        ),
        final_text="下一步熟悉主机按键。",
    )
    _assert_tools(advanced.trace, "devices.unboxing.advance")
    assert advanced.trace.tool_calls[0]["safe_args"] == {
        "model": "Air1",
        "action": "complete_current",
    }
    assert scenario.workflow("device_unboxing").active_step == "guide.controls"
    assert advanced.trace.actions == []


def test_observed_known_device_guidance_reads_official_guidance_without_write() -> None:
    scenario = ObservedScenario()
    result = scenario.run_turn(
        text="Air1 吸力变弱，我应该检查什么？",
        handlers=scenario.device_handlers(),
        tool_invocations=(
            scripted_tool_invocation("load_service_skill", {"service_skill_id": "device-guidance"}),
            scripted_tool_invocation(
                "devices.guidance.read",
                {"model": "Air1", "topic": "troubleshooting", "query": "weak suction"},
            ),
        ),
        final_text="请先检查安装密封和耗材状态。",
    )

    _assert_tools(result.trace, "load_service_skill", "devices.guidance.read")
    assert result.trace.actions == []
    assert result.trace.final_text


def test_observed_device_electrical_hazard_bypasses_model_tools_and_actions() -> None:
    scenario = ObservedScenario()
    result = scenario.run_turn(
        text="Air1 充电时有烧焦味。",
        handlers=scenario.device_handlers(),
        tool_invocations=(scripted_tool_invocation("devices.guidance.read", {"model": "Air1"}),),
        final_text="不应调用模型。",
    )

    assert result.execution_result.final_text == DEVICE_ELECTRICAL_HAZARD_RESPONSE
    assert result.trace.tool_calls == []
    assert result.trace.actions == []
    _assert_event_types(result.trace, forbidden={"safety.blocked"})


def test_observed_complex_health_web_search_emits_allowlisted_citations() -> None:
    scenario = ObservedScenario()
    provider = CapturingHealthBackend(
        result=SdkNodeResult(
            final_text="需要结合具体药物和宝宝情况判断。",
            web_search_used=True,
            web_search_citations=[
                {"url": "https://www.ncbi.nlm.nih.gov/books/NBK501922/", "title": "LactMed"},
                {"url": "https://example.com/untrusted", "title": "Untrusted"},
            ],
        )
    )
    result = scenario.run_turn(text="哺乳期用药会不会影响宝宝？", handlers={}, backend=provider)

    assert result.trace.tool_calls == []
    assert result.trace.actions == []
    request = provider.requests[0]
    assert request.web_search_required is True
    assert request.web_search_allowed_domains == tuple(HEALTH_GUIDANCE_ALLOWED_DOMAINS)
    status_events = _events(result.trace, "CUSTOM", name="momcozy.agent.web_search")
    assert [event["payload"]["value"] for event in status_events] == [
        {"status": "searching"},
        {"status": "completed"},
    ]
    assert status_events[0]["payload"]["semantic"]["label"] == "我在查专业资料～"
    assert status_events[-1]["payload"]["semantic"]["label"] == "我查好专业资料啦"
    citation_event = _event(result.trace, "CUSTOM", name="momcozy.web_search.citations")
    assert citation_event["payload"]["value"]["citations"] == [
        {
            "index": 1,
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK501922/",
            "title": "LactMed",
        }
    ]


def test_observed_complex_health_provider_failure_is_bounded_and_side_effect_free() -> None:
    scenario = ObservedScenario()
    result = scenario.run_turn(
        text="哺乳期用药会不会影响宝宝？",
        handlers={},
        backend=FailingHealthBackend(),
    )

    assert result.execution_result.final_text == COMPLEX_HEALTH_SEARCH_UNAVAILABLE_RESPONSE
    assert result.trace.tool_calls == []
    assert result.trace.actions == []
    status_events = _events(result.trace, "CUSTOM", name="momcozy.agent.web_search")
    assert [event["payload"]["value"] for event in status_events] == [
        {"status": "searching"},
        {"status": "failed"},
    ]
    assert status_events[-1]["payload"]["semantic"]["label"] == "专业资料暂时没查好"


def test_observed_medical_red_flag_is_blocked_before_runtime_execution() -> None:
    scenario = ObservedScenario()
    trace, run = scenario.create_safety_blocked_run("我发烧而且乳房红肿越来越严重。")

    assert run.status == "completed"
    assert trace.safety_decision == "escalate"
    assert trace.tool_calls == []
    assert trace.actions == []
    assert [event["type"] for event in trace.events] == [
        "message.completed",
        "safety.blocked",
        "message.completed",
        "run.completed",
    ]
    assistant_event = trace.events[2]
    assert assistant_event["payload"]["role"] == "assistant"
    assert "立即" in assistant_event["payload"]["text"]


def test_observed_ibclc_requires_semantic_consent_and_creates_no_support_action() -> None:
    scenario = ObservedScenario()
    handlers = scenario.ibclc_handlers()
    blocked = scenario.run_turn(
        text="IBCLC 是什么？",
        handlers=handlers,
        tool_invocations=(scripted_tool_invocation("ibclc_consult_card_create", {"reason": "衔乳疼痛"}),),
        final_text="IBCLC 是国际认证哺乳顾问。",
    )
    _assert_tools(blocked.trace, "ibclc_consult_card_create")
    assert blocked.trace.actions == []
    assert scenario.repository.artifacts == []
    _assert_event_types(blocked.trace, forbidden={"artifact.created", "action.confirmation_required"})

    scenario.run_turn(
        text="我是不是需要 IBCLC？",
        handlers=handlers,
        final_text="我不建议现在帮你推荐 IBCLC 哺乳顾问。",
    )
    negated_offer = scenario.run_turn(
        text="好的",
        handlers=handlers,
        tool_invocations=(scripted_tool_invocation("ibclc_consult_card_create", {"reason": "衔乳疼痛"}),),
        final_text="当前不创建咨询入口。",
    )
    _assert_tools(negated_offer.trace, "ibclc_consult_card_create")
    assert scenario.repository.artifacts == []
    _assert_event_types(negated_offer.trace, forbidden={"artifact.created", "action.confirmation_required"})

    opened = scenario.run_turn(
        text="请帮我打开 IBCLC 咨询入口。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation(
                "ibclc_consult_card_create",
                {"reason": "衔乳疼痛", "feeding_context": "左侧喂奶后疼", "urgency": "soon"},
            ),
        ),
        final_text="咨询入口已准备好。",
    )
    _assert_tools(opened.trace, "ibclc_consult_card_create")
    _assert_artifact_events(opened.trace, "ibclc_consult_card")
    assert opened.trace.actions == []
    assert all(call["tool_name"] != "support.ticket.propose" for call in opened.trace.tool_calls)
    _assert_event_types(opened.trace, forbidden={"action.confirmation_required"})


def test_observed_ibclc_canonical_offer_opens_on_first_short_confirmation() -> None:
    scenario = ObservedScenario()
    handlers = scenario.ibclc_handlers()
    scenario.run_turn(
        text="乳头疼，宝宝总是吸不住。",
        handlers=handlers,
        final_text="我这里有很多优秀的 IBCLC 可以帮助到你，你需要我帮你推荐吗？",
    )

    opened = scenario.run_turn(
        text="好的",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation(
                "ibclc_consult_card_create",
                {"reason": "衔乳疼痛", "feeding_context": "宝宝吸不住"},
            ),
        ),
        final_text="IBCLC 咨询入口已经准备好。",
    )

    _assert_tools(opened.trace, "ibclc_consult_card_create")
    _assert_artifact_events(opened.trace, "ibclc_consult_card")
    assert opened.trace.actions == []
    _assert_event_types(opened.trace, forbidden={"action.confirmation_required"})


@dataclass(frozen=True)
class ObservedTurn:
    execution_result: Any
    trace: AgentEvalTrace


class ObservedScenario:
    def __init__(self) -> None:
        self.actor_user_id = uuid4()
        self.thread_id = uuid4()
        self.repository = RecordingRuntimeRepository(actor_user_id=self.actor_user_id, thread_id=self.thread_id)
        self.plans = RecordingPlansService(owner_user_id=self.actor_user_id)
        self.action_executor = AgentActionExecutor(
            repository=self.repository,
            handlers={
                "hospital_bag.cart.update": HospitalBagCartUpdateActionHandler(),
                "plans.milk_plan.create": MilkPlanCreateActionHandler(service=self.plans),
                "plans.milk_schedule.reschedule": MilkScheduleRescheduleActionHandler(service=self.plans),
                "pregnancy.plan.create": PregnancyPlanCreateActionHandler(service=self.plans),
            },
        )
        self.runtime_service = AgentRuntimeService(
            repository=self.repository,
            action_executor=self.action_executor,
        )
        self.assets = RecordingAssetService()
        self.records = RecordingRecordsService(owner_user_id=self.actor_user_id)
        self.profiles = RecordingProfileService()

    def run_turn(
        self,
        *,
        text: str,
        handlers: dict[str, Any],
        tool_invocations: tuple[Any, ...] = (),
        final_text: str = "",
        attachments: list[dict[str, Any]] | None = None,
        backend: Any | None = None,
    ) -> ObservedTurn:
        run = self.repository.add_run(text=text, attachments=attachments or [])
        assert self.repository.tool_calls_for(run.id) == []
        assert self.repository.events_for(run.id) == []
        assert self.repository.actions_for(run.id) == []
        registry = default_tool_registry()
        tool_executor = ToolExecutor(registry=registry, repository=self.repository, handlers=handlers)
        scripted_backend = backend or ScriptedSdkBackend(
            [
                scripted_sdk_response(
                    final_text=final_text,
                    tool_invocations=tool_invocations,
                    expected_available_tools=tuple(invocation.contract_name for invocation in tool_invocations),
                )
            ]
        )
        executor = AgentRuntimeExecutor(
            repository=self.repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=scripted_backend),
            tool_registry=registry,
            tool_executor=tool_executor,
        )
        result = asyncio.run(
            AgentEvalRuntimeClient(executor=executor, repository=self.repository).execute_case(
                run=run,
                case=EMPTY_CASE,
            )
        )
        run.status = result.execution_result.status
        if result.execution_result.status == "completed" and result.execution_result.final_text:
            content: dict[str, Any] = {"text": result.execution_result.final_text}
            if result.execution_result.workflow_reply:
                content["workflow_reply"] = dict(result.execution_result.workflow_reply)
            self.repository.messages.append(
                AgentMessage(
                    id=result.execution_result.assistant_message_id,
                    thread_id=self.thread_id,
                    run_id=run.id,
                    role="assistant",
                    message_type="text",
                    content=content,
                    status="completed",
                    sequence=len(self.repository.messages) + 1,
                    created_at=datetime.now(timezone.utc),
                )
            )
        return ObservedTurn(execution_result=result.execution_result, trace=result.trace)

    def pregnancy_handlers(self) -> dict[str, Any]:
        return {
            "pregnancy.plan_intake.start": PregnancyPlanIntakeStartToolHandler(runtime_service=self.runtime_service),
            "pregnancy.plan_intake.analyze": PregnancyPlanIntakeAnalyzeToolHandler(runtime_service=self.runtime_service),
            "pregnancy.plan_intake.advance": PregnancyPlanIntakeAdvanceToolHandler(runtime_service=self.runtime_service),
            "pregnancy.plan.propose": PregnancyPlanProposeToolHandler(runtime_service=self.runtime_service),
        }

    def hospital_bag_handlers(self) -> dict[str, Any]:
        return {
            "hospital_bag_form_create": HospitalBagFormCreateToolHandler(runtime_service=self.runtime_service),
            "hospital_bag_card_create": HospitalBagCardCreateToolHandler(runtime_service=self.runtime_service),
            "hospital_bag_cart_update": HospitalBagCartUpdateProposeToolHandler(runtime_service=self.runtime_service),
        }

    def device_handlers(self) -> dict[str, Any]:
        return {
            "devices.guidance.read": DeviceGuidanceReadToolHandler(asset_service=self.assets),
            "devices.unboxing.advance": DeviceUnboxingAdvanceToolHandler(
                runtime_service=self.runtime_service,
                asset_service=self.assets,
            ),
        }

    def ibclc_handlers(self) -> dict[str, Any]:
        return {"ibclc_consult_card_create": IbclcConsultCardCreateToolHandler(runtime_service=self.runtime_service)}

    def milk_handlers(self) -> dict[str, Any]:
        return {
            "records.milk_analysis.intake": MilkAnalysisIntakeToolHandler(
                records_service=self.records,
                profile_service=self.profiles,
                runtime_service=self.runtime_service,
            ),
            "records.milk_analysis.evaluate": MilkAnalysisEvaluateToolHandler(runtime_service=self.runtime_service),
            "plans.milk_plan.propose": MilkPlanProposeToolHandler(
                runtime_service=self.runtime_service,
                plans_service=self.plans,
            ),
            "plans.milk_schedule.propose": MilkScheduleRescheduleProposeToolHandler(
                runtime_service=self.runtime_service,
                plans_service=self.plans,
            ),
        }

    def confirm_and_apply(self, action_type: str) -> AgentEvalTrace:
        action = next(action for action in reversed(self.repository.actions) if action.action_type == action_type)
        action_run = next(run for run in self.repository.runs if run.id == action.run_id)
        assert action_run.status == "waiting_for_confirmation"
        confirmed = asyncio.run(
            self.runtime_service.confirm_action(
                owner_user_id=self.actor_user_id,
                action_id=action.id,
            )
        )
        assert confirmed.status == "confirmed"
        outcome = asyncio.run(self.action_executor.apply(confirmed))
        assert outcome.action.status == "applied"
        return asyncio.run(AgentEvalRuntimeTraceCollector(repository=self.repository).collect(run_id=action.run_id))

    def workflow(self, workflow_type: str) -> AgentWorkflowState:
        matches = [workflow for workflow in self.repository.workflow_states if workflow.workflow_type == workflow_type]
        assert matches
        return matches[-1]

    def seed_pregnancy_workflow(self, *, phase: str, state: dict[str, Any]) -> AgentWorkflowState:
        workflow = AgentWorkflowState(
            id=uuid4(),
            thread_id=self.thread_id,
            owner_user_id=self.actor_user_id,
            run_id=None,
            workflow_type="pregnancy_plan",
            status="waiting",
            schema_version="pregnancy-plan.v1",
            state={"phase": phase, "form_id": "birth_journey_basic_info_intake", **state},
            active_step=phase,
        )
        self.repository.workflow_states.append(workflow)
        return workflow

    def create_safety_blocked_run(self, text: str) -> tuple[AgentEvalTrace, AgentRun]:
        run = self.repository.add_run(text=text)
        assert self.repository.tool_calls_for(run.id) == []
        assert self.repository.events_for(run.id) == []
        assert self.repository.actions_for(run.id) == []
        safety_service = AgentSafetyService(repository=self.repository)
        runtime_service = AgentRuntimeService(repository=self.repository, safety_service=safety_service)
        message = self.repository.current_message
        blocked = asyncio.run(
            runtime_service._apply_input_safety_gate(  # noqa: SLF001 - focused service-boundary eval
                owner_user_id=self.actor_user_id,
                run=run,
                message_record_id=message.id,
                text=text,
            )
        )
        assert blocked is run
        trace = asyncio.run(AgentEvalRuntimeTraceCollector(repository=self.repository).collect(run_id=run.id))
        return trace, run


class RecordingRuntimeRepository:
    def __init__(self, *, actor_user_id: UUID, thread_id: UUID) -> None:
        self.actor_user_id = actor_user_id
        self.thread = AgentThread(
            id=thread_id,
            owner_user_id=actor_user_id,
            title="Observed eval",
            status="active",
            metadata_json={},
        )
        self.runs: list[AgentRun] = []
        self.messages: list[AgentMessage] = []
        self.tool_calls: list[AgentToolCall] = []
        self.tool_outputs: list[AgentToolOutput] = []
        self.actions: list[AgentAction] = []
        self.artifacts: list[AgentArtifact] = []
        self.events: list[AgentEvent] = []
        self.safety_events: list[AgentSafetyEvent] = []
        self.workflow_states: list[AgentWorkflowState] = []

    @property
    def current_message(self) -> AgentMessage:
        return self.messages[-1]

    def add_run(self, *, text: str, attachments: list[dict[str, Any]] | None = None) -> AgentRun:
        run = AgentRun(
            id=uuid4(),
            thread_id=self.thread.id,
            actor_user_id=self.actor_user_id,
            status="running",
            runtime_pattern="langgraph_sdk",
            graph_version="momcozy-agent-v1",
            prompt_version="",
            request_id=f"req-{len(self.runs) + 1}",
            trace_id=f"trace-{len(self.runs) + 1}",
            error_code="",
            error_details={},
        )
        self.runs.append(run)
        self.messages.append(
            AgentMessage(
                id=uuid4(),
                thread_id=self.thread.id,
                run_id=run.id,
                role="user",
                message_type="text",
                content={"text": text, "attachments": attachments or []},
                status="completed",
                sequence=len(self.messages) + 1,
                created_at=datetime.now(timezone.utc),
            )
        )
        return run

    def tool_calls_for(self, run_id: UUID) -> list[AgentToolCall]:
        return [call for call in self.tool_calls if call.run_id == run_id]

    def events_for(self, run_id: UUID) -> list[AgentEvent]:
        return [event for event in self.events if event.run_id == run_id]

    def actions_for(self, run_id: UUID) -> list[AgentAction]:
        return [action for action in self.actions if action.run_id == run_id]

    async def get_latest_user_message_for_run(self, *, run_id: UUID):
        return next((message for message in reversed(self.messages) if message.run_id == run_id and message.role == "user"), None)

    async def list_messages_for_thread(self, *, thread_id: UUID, limit: int = 40):
        return [message for message in self.messages if message.thread_id == thread_id][-limit:]

    async def list_client_events_for_thread(self, **_kwargs):
        return []

    async def list_recent_run_summaries(self, **_kwargs):
        return []

    async def get_run(self, *, run_id: UUID):
        return next((run for run in self.runs if run.id == run_id), None)

    async def get_run_for_owner(self, *, run_id: UUID, owner_user_id: UUID):
        run = await self.get_run(run_id=run_id)
        return run if run is not None and run.actor_user_id == owner_user_id else None

    async def record_routing_decision(self, **kwargs):
        run = await self.get_run(run_id=kwargs["run_id"])
        assert run is not None
        run.service_skill_id = kwargs["selected_skill_id"]
        run.routing_source = kwargs["routing_source"]
        run.routing_confidence_score = int(float(kwargs["confidence"]) * 100)
        run.routing_summary = {
            "execution_mode": kwargs["execution_mode"],
            "intents": kwargs["intents"],
            "reason_codes": kwargs["reason_codes"],
            "safety_flags": kwargs["safety_flags"],
            "needs_clarification": kwargs["needs_clarification"],
            "tool_scope_version": kwargs["tool_scope_version"],
        }
        return kwargs

    async def start_tool_call(self, **kwargs):
        call = AgentToolCall(
            id=uuid4(),
            run_id=kwargs["run_id"],
            tool_name=kwargs["tool_name"],
            call_id=kwargs["call_id"],
            status="started",
            safe_args=kwargs["safe_args"],
            started_at=kwargs["started_at"],
            error_code="",
        )
        self.tool_calls.append(call)
        return call

    async def get_tool_call(self, *, tool_call_id: UUID):
        return next((call for call in self.tool_calls if call.id == tool_call_id), None)

    async def complete_tool_call(self, *, tool_call: AgentToolCall, completed_at: datetime):
        tool_call.status = "completed"
        tool_call.completed_at = completed_at
        return tool_call

    async def fail_tool_call(self, *, tool_call: AgentToolCall, completed_at: datetime, error_code: str):
        tool_call.status = "failed"
        tool_call.completed_at = completed_at
        tool_call.error_code = error_code
        return tool_call

    async def create_tool_output(self, **kwargs):
        output = AgentToolOutput(id=uuid4(), **kwargs)
        self.tool_outputs.append(output)
        return output

    async def list_tool_outputs_for_run(self, *, run_id: UUID):
        calls = {call.id: call for call in self.tool_calls_for(run_id)}
        return [(calls[output.tool_call_id], output) for output in self.tool_outputs if output.tool_call_id in calls]

    async def list_tool_calls_for_run(self, *, run_id: UUID):
        return self.tool_calls_for(run_id)

    async def append_event(self, **kwargs):
        event = AgentEvent(
            event_id=uuid4(),
            thread_id=kwargs["thread_id"],
            run_id=kwargs["run_id"],
            sequence=len(self.events_for(kwargs["run_id"])) + 1,
            event_type=kwargs["event_type"],
            payload=kwargs["payload"],
        )
        self.events.append(event)
        return event

    async def list_events_for_run(self, *, run_id: UUID):
        return self.events_for(run_id)

    async def create_action(self, **kwargs):
        action = AgentAction(id=uuid4(), error_code="", **kwargs)
        self.actions.append(action)
        return action

    async def list_actions_for_run(self, *, run_id: UUID):
        return self.actions_for(run_id)

    async def get_action(self, *, action_id: UUID):
        return next((action for action in self.actions if action.id == action_id), None)

    async def get_action_for_owner(self, *, action_id: UUID, owner_user_id: UUID):
        action = await self.get_action(action_id=action_id)
        return action if action is not None and action.actor_user_id == owner_user_id else None

    async def get_reusable_action_by_idempotency_key(self, **kwargs):
        return next(
            (
                action
                for action in reversed(self.actions_for(kwargs["run_id"]))
                if action.actor_user_id == kwargs["actor_user_id"]
                and action.action_type == kwargs["action_type"]
                and action.idempotency_key == kwargs["idempotency_key"]
            ),
            None,
        )

    async def lock_run_for_action_proposal(self, **_kwargs):
        return None

    async def mark_action_confirmed(self, *, action: AgentAction, confirmed_at: datetime, apply_payload, idempotency_key: str):
        action.status = "confirmed"
        action.confirmed_at = confirmed_at
        if apply_payload is not None:
            action.apply_payload = apply_payload
        action.idempotency_key = idempotency_key
        return action

    async def mark_action_applying(self, *, action: AgentAction):
        action.status = "applying"
        return action

    async def mark_action_applied(self, *, action: AgentAction, applied_at: datetime):
        action.status = "applied"
        action.applied_at = applied_at
        return action

    async def mark_action_failed(self, *, action: AgentAction, failed_at: datetime, error_code: str):
        action.status = "failed"
        action.failed_at = failed_at
        action.error_code = error_code
        return action

    async def mark_run_queued(self, *, run: AgentRun):
        run.status = "queued"
        return run

    async def create_artifact(self, **kwargs):
        artifact = AgentArtifact(id=uuid4(), **kwargs)
        self.artifacts.append(artifact)
        return artifact

    async def create_message(self, **kwargs):
        message = AgentMessage(
            id=kwargs.get("message_id") or uuid4(),
            thread_id=kwargs["thread_id"],
            run_id=kwargs["run_id"],
            role=kwargs["role"],
            message_type=kwargs["message_type"],
            content=kwargs["content"],
            status=kwargs["status"],
            sequence=len(self.messages) + 1,
        )
        self.messages.append(message)
        return message

    async def list_artifacts_for_run(self, *, run_id: UUID):
        return [artifact for artifact in self.artifacts if artifact.run_id == run_id]

    async def get_latest_artifact_for_thread(self, **kwargs):
        run_ids = {run.id for run in self.runs if run.thread_id == kwargs["thread_id"]}
        return next(
            (
                artifact
                for artifact in reversed(self.artifacts)
                if artifact.run_id in run_ids
                and artifact.owner_user_id == kwargs["owner_user_id"]
                and artifact.artifact_type == kwargs["artifact_type"]
                and artifact.status != "deleted"
            ),
            None,
        )

    async def get_latest_workflow_state_for_thread(self, *, thread_id: UUID, owner_user_id: UUID, workflow_type: str):
        return next(
            (
                workflow
                for workflow in reversed(self.workflow_states)
                if workflow.thread_id == thread_id and workflow.owner_user_id == owner_user_id and workflow.workflow_type == workflow_type
            ),
            None,
        )

    async def list_active_workflow_states_for_thread(self, *, thread_id: UUID, owner_user_id: UUID, limit: int = 5):
        return [
            workflow
            for workflow in reversed(self.workflow_states)
            if workflow.thread_id == thread_id
            and workflow.owner_user_id == owner_user_id
            and workflow.status not in {"completed", "expired", "failed"}
        ][:limit]

    async def create_workflow_state(self, **kwargs):
        workflow = AgentWorkflowState(id=uuid4(), **kwargs)
        self.workflow_states.append(workflow)
        return workflow

    async def update_workflow_state(self, *, workflow_state: AgentWorkflowState, **kwargs):
        for field in ("status", "state", "active_step", "revision", "step_token", "expires_at"):
            if field in kwargs:
                setattr(workflow_state, field, kwargs[field])
        return workflow_state

    async def record_safety_event(self, **kwargs):
        event = AgentSafetyEvent(id=uuid4(), **kwargs)
        self.safety_events.append(event)
        return event

    async def list_safety_events_for_run(self, *, run_id: UUID):
        return [event for event in self.safety_events if event.run_id == run_id]

    async def mark_run_failed(self, *, run: AgentRun, completed_at: datetime, error_code: str, error_details: dict[str, Any]):
        run.status = "failed"
        run.completed_at = completed_at
        run.error_code = error_code
        run.error_details = error_details
        return run

    async def mark_run_completed(self, *, run: AgentRun, completed_at: datetime):
        run.status = "completed"
        run.completed_at = completed_at
        run.error_code = ""
        run.error_details = {}
        return run


class RecordingPlansService:
    def __init__(self, *, owner_user_id: UUID) -> None:
        self.owner_user_id = owner_user_id
        self.plans: list[Plan] = []
        self.tasks: list[PlanTask] = []

    async def create_plan(self, **kwargs):
        plan = Plan(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            plan_type=kwargs["plan_type"],
            title=kwargs["title"],
            summary=kwargs["summary"],
            status="active",
            source=kwargs["source"],
            payload=kwargs["payload"],
        )
        self.plans.append(plan)
        return plan

    async def create_task(self, **kwargs):
        task = PlanTask(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            plan_id=kwargs.get("plan_id"),
            task_date=kwargs.get("task_date"),
            task_time=kwargs.get("task_time", ""),
            title=kwargs["title"],
            description=kwargs.get("description", ""),
            status="pending",
            payload=kwargs.get("payload", {}),
        )
        self.tasks.append(task)
        return task

    async def get_plan(self, *, owner_user_id: UUID, plan_id: UUID):
        plan = next((item for item in self.plans if item.id == plan_id and item.owner_user_id == owner_user_id), None)
        if plan is None:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        return plan

    async def list_tasks_for_plan(self, *, owner_user_id: UUID, plan_id: UUID, task_dates, status: str, limit: int):
        await self.get_plan(owner_user_id=owner_user_id, plan_id=plan_id)
        return [task for task in self.tasks if task.plan_id == plan_id and task.task_date in task_dates and task.status == status][:limit]

    async def list_tasks(self, *, owner_user_id: UUID, task_date: date, status: str, limit: int):
        return [
            task for task in self.tasks if task.owner_user_id == owner_user_id and task.task_date == task_date and task.status == status
        ][:limit]

    async def list_future_milk_plan_tasks(
        self,
        *,
        owner_user_id: UUID,
        start_date: date,
        end_date: date,
    ):
        milk_plan_ids = {
            plan.id
            for plan in self.plans
            if plan.owner_user_id == owner_user_id and plan.plan_type == "milk_management" and plan.status == "active"
        }
        return [
            task
            for task in self.tasks
            if task.owner_user_id == owner_user_id
            and task.plan_id in milk_plan_ids
            and task.status == "pending"
            and task.task_date is not None
            and start_date <= task.task_date <= end_date
        ]

    async def reschedule_milk_tasks(self, *, owner_user_id: UUID, plan_id: UUID, updates: list[dict[str, Any]], request_id: str):
        del request_id
        await self.get_plan(owner_user_id=owner_user_id, plan_id=plan_id)
        changed: list[PlanTask] = []
        for update in updates:
            task = next((item for item in self.tasks if str(item.id) == update["task_id"] and item.plan_id == plan_id), None)
            if task is None or str(task.task_date) != update["expected_task_date"] or task.task_time != update["expected_task_time"]:
                raise ApiError(code="milk_schedule_conflict", message="Schedule changed.", status=409)
            task.task_date = date.fromisoformat(update["new_task_date"])
            task.task_time = update["new_task_time"]
            changed.append(task)
        return changed


class RecordingRecordsService:
    def __init__(self, *, owner_user_id: UUID) -> None:
        self.owner_user_id = owner_user_id

    async def list_feedings(
        self,
        *,
        owner_user_id: UUID,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int,
    ):
        assert owner_user_id == self.owner_user_id
        return [
            FeedingRecord(
                id=uuid4(),
                owner_user_id=owner_user_id,
                infant_id=None,
                feed_time=datetime(2026, 7, 13, 8, tzinfo=timezone.utc),
                feed_type="bottle",
                feed_action="fed",
                volume_ml=60,
                duration_seconds=None,
                title="Morning feed",
            )
        ][:limit]

    async def list_pumpings(
        self,
        *,
        owner_user_id: UUID,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int,
    ):
        assert owner_user_id == self.owner_user_id
        return [
            PumpingRecord(
                id=uuid4(),
                owner_user_id=owner_user_id,
                pump_start_time=datetime(2026, 7, 13, 9, tzinfo=timezone.utc),
                pump_end_time=None,
                milk_volume_ml=80,
                pump_type="electric",
                duration_seconds=900,
                source="device",
                title="Pump session",
            )
        ][:limit]

    async def list_growth(self, *, owner_user_id: UUID, infant_id=None, limit: int):
        assert owner_user_id == self.owner_user_id
        return [
            GrowthRecord(
                id=uuid4(),
                owner_user_id=owner_user_id,
                infant_id=infant_id,
                measured_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
                height_cm=62,
                weight_kg=6.2,
                head_cm=None,
            )
        ][:limit]

    async def get_milk_trends(self, *, owner_user_id: UUID, days: int, include_today: bool):
        assert owner_user_id == self.owner_user_id
        return MilkTrendListResponse(
            days=days,
            include_today=include_today,
            items=[
                MilkTrendDayRead(date=date(2026, 7, 12), pumped_milk_volume_ml=280, pumping_count=5),
                MilkTrendDayRead(date=date(2026, 7, 13), pumped_milk_volume_ml=260, pumping_count=5),
            ],
        )


class RecordingProfileService:
    async def list_infants(self, *, owner_user_id: UUID):
        del owner_user_id
        return [SimpleNamespace(id=uuid4())]


class RecordingAssetService:
    def list_assets(self, *, limit: int):
        return [
            ProductAsset(
                id="air1-guide",
                label="Air1 unboxing pump guide",
                domain="device_guidance",
                content_type="application/pdf",
                size_bytes=1200,
                path=None,
            )
        ][:limit]


class CapturingHealthBackend:
    def __init__(self, *, result: SdkNodeResult) -> None:
        self.result = result
        self.requests: list[SdkNodeRequest] = []

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        self.requests.append(request)
        return self.result


class FailingHealthBackend:
    def __init__(self) -> None:
        self.requests: list[SdkNodeRequest] = []

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        self.requests.append(request)
        raise ApiError(code="sdk_provider_unavailable", message="provider down", status=503)


def _assert_tools(trace: AgentEvalTrace, *expected: str) -> None:
    assert [(call["tool_name"], call["status"]) for call in trace.tool_calls] == [(tool_name, "completed") for tool_name in expected]


def _assert_actions(trace: AgentEvalTrace, *expected: tuple[str, str, str]) -> None:
    assert [(action["action_type"], action["status"], action["target_type"]) for action in trace.actions] == list(expected)


def _assert_event_types(
    trace: AgentEvalTrace,
    *,
    required: set[str] | None = None,
    forbidden: set[str] | None = None,
) -> None:
    observed = {event["type"] for event in trace.events}
    assert (required or set()) <= observed
    assert not (forbidden or set()) & observed


def _assert_artifact_events(trace: AgentEvalTrace, *artifact_types: str) -> None:
    observed = [event["payload"].get("artifact_type") for event in trace.events if event["type"] == "artifact.created"]
    assert observed == list(artifact_types)


def _event(trace: AgentEvalTrace, event_type: str, *, name: str) -> dict[str, Any]:
    return next(event for event in trace.events if event["type"] == event_type and event["payload"].get("name") == name)


def _events(trace: AgentEvalTrace, event_type: str, *, name: str) -> list[dict[str, Any]]:
    return [event for event in trace.events if event["type"] == event_type and event["payload"].get("name") == name]


def _event_by_type(trace: AgentEvalTrace, event_type: str) -> dict[str, Any]:
    return next(event for event in trace.events if event["type"] == event_type)
