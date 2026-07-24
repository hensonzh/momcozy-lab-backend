import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agents.cozymate.actions import cozymate_action_policy
from app.core.errors import ApiError
from app.agent_runtime.actions.executor import AgentActionExecutor
from app.agents.cozymate.health_guidance import (
    HEALTH_GUIDANCE_ALLOWED_DOMAINS,
)
from app.agents.cozymate.tools import (
    DeviceGuidanceToolHandler,
    HospitalBagCardCreateToolHandler,
    HospitalBagCartUpdateProposeToolHandler,
    HospitalBagFormCreateToolHandler,
    IbclcConsultCardCreateToolHandler,
    LactationTimelineManageToolHandler,
    MilkAnalysisReadToolHandler,
    MilkAnalysisToolHandler,
    MilkAnalysisEvaluateToolHandler,
    MilkAnalysisIntakeToolHandler,
    MilkStatusReadToolHandler,
    MilkPlanProposeToolHandler,
    PregnancyDiarySaveToolHandler,
    PregnancyPlanWorkflowToolHandler,
    SupportTicketProposeToolHandler,
    CozymateToolExecutor,
    default_tool_registry,
)
from app.agents.cozymate.tools.pregnancy_plan_flow import (
    PREGNANCY_PLAN_URGENT_RESPONSE,
)
from app.agent_runtime.evals.service import (
    AgentEvalRuntimeClient,
    AgentEvalRuntimeTraceCollector,
    AgentEvalTrace,
)
from app.agent_runtime.context.items import ContextItemAppend, message_context_item
from app.agent_runtime.context.workflow_reply import build_workflow_reply_context
from app.agent_runtime.runs.models import (
    AgentAction,
    AgentArtifact,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentThread,
    AgentToolCall,
    AgentToolOutput,
    AgentWorkflowState,
)
from app.agents.cozymate.executor import CozymateAgentExecutor
from app.agent_runtime.providers import (
    OpenAIResponsesRunner,
    SdkNodeRequest,
    SdkNodeResult,
    ScriptedSdkBackend,
    scripted_sdk_response,
    scripted_tool_invocation,
)
from app.agent_runtime.runs.service import AgentRuntimeService
from app.modules.assets.models import ProductAsset
from app.modules.diary.models import PregnancyDiaryEntry
from app.agents.cozymate.actions.diary import PregnancyDiarySaveActionHandler
from app.agents.cozymate.actions.hospital_bag import HospitalBagCartUpdateActionHandler
from app.agents.cozymate.actions.plans import (
    MilkPlanCreateActionHandler,
    MilkScheduleRescheduleActionHandler,
    PregnancyPlanCreateActionHandler,
)
from app.modules.plans.models import Plan, PlanTask
from app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from app.modules.records.schemas import MilkTrendDayRead, MilkTrendListResponse


ACTION_POLICY = cozymate_action_policy()
EMPTY_CASE = {
    "suite": "task8_observed_runtime",
    "name": "runtime-generated trace",
    "expected_tool_calls": [],
    "forbidden_tool_calls": [],
    "expected_behavior": {},
}


def test_observed_pregnancy_plan_creates_durable_form_then_applies_one_plan() -> None:
    scenario = ObservedScenario()
    handlers = scenario.pregnancy_handlers()

    started = scenario.run_turn(
        text="我现在32周，想制定孕期计划。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation("pregnancy_plan_workflow", {"command": "start_or_resume"}),
        ),
        final_text="请先填写孕期基本信息表。",
    )

    _assert_tools(started.trace, "pregnancy_plan_workflow")
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
        client_context=scenario.pregnancy_command_context(command="submit_form"),
    )
    _assert_tools(analyzed.trace, "pregnancy_plan_workflow")
    assert scenario.workflow("pregnancy_plan").state["source_form_submission_id"] == "pregnancy-submission-1"

    skipped = scenario.run_turn(
        text="暂时没有产检记录，先跳过。",
        handlers=handlers,
        client_context=scenario.pregnancy_command_context(
            command="answer_current",
            choice_id="skip_checkup_records",
        ),
    )
    _assert_tools(skipped.trace, "pregnancy_plan_workflow")
    assert scenario.workflow("pregnancy_plan").state["phase"] == "final_plan_confirmation"

    created = scenario.run_turn(
        text="没有更多信息，请生成。",
        handlers=handlers,
        client_context=scenario.pregnancy_command_context(
            command="answer_current",
            choice_id="confirm_ready_to_generate",
        ),
    )

    _assert_tools(created.trace, "pregnancy_plan_workflow", "pregnancy_plan_workflow")
    _assert_actions(created.trace, ("pregnancy.plan.create", "applied", "plan"))
    _assert_event_types(created.trace, required={"action.applied", "pregnancy_plan.changed", "artifact.created"})
    _assert_event_types(created.trace, forbidden={"action.confirmation_required"})
    _assert_artifact_events(created.trace, "birth_journey_plan_card")
    assert len(scenario.plans.plans) == 1
    assert scenario.plans.plans[0].plan_type == "pregnancy"
    assert scenario.workflow("pregnancy_plan").status == "completed"


def test_observed_pregnancy_plan_urgent_turn_enters_model_before_tool_safety_result() -> None:
    scenario = ObservedScenario()
    scenario.seed_pregnancy_workflow(
        phase="awaiting_additional_information",
        state={"analysis_run_id": "analysis-1", "plan_context": {"current_week": "32周"}},
    )

    result = scenario.run_turn(
        text="我现在大量出血",
        handlers=scenario.pregnancy_handlers(),
        tool_invocations=(
            scripted_tool_invocation(
                "pregnancy_plan_workflow",
                {"command": "generate_plan"},
            ),
        ),
        final_text="不应返回这段模型文本。",
    )

    assert result.execution_result.final_text == PREGNANCY_PLAN_URGENT_RESPONSE
    _assert_tools(result.trace, "pregnancy_plan_workflow")
    assert result.trace.actions == []
    assert scenario.repository.artifacts == []
    assert scenario.workflow("pregnancy_plan").status == "paused"
    assert scenario.workflow("pregnancy_plan").active_step == "workflow_paused"
    assert scenario.workflow("pregnancy_plan").state["resume_phase"] == "awaiting_additional_information"


def test_observed_pregnancy_plan_urgent_historical_edit_interrupts_before_revision() -> None:
    scenario = ObservedScenario()
    scenario.seed_pregnancy_workflow(
        phase="ready_to_generate",
        state={
            "final_plan_confirmed": True,
            "plan_context": {"final_additional_info": "没有其他补充"},
        },
    )

    result = scenario.run_turn(
        text="修改最后补充",
        handlers=scenario.pregnancy_handlers(),
        client_context=scenario.pregnancy_command_context(
            command="edit_answer",
            step_id="final_confirmation",
            choice_id="submit_final_additional_info",
            answer="我现在大量出血",
        ),
        final_text="不应返回这段模型文本。",
    )

    assert result.execution_result.final_text == PREGNANCY_PLAN_URGENT_RESPONSE
    _assert_tools(result.trace, "pregnancy_plan_workflow")
    assert result.trace.actions == []
    assert scenario.repository.artifacts == []
    workflow = scenario.workflow("pregnancy_plan")
    assert workflow.status == "paused"
    assert workflow.active_step == "workflow_paused"
    assert workflow.state["resume_phase"] == "ready_to_generate"
    assert workflow.state["plan_context"]["final_additional_info"] == "没有其他补充"


def test_observed_hospital_bag_form_card_and_cart_use_runtime_ledgers() -> None:
    scenario = ObservedScenario()
    handlers = scenario.hospital_bag_handlers()
    form = scenario.run_turn(
        text="帮我准备待产包。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation("hospital_bag_form_create", {}),
        ),
        final_text="请填写待产包信息。",
    )
    _assert_tools(form.trace, "hospital_bag_form_create")
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
            scripted_tool_invocation("milk_analysis", {"operation": "start_or_resume"}),
        ),
        final_text="先确认宝宝近 24 小时的湿尿布。",
    )
    _assert_tools(started.trace, "milk_analysis")
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
            tool_invocations=(scripted_tool_invocation("milk_analysis", {"operation": "answer"}),),
            final_text="继续下一项。",
        )
        _assert_tools(turn.trace, "milk_analysis")
        assert turn.trace.tool_calls[0]["safe_args"] == {"operation": "answer"}

    assert scenario.workflow("milk_analysis").active_step == "ready_to_evaluate"
    evaluated = scenario.run_turn(
        text="请给我分析结果。",
        handlers=handlers,
        tool_invocations=(scripted_tool_invocation("milk_analysis", {"operation": "evaluate"}),),
        final_text="分析完成，可以制定温和的稳奶计划。",
    )
    _assert_tools(evaluated.trace, "milk_analysis")
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
                "plans_milk_plan_propose",
                {
                    "direction": "maintain",
                    "days": 1,
                    "preferred_pumping_times": ["08:00", "11:00", "14:00"],
                },
            ),
        ),
        final_text="请确认后创建计划。",
    )
    _assert_tools(plan_turn.trace, "plans_milk_plan_propose")
    _assert_actions(plan_turn.trace, ("plans.milk_plan.create", "confirmation_required", "plan"))
    _assert_event_types(
        plan_turn.trace,
        required={"action.confirmation_required", "artifact.created", "run.waiting_for_confirmation"},
    )
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
                "lactation_timeline_manage",
                {
                    "operation": "reschedule",
                    "item_type": "schedule",
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
    _assert_tools(schedule_turn.trace, "lactation_timeline_manage")
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
        tool_invocations=(scripted_tool_invocation("milk_analysis", {"operation": "start_or_resume"}),),
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
            tool_invocations=(scripted_tool_invocation("milk_analysis", {"operation": "answer"}),),
            final_text="继续。",
        )
    scenario.run_turn(
        text="给我结论。",
        handlers=handlers,
        tool_invocations=(scripted_tool_invocation("milk_analysis", {"operation": "evaluate"}),),
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
                    "plans_milk_plan_propose",
                    {"direction": "increase"},
                ),
            ),
            final_text="不应成功。",
        )

    failed_run = scenario.repository.runs[-1]
    assert [(call.tool_name, call.status) for call in scenario.repository.tool_calls_for(failed_run.id)] == [
        ("plans_milk_plan_propose", "failed")
    ]
    assert scenario.repository.actions_for(failed_run.id) == []
    assert len(scenario.repository.artifacts) == artifact_count
    assert "tool.failed" in {event.event_type for event in scenario.repository.events_for(failed_run.id)}


def test_observed_device_unboxing_complete_current_advances_exactly_one_persisted_step() -> None:
    scenario = ObservedScenario()
    handlers = scenario.device_handlers()
    started = scenario.run_turn(
        text="Air1 刚开箱，从哪里开始？",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation(
                "devices_guidance",
                {"model": "Air1", "operation": "start_or_resume"},
            ),
        ),
        final_text="请完成当前主步骤的全部部件核对。",
    )
    _assert_tools(started.trace, "devices_guidance")
    assert scenario.workflow("device_unboxing").active_step == "guide.parts"

    advanced = scenario.run_turn(
        text="当前主步骤已经全部完成。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation(
                "devices_guidance",
                {"model": "Air1", "operation": "complete_current"},
            ),
        ),
        final_text="下一步熟悉主机按键。",
    )
    _assert_tools(advanced.trace, "devices_guidance")
    assert advanced.trace.tool_calls[0]["safe_args"] == {
        "model": "Air1",
        "operation": "complete_current",
    }
    assert scenario.workflow("device_unboxing").active_step == "guide.controls"
    assert advanced.trace.actions == []


def test_observed_known_device_guidance_reads_official_manual_without_write() -> None:
    scenario = ObservedScenario()
    result = scenario.run_turn(
        text="Air1 怎么连接蓝牙？",
        handlers=scenario.device_handlers(),
        tool_invocations=(
            scripted_tool_invocation(
                "devices_guidance",
                {"model": "Air1", "operation": "read", "topic": "bluetooth"},
            ),
        ),
        final_text="请先让主机进入蓝牙配对模式。",
    )

    _assert_tools(result.trace, "devices_guidance")
    assert result.trace.actions == []
    assert result.trace.final_text


def test_observed_device_aftersales_requires_confirmation_then_creates_editable_draft() -> None:
    scenario = ObservedScenario()
    handlers = scenario.support_handlers()
    offered = scenario.run_turn(
        text="Air1 开箱后发现少了一个配件，我很着急。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation(
                "support_ticket_propose",
                {
                    "issue_type": "missing_parts",
                    "issue_summary": "Air1 开箱后缺少配件",
                    "product_model": "Air1",
                    "user_emotion": "着急",
                    "user_confirmed": False,
                },
            ),
        ),
        final_text="这件事确实很影响使用体验，我可以帮你创建一个售后工单。需要我现在帮你创建吗？",
    )

    _assert_tools(offered.trace, "support_ticket_propose")
    _assert_event_types(offered.trace, forbidden={"artifact.created", "action.confirmation_required"})
    assert scenario.repository.artifacts == []

    confirmed = scenario.run_turn(
        text="好的，请帮我创建。",
        handlers=handlers,
        tool_invocations=(
            scripted_tool_invocation(
                "support_ticket_propose",
                {
                    "issue_type": "missing_parts",
                    "issue_summary": "Air1 开箱后缺少配件",
                    "product_model": "Air1",
                    "user_emotion": "着急",
                    "user_confirmed": True,
                },
            ),
        ),
        final_text="我已经把售后信息整理好了，你可以检查并提交。",
    )

    _assert_tools(confirmed.trace, "support_ticket_propose")
    _assert_artifact_events(confirmed.trace, "support_ticket_draft")
    assert confirmed.trace.actions == []
    assert scenario.repository.artifacts[-1].payload["submit_label"] == "确认并提交"


def test_observed_health_response_can_write_user_facts_then_continue_replying() -> None:
    scenario = ObservedScenario()
    result = scenario.run_turn(
        text="没有出血或发烧，疼痛也没有加重，宝宝胎动正常。今天散步后只是有一点轻微牵拉感。",
        handlers=scenario.diary_handlers(),
        tool_invocations=(
            scripted_tool_invocation(
                "pregnancy_diary_save",
                {
                    "operation": "create",
                    "content": "今天散步后有一点轻微牵拉感；没有出血或发烧，疼痛没有加重，宝宝胎动正常。",
                },
            ),
        ),
        final_text="我已经记下来了。先休息并观察；如果牵拉感加重、出现出血或胎动异常，请及时联系产科。",
    )

    _assert_tools(result.trace, "pregnancy_diary_save")
    _assert_event_types(result.trace, required={"pregnancy_diary.changed"})
    assert result.trace.final_text.startswith("我已经记下来了")
    assert scenario.diary.entries[0].content == (
        "今天散步后有一点轻微牵拉感；没有出血或发烧，疼痛没有加重，宝宝胎动正常。"
    )
    assert "建议" not in scenario.diary.entries[0].content


def test_observed_device_electrical_hazard_enters_model_without_runtime_block() -> None:
    scenario = ObservedScenario()
    provider = CapturingHealthBackend(result=SdkNodeResult(final_text="请立即停止使用，并在安全时断开电源。"))
    result = scenario.run_turn(
        text="Air1 充电时有烧焦味。",
        handlers=scenario.device_handlers(),
        backend=provider,
    )

    assert len(provider.requests) == 1
    assert result.execution_result.final_text == "请立即停止使用，并在安全时断开电源。"
    assert result.trace.tool_calls == []
    assert result.trace.actions == []


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
    assert request.web_search_enabled is True
    assert request.web_search_required is False
    assert request.web_search_allowed_domains == tuple(HEALTH_GUIDANCE_ALLOWED_DOMAINS)
    status_events = _events(result.trace, "CUSTOM", name="momcozy.agent.web_search")
    assert [event["payload"]["value"] for event in status_events] == [{"status": "completed"}]
    assert status_events[-1]["payload"]["semantic"]["label"] == "我查好专业资料啦"
    citation_event = _event(result.trace, "CUSTOM", name="momcozy.web_search.citations")
    assert citation_event["payload"]["value"]["citations"] == [
        {
            "index": 1,
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK501922/",
            "title": "LactMed",
        }
    ]


def test_observed_complex_health_provider_failure_is_not_replaced_by_content_fallback() -> None:
    scenario = ObservedScenario()
    provider = FailingHealthBackend()
    with pytest.raises(ApiError) as exc_info:
        scenario.run_turn(
            text="哺乳期用药会不会影响宝宝？",
            handlers={},
            backend=provider,
        )

    assert exc_info.value.code == "sdk_provider_unavailable"
    assert len(provider.requests) == 1


def test_observed_medical_red_flag_enters_model_before_response() -> None:
    scenario = ObservedScenario()
    provider = CapturingHealthBackend(result=SdkNodeResult(final_text="请尽快联系医生或急诊评估。"))
    result = scenario.run_turn(
        text="我发烧而且乳房红肿越来越严重。",
        handlers={},
        backend=provider,
    )

    assert len(provider.requests) == 1
    assert result.execution_result.status == "completed"
    assert result.trace.tool_calls == []
    assert result.trace.actions == []
    _assert_event_types(result.trace, forbidden={"run.failed"})


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
    assert all(call["tool_name"] != "support_ticket_propose" for call in opened.trace.tool_calls)
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
        self.diary = RecordingDiaryService(owner_user_id=self.actor_user_id)
        self.action_executor = AgentActionExecutor(action_policy=ACTION_POLICY,
            repository=self.repository,
            handlers={
                "hospital_bag.cart.update": HospitalBagCartUpdateActionHandler(),
                "plans.milk_plan.create": MilkPlanCreateActionHandler(service=self.plans),
                "plans.milk_schedule.reschedule": MilkScheduleRescheduleActionHandler(service=self.plans),
                "pregnancy.plan.create": PregnancyPlanCreateActionHandler(service=self.plans),
                "pregnancy_diary.entry.save": PregnancyDiarySaveActionHandler(service=self.diary),
            },
        )
        self.runtime_service = AgentRuntimeService(action_policy=ACTION_POLICY,
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
        client_context: dict[str, Any] | None = None,
        backend: Any | None = None,
    ) -> ObservedTurn:
        run = self.repository.add_run(
            text=text,
            attachments=attachments or [],
            client_context=client_context,
        )
        assert self.repository.tool_calls_for(run.id) == []
        assert self.repository.events_for(run.id) == []
        assert self.repository.actions_for(run.id) == []
        registry = default_tool_registry()
        tool_executor = CozymateToolExecutor(registry=registry, repository=self.repository, handlers=handlers)
        scripted_backend = backend or ScriptedSdkBackend(
            [
                scripted_sdk_response(
                    final_text=final_text,
                    tool_invocations=tool_invocations,
                    expected_available_tools=tuple(invocation.contract_name for invocation in tool_invocations),
                )
            ]
        )
        executor = CozymateAgentExecutor(
            repository=self.repository,
            sdk_runner=OpenAIResponsesRunner(backend=scripted_backend),
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
        trace = result.trace
        if result.execution_result.status == "waiting_for_confirmation":
            asyncio.run(
                self.repository.append_event(
                    thread_id=run.thread_id,
                    run_id=run.id,
                    event_type="run.waiting_for_confirmation",
                    payload={"action_id": str(result.execution_result.pending_action_id or "")},
                )
            )
            trace = asyncio.run(
                AgentEvalRuntimeTraceCollector(repository=self.repository).collect(
                    run_id=run.id,
                    final_text=str(result.execution_result.final_text or ""),
                )
            )
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
        return ObservedTurn(execution_result=result.execution_result, trace=trace)

    def pregnancy_command_context(
        self,
        *,
        command: str,
        choice_id: str = "",
        answer: str = "",
        step_id: str = "",
    ) -> dict[str, Any]:
        workflow = self.workflow("pregnancy_plan")
        command_payload = {
            "schema_version": "pregnancy_plan_command.v1",
            "workflow_type": "pregnancy_plan",
            "command": command,
        }
        resolved_step_id = step_id or (workflow.active_step if command == "answer_current" else "")
        if resolved_step_id:
            command_payload["step_id"] = resolved_step_id
        if choice_id:
            command_payload["choice_id"] = choice_id
        if answer:
            command_payload["answer"] = answer
        return {
            "workflow_reply": build_workflow_reply_context(workflow),
            "workflow_command": command_payload,
        }

    def pregnancy_handlers(self) -> dict[str, Any]:
        return {
            "pregnancy_plan_workflow": PregnancyPlanWorkflowToolHandler(runtime_service=self.runtime_service),
        }

    def hospital_bag_handlers(self) -> dict[str, Any]:
        return {
            "hospital_bag_form_create": HospitalBagFormCreateToolHandler(runtime_service=self.runtime_service),
            "hospital_bag_card_create": HospitalBagCardCreateToolHandler(runtime_service=self.runtime_service),
            "hospital_bag_cart_update": HospitalBagCartUpdateProposeToolHandler(runtime_service=self.runtime_service),
        }

    def device_handlers(self) -> dict[str, Any]:
        return {
            "devices_guidance": DeviceGuidanceToolHandler(
                runtime_service=self.runtime_service,
                asset_service=self.assets,
            ),
        }

    def ibclc_handlers(self) -> dict[str, Any]:
        return {"ibclc_consult_card_create": IbclcConsultCardCreateToolHandler(runtime_service=self.runtime_service)}

    def support_handlers(self) -> dict[str, Any]:
        return {"support_ticket_propose": SupportTicketProposeToolHandler(runtime_service=self.runtime_service)}

    def diary_handlers(self) -> dict[str, Any]:
        return {"pregnancy_diary_save": PregnancyDiarySaveToolHandler(runtime_service=self.runtime_service)}

    def milk_handlers(self) -> dict[str, Any]:
        return {
            "milk_analysis": MilkAnalysisToolHandler(
                summary_handler=MilkStatusReadToolHandler(
                    records_service=self.records,
                    profile_service=self.profiles,
                ),
                detailed_handler=MilkAnalysisReadToolHandler(
                    records_service=self.records,
                    profile_service=self.profiles,
                ),
                intake_handler=MilkAnalysisIntakeToolHandler(
                    records_service=self.records,
                    profile_service=self.profiles,
                    runtime_service=self.runtime_service,
                ),
                evaluate_handler=MilkAnalysisEvaluateToolHandler(runtime_service=self.runtime_service),
            ),
            "plans_milk_plan_propose": MilkPlanProposeToolHandler(
                runtime_service=self.runtime_service,
                plans_service=self.plans,
            ),
            "lactation_timeline_manage": LactationTimelineManageToolHandler(
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
        self.workflow_states: list[AgentWorkflowState] = []
        self.context_items: list[ContextItemAppend] = []

    @property
    def current_message(self) -> AgentMessage:
        return self.messages[-1]

    def add_run(
        self,
        *,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        client_context: dict[str, Any] | None = None,
    ) -> AgentRun:
        run = AgentRun(
            id=uuid4(),
            thread_id=self.thread.id,
            actor_user_id=self.actor_user_id,
            status="running",
            runtime_pattern="sdk_only",
            runtime_version="momcozy-agent-v2",
            prompt_version="",
            request_id=f"req-{len(self.runs) + 1}",
            trace_id=f"trace-{len(self.runs) + 1}",
            error_code="",
            error_details={},
        )
        self.runs.append(run)
        content: dict[str, Any] = {"text": text, "attachments": attachments or []}
        if client_context:
            content["client_context"] = dict(client_context)
        message = AgentMessage(
            id=uuid4(),
            thread_id=self.thread.id,
            run_id=run.id,
            role="user",
            message_type="text",
            content=content,
            status="completed",
            sequence=len(self.messages) + 1,
            created_at=datetime.now(timezone.utc),
        )
        self.messages.append(message)
        self.context_items.append(
            ContextItemAppend(
                item_key=f"message:{message.id}",
                item=message_context_item(role="user", content=message.content),
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

    async def list_context_items_for_thread(
        self,
        *,
        thread_id: UUID,
        limit: int | None = None,
    ):
        items = list(self.context_items) if thread_id == self.thread.id else []
        return items[-limit:] if isinstance(limit, int) else items

    async def append_context_items(self, *, thread_id: UUID, run_id: UUID, items):
        assert thread_id == self.thread.id
        assert any(run.id == run_id for run in self.runs)
        self.context_items.extend(items)
        return list(items)

    async def list_client_events_for_thread(self, **_kwargs):
        return []

    async def list_recent_run_summaries(self, **_kwargs):
        return []

    async def get_run(self, *, run_id: UUID):
        return next((run for run in self.runs if run.id == run_id), None)

    async def get_run_for_owner(self, *, run_id: UUID, owner_user_id: UUID):
        run = await self.get_run(run_id=run_id)
        return run if run is not None and run.actor_user_id == owner_user_id else None

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

    async def get_latest_workflow_state_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        workflow_type: str,
        for_update: bool = False,
    ):
        del for_update
        return next(
            (
                workflow
                for workflow in reversed(self.workflow_states)
                if workflow.thread_id == thread_id and workflow.owner_user_id == owner_user_id and workflow.workflow_type == workflow_type
            ),
            None,
        )

    async def lock_workflow_owner(self, *, owner_user_id: UUID):
        del owner_user_id

    async def get_latest_workflow_state_for_owner(
        self,
        *,
        owner_user_id: UUID,
        workflow_type: str,
        for_update: bool = False,
    ):
        del for_update
        return next(
            (
                workflow
                for workflow in reversed(self.workflow_states)
                if workflow.owner_user_id == owner_user_id
                and workflow.workflow_type == workflow_type
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

    async def list_active_workflow_states_for_owner(
        self,
        *,
        owner_user_id: UUID,
        workflow_type: str | None = None,
        limit: int = 5,
    ):
        return [
            workflow
            for workflow in reversed(self.workflow_states)
            if workflow.owner_user_id == owner_user_id
            and (workflow_type is None or workflow.workflow_type == workflow_type)
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

    async def append_workflow_event(self, **kwargs):
        return kwargs

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


class RecordingDiaryService:
    def __init__(self, *, owner_user_id: UUID) -> None:
        self.owner_user_id = owner_user_id
        self.entries: list[PregnancyDiaryEntry] = []

    async def create_entry(self, *, owner_user_id: UUID, entry_date: date, values: dict[str, Any], request_id: str):
        del request_id
        assert owner_user_id == self.owner_user_id
        if any(entry.entry_date == entry_date for entry in self.entries):
            raise ApiError(code="conflict", message="Diary entry already exists.", status=409)
        entry = PregnancyDiaryEntry(
            id=uuid4(),
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            content=str(values.get("content") or ""),
            symptom_tags=[],
            attachments=[],
        )
        self.entries.append(entry)
        return entry

    async def get_entry(self, *, owner_user_id: UUID, entry_date: date):
        entry = next(
            (item for item in self.entries if item.owner_user_id == owner_user_id and item.entry_date == entry_date),
            None,
        )
        if entry is None:
            raise ApiError(code="not_found", message="Diary entry not found.", status=404)
        return entry


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
