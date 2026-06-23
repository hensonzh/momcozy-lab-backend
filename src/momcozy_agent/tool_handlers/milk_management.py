from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from ..services.milk_management.assessment import evaluate_milk_status
from ..services.milk_management.calendar import (
    apply_calendar_adjustment,
    apply_calendar_reschedule,
    delete_calendar_item,
    get_calendar_day,
    get_calendar_range,
    get_current_calendar_plan,
    preview_calendar_adjustment,
    preview_day_reschedule,
    update_calendar_range,
    update_calendar_item,
)
from ..services.milk_management.context import get_milk_context
from ..services.milk_management.growth import evaluate_infant_growth
from ..services.milk_management.growth_mutation import mutate_infant_growth
from ..services.milk_management.plan import (
    apply_milk_plan,
    delete_milk_plan,
    get_milk_plan,
    list_milk_plans,
    preview_milk_plan,
    regenerate_milk_plan_preview,
    update_milk_plan,
    validate_milk_plan,
    validate_milk_plan_target,
)
from ..services.milk_management.records import (
    create_record,
    delete_record,
    get_records_range,
    update_record,
)
from ..services.milk_management.status import query_milk_status
from ..services.milk_management.task_completion import complete_milk_task
from ..services.milk_management.today import (
    get_today_overview,
    get_today_summary,
)
from ..services.milk_management.schemas import PLAN_TYPE_DECREASE, PLAN_TYPE_INCREASE, PLAN_TYPE_MAINTAIN, norm_text, to_bool, to_int
from ..types import RuntimeInputs

RISK_LOW = "low"
RISK_WATCH = "watch"
RISK_IBCLC_RECOMMENDED = "ibclc_recommended"
RISK_MEDICAL_RECOMMENDED = "medical_recommended"
RISK_URGENT = "urgent"

ALL_PLAN_TYPES = [PLAN_TYPE_INCREASE, PLAN_TYPE_MAINTAIN, PLAN_TYPE_DECREASE]
FLOW_REQUIRED_FIELDS = [
    "records_7d",
    "infant_wet_diapers",
    "infant_state_or_satisfaction",
    "infant_growth_signal",
    "maternal_red_flags",
    "maternal_breast_comfort",
]
FLOW_MATERNAL_RED_FLAG_FIELDS = ("fever", "chills", "breast_redness", "lump_or_hard_area", "worsening_pain")
FLOW_FIELD_LABELS = {
    "records_7d": "过去 7 天可计算奶量记录",
    "infant_wet_diapers": "宝宝近 24 小时尿量/尿布情况",
    "infant_state_or_satisfaction": "宝宝精神状态和吃奶后表现",
    "infant_growth_signal": "宝宝近期体重增长情况",
    "maternal_red_flags": "妈妈有没有发热、寒战、红肿、硬块或疼痛加重",
    "maternal_breast_comfort": "吸奶或亲喂后乳房舒适度",
}
FLOW_FIELD_QUICK_REPLIES = {
    "records_7d": ["记录是完整的", "有漏记吸奶", "有漏记瓶喂"],
    "infant_wet_diapers": ["尿布挺多的", "尿布有点少", "不太确定"],
    "infant_state_or_satisfaction": ["精神还不错", "吃奶后不安稳", "变化不明显"],
    "infant_growth_signal": ["体重增长正常", "增长有点慢", "还没称体重"],
    "maternal_red_flags": ["没有这些情况", "有发热红肿", "有硬块疼痛"],
    "maternal_breast_comfort": ["吸完舒服", "还会胀", "会疼"],
}
FLOW_FIELD_GUIDANCE = {
    "records_7d": {
        "why_this_field_matters": "7 天记录完整度决定趋势判断是否可信，尤其会影响偏低是持续趋势还是漏记造成。",
        "how_to_interpret_answers": "用户确认完整时继续看宝宝和妈妈状态；用户说有漏记时，要把结论降级为初步观察，并先补齐记录。",
        "do_not_infer": ["不要把计划任务完成数当作实际奶量记录", "不要让用户重复描述工具已经读取到的完整 7 天节奏"],
    },
    "infant_wet_diapers": {
        "why_this_field_matters": "尿布/尿量是判断宝宝短期摄入是否足够的重要信号。",
        "how_to_interpret_answers": "尿布正常会降低短期摄入风险；明显偏少或不确定时，分析和计划都要更保守。",
        "do_not_infer": ["不要把宝宝精神正常自动等同于尿布正常", "不要把尿布问题扩展成多项追问"],
    },
    "infant_state_or_satisfaction": {
        "why_this_field_matters": "精神状态和吃奶后是否安稳能帮助区分单纯产量波动和宝宝摄入不满意。",
        "how_to_interpret_answers": "精神好且吃奶后能安稳，通常比烦躁、嗜睡或持续不满足更安心，但仍要结合尿布和体重。",
        "do_not_infer": ["不要把吃奶后不安稳直接诊断为奶量不足", "不要同时追问体重和妈妈症状"],
    },
    "infant_growth_signal": {
        "why_this_field_matters": "体重趋势是判断奶量调整优先级的重要背景。",
        "how_to_interpret_answers": "体重增长正常时可偏向温和调整；增长慢或未知时，建议更谨慎并提示必要时联系专业支持。",
        "do_not_infer": ["不要要求用户提供精确称重数据", "不要把没称体重当成异常"],
    },
    "maternal_red_flags": {
        "why_this_field_matters": "发热、寒战、红肿、硬块或疼痛加重会影响是否适合继续普通奶量调整。",
        "how_to_interpret_answers": "没有红旗时可继续奶量分析；有红旗时优先提醒联系医生或 IBCLC，不直接推进强度计划。",
        "do_not_infer": ["不要给诊断或用药建议", "不要把轻微胀奶自动当成红旗"],
    },
    "maternal_breast_comfort": {
        "why_this_field_matters": "吸奶/亲喂后的舒适度会影响排空、频次调整和计划强度。",
        "how_to_interpret_answers": "吸完舒服更适合温和调整；持续胀、排不空或疼痛时，计划应更重视排空和风险边界。",
        "do_not_infer": ["不要把乳房不适直接等同于堵奶或乳腺炎", "不要追加睡眠、压力、经期等非状态机字段"],
    },
}
FLOW_JOINT_REASONING_GUIDANCE = [
    "7 天奶量偏低 + 记录完整 + 宝宝尿布/精神/体重都稳定：可以解释为趋势偏低但短期宝宝信号相对安心，完成采集后再评估是否温和追奶。",
    "7 天奶量偏低 + 尿布偏少、精神差或体重增长慢：不要急着生成计划，先强调谨慎观察并建议必要时联系专业支持。",
    "7 天奶量偏低 + 妈妈有发热、红肿、硬块或疼痛加重：普通奶量计划要让位于健康边界，优先建议联系医生或 IBCLC。",
    "记录可能漏记时，任何奶量结论都只能作为初步趋势，不能当成完整评估或直接生成计划依据。",
]

EVIDENCE_SOURCES: dict[str, dict[str, str]] = {
    "CDC_BREASTFEEDING_FREQUENCY": {
        "title": "CDC breastfeeding frequency and intake signals",
        "source": "CDC",
    },
    "CDC_PUMPING_BREAST_MILK": {
        "title": "CDC pumping breast milk guidance",
        "source": "CDC",
    },
    "ABM_MASTITIS_PROTOCOL": {
        "title": "ABM mastitis spectrum protocol",
        "source": "Academy of Breastfeeding Medicine",
    },
    "ABM_HYPERLACTATION_PROTOCOL": {
        "title": "ABM hyperlactation protocol",
        "source": "Academy of Breastfeeding Medicine",
    },
    "WHO_GROWTH_STANDARDS": {
        "title": "WHO child growth standards",
        "source": "WHO",
    },
}

MILK_WRITE_TOOL_NAMES = {
    "milk_record_mutate",
    "milk_plan_mutate",
    "milk_calendar_mutate",
    "milk_task_complete",
    "infant_growth_mutate",
}


def execute_milk_management_tool(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    name = str(args.get("_tool_name") or "")
    arguments = _with_user_id(args, inputs)
    if name in {"milk_status_query", "milk_task_complete", "infant_growth_mutate"} and not arguments.get("target_date"):
        runtime_date = _runtime_target_date(inputs)
        if runtime_date:
            arguments["target_date"] = runtime_date
    if name == "milk_plan_mutate":
        arguments = _arguments_with_cached_milk_plan_preview(arguments, inputs)
    if name == "milk_calendar_mutate":
        arguments = _arguments_with_cached_calendar_adjustment(arguments, inputs)
    if name == "milk_calendar_reschedule_preview":
        arguments = _arguments_with_calendar_reschedule_from_message(arguments, inputs)

    if name == "milk_snapshot_get":
        return dict(get_milk_context(**_pick(arguments, "user_id")))
    if name == "milk_status_query":
        return dict(query_milk_status(**_pick(arguments, "user_id", "section", "target_date", "trend_days", "growth_history_limit", "include_tasks")))
    if name == "milk_records_query":
        return dict(
            get_records_range(
                **_pick(arguments, "user_id", "start_at", "end_at", "record_scope", "include_raw_records", "summary_granularity", "limit")
            )
        )
    if name == "milk_record_mutate":
        return _mutate_record(arguments)
    if name == "milk_plan_query":
        return _query_plan(arguments)
    if name == "milk_plan_mutate":
        return _mutate_plan(arguments)
    if name == "milk_calendar_query":
        return _query_calendar(arguments, inputs)
    if name == "milk_calendar_change_preview":
        return dict(
            preview_calendar_adjustment(
                **_pick(
                    arguments,
                    "user_id",
                    "target_date",
                    "event_start_time",
                    "event_end_time",
                    "duration_minutes",
                    "content",
                    "item_type",
                    "plan_id",
                )
            )
        )
    if name == "milk_calendar_reschedule_preview":
        return dict(
            preview_day_reschedule(
                **_pick(
                    arguments,
                    "user_id",
                    "target_date",
                    "target_dates",
                    "start_date",
                    "end_date",
                    "busy_windows",
                    "adjustable_item_types",
                    "plan_id",
                    "default_duration_minutes",
                    "min_gap_minutes",
                    "include_busy_events",
                )
            )
        )
    if name == "milk_calendar_mutate":
        return _mutate_calendar(arguments)
    if name == "milk_task_complete":
        return dict(
            complete_milk_task(
                **_pick(
                    arguments,
                    "user_id",
                    "operation",
                    "target_date",
                    "task_id",
                    "item_id",
                    "record_kind",
                    "amount_ml",
                    "duration_minutes",
                    "occurred_at",
                    "title",
                    "delete_linked_record",
                    "idempotency_key",
                )
            )
        )
    if name == "milk_analysis_intake_manage":
        return _analysis_intake_manage(arguments, inputs)
    if name == "milk_analysis_evaluate":
        return _analysis_evaluate(arguments, inputs)
    if name == "milk_plan_preview_create":
        return _plan_preview_create(arguments, inputs)
    if name == "infant_growth_evaluate":
        return dict(evaluate_infant_growth(**_pick(arguments, "user_id", "infant_id", "as_of_time")))
    if name == "infant_growth_mutate":
        return dict(
            mutate_infant_growth(
                **_pick(
                    arguments,
                    "user_id",
                    "operation",
                    "growth_id",
                    "infant_id",
                    "height_cm",
                    "weight_kg",
                    "head_cm",
                    "target_date",
                    "history_limit",
                    "idempotency_key",
                )
            )
        )
    raise ValueError(f"Unknown milk-management tool: {name}")


def _milk_assessment_arguments(arguments: dict[str, Any], *, comprehensive_assessment: bool) -> dict[str, Any]:
    assessment_arguments = _pick(arguments, "user_id", "as_of_time", "window_days", "include_today")
    if comprehensive_assessment:
        assessment_arguments["window_days"] = 7
        assessment_arguments["include_today"] = False
    return assessment_arguments


def _analysis_intake_manage(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    state = inputs.get("_milk_management_state") if isinstance(inputs.get("_milk_management_state"), dict) else {}
    previous = _previous_analysis_intake_state(state)
    action = norm_text(arguments.get("action")) or "auto"
    user_update = norm_text(arguments.get("user_update")) or norm_text(inputs.get("user_message"))
    if _should_reset_previous_intake_for_action(action, previous, user_update):
        previous = {}
    flow = _flow_seed(previous)
    flow["goal"] = "milk_analysis"
    flow["user_update"] = user_update
    flow["as_of_time"] = arguments.get("as_of_time") or previous.get("as_of_time")

    records_result = _flow_records_result(arguments)
    records_data = records_result.get("data") if isinstance(records_result.get("data"), dict) else {}
    flow["records_snapshot"] = _flow_records_snapshot(records_data)

    infant_signals, maternal_symptoms = _flow_context_updates(arguments, flow, user_update)
    flow["infant_signals"] = infant_signals
    flow["maternal_symptoms"] = maternal_symptoms
    arguments = {**arguments, "user_update": user_update}
    if _flow_user_update_invalidates_assessment(action, user_update):
        flow.pop("assessment_result", None)
        flow.pop("plan_preview", None)

    plan_type = _flow_plan_type(arguments, flow)
    target_daily_ml = arguments.get("target_daily_ml")
    delta_ml = arguments.get("delta_ml")
    extracted_target_daily_ml, extracted_delta_ml = _workflow_plan_numbers_from_text(user_update)
    if target_daily_ml is None:
        target_daily_ml = flow.get("target_daily_ml") or extracted_target_daily_ml
    if delta_ml is None:
        delta_ml = flow.get("delta_ml") or extracted_delta_ml
    if plan_type:
        flow["plan_type"] = plan_type
    if target_daily_ml is not None:
        flow["target_daily_ml"] = target_daily_ml
    if delta_ml is not None:
        flow["delta_ml"] = delta_ml

    checklist = _flow_checklist(flow)
    flow["checklist"] = checklist
    missing = _flow_missing_fields(checklist)
    if missing:
        flow["stage"] = "intake_collecting"
        flow["current_field"] = missing[0]
        flow["next_question"] = _flow_question_for_field(missing[0])
        return _intake_collecting_result(flow, records_result)

    analysis_context = _analysis_context_from_flow(flow, records_result=records_result)
    flow["stage"] = "ready_to_evaluate"
    flow["current_field"] = None
    flow["next_question"] = None
    flow["analysis_context"] = analysis_context
    workflow_control = _flow_workflow_control(flow, missing_fields=[], allowed_next_action="call_tool")
    flow["workflow_control"] = workflow_control
    flow["progress"] = workflow_control.get("step")
    return {
        "ok": True,
        "status": "milk_analysis_ready_to_evaluate",
        "summary": "奶量分析信息采集已完成。",
        "data": {
            "intake_state": _analysis_intake_state_for_storage(flow),
            "flow_state": _analysis_intake_state_for_storage(flow),
            "analysis_context": analysis_context,
            "checklist": checklist,
            "missing_fields": [],
            "current_field": None,
            "next_question": None,
            "executed_step": "intake",
            "workflow_control": workflow_control,
            "progress": workflow_control.get("step"),
            "next_tool": "milk_analysis_evaluate",
        },
    }


def _analysis_evaluate(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    analysis_context = _analysis_context_from_args_or_state(arguments, inputs)
    if not analysis_context:
        return _needs_analysis_context_result()
    missing_fields = _analysis_context_missing_fields(analysis_context)
    if missing_fields:
        return _needs_complete_analysis_intake_result(analysis_context, missing_fields=missing_fields)
    flow = _flow_from_analysis_context(analysis_context)
    assessment_args = _flow_assessment_arguments(arguments, flow)
    assessment = _evaluate_milk_analysis_core(assessment_args, inputs)
    if not flow.get("plan_type"):
        flow["plan_type"] = _flow_plan_type_from_assessment(assessment)
    if flow.get("plan_type"):
        analysis_context = {**analysis_context, "plan_type": flow.get("plan_type")}
    assessment = _assessment_with_analysis_context_fingerprint(assessment, analysis_context)
    flow["assessment_result"] = assessment
    flow["stage"] = "analysis_ready"
    flow["analysis_context"] = analysis_context
    flow["checklist"] = analysis_context.get("checklist") if isinstance(analysis_context.get("checklist"), list) else _flow_checklist(flow)
    flow["next_question"] = _flow_analysis_next_question(assessment)

    data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
    flow_decision = data.get("milk_flow_decision") if isinstance(data.get("milk_flow_decision"), dict) else {}
    plan_decision = flow_decision.get("plan_decision") if isinstance(flow_decision.get("plan_decision"), dict) else {}
    next_tool = "milk_plan_preview_create" if plan_decision.get("can_start_plan") is True else None
    return {
        "ok": assessment.get("ok") is not False,
        "status": assessment.get("status") or "milk_analysis_ready",
        "summary": assessment.get("summary") or "奶量分析已完成。",
        "data": {
            "intake_state": _analysis_intake_state_for_storage(flow),
            "analysis_context": analysis_context,
            "assessment_result": assessment,
            "milk_flow_decision": flow_decision,
            "executed_step": "assessment",
            "next_tool": next_tool,
        },
        "assistant_followup": assessment.get("assistant_followup")
        if isinstance(assessment.get("assistant_followup"), dict)
        else {"message": str(flow.get("next_question") or "")},
    }


def _plan_preview_create(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    analysis_context = _analysis_context_from_args_or_state(arguments, inputs)
    if not analysis_context:
        return _needs_analysis_context_result()
    missing_fields = _analysis_context_missing_fields(analysis_context)
    if missing_fields:
        return _needs_complete_analysis_intake_result(
            analysis_context,
            missing_fields=missing_fields,
            status="milk_plan_needs_clinical_context",
            summary="生成奶量计划前需要先补齐宝宝状态和妈妈乳房情况。",
            ok=False,
            workflow_intent="milk_plan_preview_create",
            continuation_instruction=(
                "当前仍处于奶量计划生成流程。"
                "继续向用户确认 current_field 对应的信息；"
                "收集齐宝宝状态和妈妈乳房/全身状态后，再调用 milk_plan_preview_create 生成计划。"
                "不要一次追问多个缺失项，不要说最后一个或只差一个，也不要直接生成计划。"
            ),
        )
    assessment = _assessment_result_from_args_or_state(arguments, inputs, analysis_context=analysis_context)
    if not assessment:
        return {
            "ok": False,
            "status": "milk_plan_preview_needs_analysis_evaluation",
            "summary": "生成奶量计划前需要先完成奶量分析。",
            "data": {
                "analysis_context": analysis_context,
                "missing_fields": ["assessment_result"],
                "next_tool": "milk_analysis_evaluate",
            },
            "assistant_followup": {"message": "我已经拿到关键信息了，先完成奶量分析，再继续生成计划。"},
        }

    flow = _flow_from_analysis_context(analysis_context)
    flow["assessment_result"] = assessment
    user_update = norm_text(arguments.get("user_update")) or norm_text(inputs.get("user_message"))
    preview_arguments = {**arguments, "user_update": user_update}
    flow["user_update"] = user_update
    flow["plan_type"] = _flow_plan_type(preview_arguments, flow) or _flow_plan_type_from_assessment(assessment)
    target_daily_ml = arguments.get("target_daily_ml")
    delta_ml = arguments.get("delta_ml")
    extracted_target_daily_ml, extracted_delta_ml = _workflow_plan_numbers_from_text(norm_text(inputs.get("user_message")))
    if target_daily_ml is None:
        target_daily_ml = analysis_context.get("target_daily_ml") or extracted_target_daily_ml
    if delta_ml is None:
        delta_ml = analysis_context.get("delta_ml") or extracted_delta_ml
    if target_daily_ml is not None:
        flow["target_daily_ml"] = target_daily_ml
    if delta_ml is not None:
        flow["delta_ml"] = delta_ml

    if not _flow_plan_type_from_flow(flow):
        return {
            "ok": False,
            "status": "milk_plan_preview_missing_plan_type",
            "summary": "缺少 plan_type，无法生成新的奶量计划草稿。",
            "data": {
                "analysis_context": analysis_context,
                "assessment_result": assessment,
                "missing_fields": ["plan_type"],
                "milk_flow_decision": _milk_flow_decision_for_missing_plan_type(),
                "next_tool": "milk_plan_preview_create",
            },
            "assistant_followup": {"message": "我先确认一下方向，这样不会帮你排偏：你现在更想追奶、稳奶，还是减奶？"},
        }

    preview_args = _flow_plan_preview_arguments(preview_arguments, flow)
    preview = _with_milk_plan_card(_create_milk_plan_preview_core(preview_args, inputs))
    preview_data = preview.setdefault("data", {})
    if isinstance(preview_data, dict):
        updated_context = _analysis_context_from_flow(flow, records_result=None)
        updated_context.update({key: value for key, value in analysis_context.items() if key not in updated_context})
        updated_context["plan_type"] = flow.get("plan_type")
        updated_context["target_daily_ml"] = flow.get("target_daily_ml")
        updated_context["delta_ml"] = flow.get("delta_ml")
        flow["analysis_context"] = updated_context
        if str(preview.get("status") or "").strip() == "plan_preview_ready":
            flow["plan_preview"] = _flow_plan_preview_state(preview)
            flow["stage"] = "plan_preview"
            preview_data["plan_preview"] = flow["plan_preview"]
        else:
            flow["plan_preview"] = {}
            flow["stage"] = "analysis_ready"
        preview_data["analysis_context"] = updated_context
        preview_data["assessment_result"] = assessment
        preview_data["intake_state"] = _analysis_intake_state_for_storage(flow)
        preview_data["executed_step"] = "plan_preview"
    return preview


def _previous_analysis_intake_state(state: dict[str, Any]) -> dict[str, Any]:
    intake = state.get("analysis_intake") if isinstance(state.get("analysis_intake"), dict) else {}
    return intake


def _should_reset_previous_intake_for_action(action: str, previous: dict[str, Any], user_update: str) -> bool:
    if action == "reset":
        return True
    current_field = norm_text(previous.get("current_field"))
    if action == "auto" and previous and _user_update_starts_new_milk_analysis(user_update):
        if current_field and _flow_answer_matches_current_field(current_field, user_update):
            return False
        return True
    if action != "start":
        return False
    if current_field and _flow_answer_matches_current_field(current_field, user_update):
        return False
    return True


def _user_update_starts_new_milk_analysis(user_update: str) -> bool:
    text = norm_text(user_update)
    if not text:
        return False
    phrases = (
        "重新分析",
        "再分析",
        "分析最近",
        "后台奶量分析提醒",
        "生成奶量计划",
        "制定奶量计划",
        "做奶量计划",
        "追奶计划",
        "稳奶计划",
        "减奶计划",
    )
    return any(phrase in text for phrase in phrases)


def _intake_collecting_result(flow: dict[str, Any], records_result: dict[str, Any]) -> dict[str, Any]:
    missing = _flow_missing_fields(flow.get("checklist") if isinstance(flow.get("checklist"), list) else [])
    status = "milk_analysis_intake_needs_records" if missing and missing[0] == "records_7d" else "milk_analysis_intake_collecting"
    next_question = str(flow.get("next_question") or "").strip()
    workflow_control = _flow_workflow_control(flow, missing_fields=missing, allowed_next_action="ask_user")
    flow["workflow_control"] = workflow_control
    flow["progress"] = workflow_control.get("step")
    field_guidance = _flow_field_guidance(flow.get("current_field"))
    joint_reasoning_guidance = _flow_joint_reasoning_guidance()
    quick_replies = _flow_quick_replies(flow.get("current_field"))
    flow["field_guidance"] = field_guidance
    flow["joint_reasoning_guidance"] = joint_reasoning_guidance
    return {
        "ok": True,
        "status": status,
        "summary": "奶量分析信息采集中。",
        "data": {
            "intake_state": _analysis_intake_state_for_storage(flow),
            "flow_state": _analysis_intake_state_for_storage(flow),
            "checklist": flow.get("checklist", []),
            "missing_fields": missing,
            "current_field": flow.get("current_field"),
            "next_question": next_question,
            "remaining_fields": missing,
            "remaining_count": len(missing),
            "workflow_control": workflow_control,
            "progress": workflow_control.get("step"),
            "field_guidance": field_guidance,
            "joint_reasoning_guidance": joint_reasoning_guidance,
            "quick_replies": quick_replies,
            "assistant_instruction": (
                "最终回复只问 next_question 这一项；不要说最后一个、最后再问、只差一个或再确认最后一个；"
                "不要同时追问其它 missing_fields，也不要直接输出奶量结论或计划；"
                "在最终回复前调用 ui_quick_replies_create 创建恰好 3 个快捷输入，优先使用 quick_replies。"
            ),
            "records_result": records_result,
            "executed_step": "intake",
            "next_tool": "milk_analysis_intake_manage",
        },
        "assistant_followup": {"message": next_question},
    }


def _flow_workflow_control(flow: dict[str, Any], *, missing_fields: list[str], allowed_next_action: str) -> dict[str, Any]:
    current_field = norm_text(flow.get("current_field")) or (missing_fields[0] if missing_fields else "")
    next_question = norm_text(flow.get("next_question"))
    step = _flow_progress(flow.get("checklist") if isinstance(flow.get("checklist"), list) else [], current_field)
    awaiting_user_input = allowed_next_action == "ask_user"
    required_next_tool = "milk_analysis_intake_manage" if awaiting_user_input else "milk_analysis_evaluate"
    return _drop_empty_context(
        {
            "phase": "intake_collecting" if awaiting_user_input else "ready_to_evaluate",
            "allowed_next_action": allowed_next_action,
            "awaiting_user_input": awaiting_user_input,
            "required_next_tool": required_next_tool,
            "step": step,
            "current_field": current_field,
            "current_label": FLOW_FIELD_LABELS.get(current_field),
            "next_question": next_question,
            "response_contract": {
                "only_ask_current_question": awaiting_user_input,
                "forbid_last_question_wording": True,
                "do_not_continue_tools_before_user_answer": awaiting_user_input,
                "resume_confirmation_question": "我们要继续刚才的奶量分析流程吗？",
            },
        }
    )


def _flow_progress(checklist: list[dict[str, Any]], current_field: str) -> dict[str, Any]:
    completed = [item for item in checklist if isinstance(item, dict) and item.get("status") == "collected"]
    field_order = [str(item.get("id") or "") for item in checklist if isinstance(item, dict)] or FLOW_REQUIRED_FIELDS
    total = len(field_order) or len(FLOW_REQUIRED_FIELDS)
    index = field_order.index(current_field) + 1 if current_field in field_order else min(len(completed) + 1, total)
    remaining_count = len([item for item in checklist if isinstance(item, dict) and item.get("status") != "collected"])
    return {
        "index": index,
        "total": total,
        "completed_count": len(completed),
        "remaining_count": remaining_count,
        "current_field": current_field,
        "current_label": FLOW_FIELD_LABELS.get(current_field),
        "display": f"第 {index}/{total} 项" if total else "",
    }


def _flow_field_guidance(field: Any) -> dict[str, Any]:
    field_id = norm_text(field)
    guidance = FLOW_FIELD_GUIDANCE.get(field_id, {})
    return dict(guidance) if isinstance(guidance, dict) else {}


def _flow_joint_reasoning_guidance() -> list[str]:
    return list(FLOW_JOINT_REASONING_GUIDANCE)


def _flow_quick_replies(field: Any) -> list[dict[str, str]]:
    field_id = norm_text(field)
    replies = FLOW_FIELD_QUICK_REPLIES.get(field_id, [])
    return [{"text": text} for text in replies[:3]]


def _analysis_context_from_flow(flow: dict[str, Any], *, records_result: dict[str, Any] | None) -> dict[str, Any]:
    records_snapshot = flow.get("records_snapshot") if isinstance(flow.get("records_snapshot"), dict) else {}
    context = {
        "as_of_time": flow.get("as_of_time"),
        "records_snapshot": records_snapshot,
        "records_interpretation": {
            "status": records_snapshot.get("status"),
            "valid_days": records_snapshot.get("valid_days"),
            "positive_days": records_snapshot.get("positive_days"),
            "record_counts": records_snapshot.get("record_counts"),
        },
        "source_records": {
            "daily_rollups": records_snapshot.get("daily_rollups") if isinstance(records_snapshot.get("daily_rollups"), list) else [],
            "raw_records": records_snapshot.get("raw_records") if isinstance(records_snapshot.get("raw_records"), dict) else {},
        },
        "infant_signals": flow.get("infant_signals") if isinstance(flow.get("infant_signals"), dict) else {},
        "maternal_symptoms": flow.get("maternal_symptoms") if isinstance(flow.get("maternal_symptoms"), dict) else {},
        "checklist": flow.get("checklist") if isinstance(flow.get("checklist"), list) else [],
        "plan_type": flow.get("plan_type"),
        "target_daily_ml": flow.get("target_daily_ml"),
        "delta_ml": flow.get("delta_ml"),
    }
    if isinstance(records_result, dict):
        context["records_result_status"] = records_result.get("status")
        records_data = records_result.get("data") if isinstance(records_result.get("data"), dict) else {}
        if records_data:
            context["records_assessment_data"] = records_data
    return _drop_empty_context(context)


def _analysis_intake_state_for_storage(flow: dict[str, Any]) -> dict[str, Any]:
    stored = _flow_state_for_storage(flow)
    analysis_context = _analysis_context_from_intake_state({**flow, **stored})
    if analysis_context:
        stored["analysis_context"] = analysis_context
    return stored


def _analysis_context_from_args_or_state(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    explicit = _parse_json_object(arguments.get("analysis_context"))
    state_context = _analysis_context_from_state(inputs)
    if _analysis_context_is_complete(state_context):
        return state_context
    if _analysis_context_is_complete(explicit):
        return explicit
    if state_context:
        return state_context
    if explicit:
        return explicit
    return {}


def _analysis_context_from_state(inputs: RuntimeInputs) -> dict[str, Any]:
    state = inputs.get("_milk_management_state")
    if not isinstance(state, dict):
        return {}
    candidate = state.get("analysis_intake") if isinstance(state.get("analysis_intake"), dict) else {}
    context = _analysis_context_from_intake_state(candidate)
    if context:
        return context
    if candidate and isinstance(candidate.get("records_snapshot"), dict):
        checklist = candidate.get("checklist") if isinstance(candidate.get("checklist"), list) else _flow_checklist(candidate)
        if _flow_missing_fields(checklist):
            return {}
        return _analysis_context_from_flow(candidate, records_result=None)
    return {}


def _analysis_context_from_intake_state(intake: dict[str, Any]) -> dict[str, Any]:
    if not intake:
        return {}
    existing = intake.get("analysis_context") if isinstance(intake.get("analysis_context"), dict) else {}
    derived = _analysis_context_from_flow(intake, records_result=None)
    if not existing:
        return derived
    if not derived:
        return existing
    return _drop_empty_context({**existing, **derived})


def _analysis_context_is_complete(context: dict[str, Any]) -> bool:
    if not context:
        return False
    return not _analysis_context_missing_fields(context)


def _assessment_result_from_args_or_state(
    arguments: dict[str, Any],
    inputs: RuntimeInputs,
    *,
    analysis_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    explicit = _parse_json_object(arguments.get("assessment_result"))
    if explicit:
        if _assessment_matches_analysis_context(explicit, analysis_context, allow_unfingerprinted=_can_accept_unfingerprinted_explicit_assessment(inputs)):
            return explicit
    state = inputs.get("_milk_management_state")
    if not isinstance(state, dict):
        return {}
    candidate = state.get("analysis_intake") if isinstance(state.get("analysis_intake"), dict) else {}
    assessment = candidate.get("assessment_result") if isinstance(candidate.get("assessment_result"), dict) else {}
    if assessment and _assessment_matches_analysis_context(assessment, analysis_context, allow_unfingerprinted=False):
        return assessment
    return {}


def _can_accept_unfingerprinted_explicit_assessment(inputs: RuntimeInputs) -> bool:
    state = inputs.get("_milk_management_state")
    if not isinstance(state, dict):
        return True
    return not isinstance(state.get("analysis_intake"), dict)


def _assessment_with_analysis_context_fingerprint(assessment: dict[str, Any], analysis_context: dict[str, Any]) -> dict[str, Any]:
    if not assessment:
        return assessment
    fingerprint = _analysis_context_fingerprint(analysis_context)
    if not fingerprint:
        return assessment
    copied = dict(assessment)
    copied["analysis_context_fingerprint"] = fingerprint
    data = copied.get("data")
    if isinstance(data, dict):
        copied["data"] = {**data, "analysis_context_fingerprint": fingerprint}
    return copied


def _assessment_matches_analysis_context(
    assessment: dict[str, Any],
    analysis_context: dict[str, Any] | None,
    *,
    allow_unfingerprinted: bool,
) -> bool:
    if not assessment:
        return False
    if not analysis_context:
        return True
    expected = _analysis_context_fingerprint(analysis_context)
    if not expected:
        return True
    actual = norm_text(assessment.get("analysis_context_fingerprint"))
    data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
    if not actual:
        actual = norm_text(data.get("analysis_context_fingerprint"))
    if not actual:
        return allow_unfingerprinted
    return actual == expected


def _analysis_context_fingerprint(analysis_context: dict[str, Any]) -> str:
    if not analysis_context:
        return ""
    payload = {
        "records_snapshot": analysis_context.get("records_snapshot") if isinstance(analysis_context.get("records_snapshot"), dict) else {},
        "infant_signals": analysis_context.get("infant_signals") if isinstance(analysis_context.get("infant_signals"), dict) else {},
        "maternal_symptoms": analysis_context.get("maternal_symptoms") if isinstance(analysis_context.get("maternal_symptoms"), dict) else {},
        "checklist": analysis_context.get("checklist") if isinstance(analysis_context.get("checklist"), list) else [],
        "plan_type": analysis_context.get("plan_type"),
        "target_daily_ml": analysis_context.get("target_daily_ml"),
        "delta_ml": analysis_context.get("delta_ml"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]


def _flow_from_analysis_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": "ready_to_evaluate",
        "goal": "milk_analysis",
        "as_of_time": context.get("as_of_time"),
        "records_snapshot": context.get("records_snapshot") if isinstance(context.get("records_snapshot"), dict) else {},
        "infant_signals": context.get("infant_signals") if isinstance(context.get("infant_signals"), dict) else {},
        "maternal_symptoms": context.get("maternal_symptoms") if isinstance(context.get("maternal_symptoms"), dict) else {},
        "checklist": context.get("checklist") if isinstance(context.get("checklist"), list) else [],
        "plan_type": norm_text(context.get("plan_type")),
        "target_daily_ml": context.get("target_daily_ml"),
        "delta_ml": context.get("delta_ml"),
        "analysis_context": context,
    }


def _needs_analysis_context_result() -> dict[str, Any]:
    return {
        "ok": False,
        "status": "milk_analysis_needs_intake_context",
        "summary": "奶量分析前需要先完成信息采集。",
        "data": {
            "missing_fields": ["analysis_context"],
            "next_tool": "milk_analysis_intake_manage",
        },
        "assistant_followup": {"message": "我先把近期记录和宝宝、妈妈状态补齐，再继续分析。"},
    }


def _analysis_context_missing_fields(analysis_context: dict[str, Any]) -> list[str]:
    checklist = analysis_context.get("checklist") if isinstance(analysis_context.get("checklist"), list) else []
    flow = _flow_from_analysis_context(analysis_context)
    derived_missing = _flow_missing_fields(_flow_checklist(flow))
    if derived_missing:
        return derived_missing
    if checklist:
        return _flow_missing_fields(checklist)
    return []


def _needs_complete_analysis_intake_result(
    analysis_context: dict[str, Any],
    *,
    missing_fields: list[str],
    status: str = "milk_analysis_intake_collecting",
    summary: str = "奶量分析前需要先补齐宝宝状态和妈妈乳房情况。",
    ok: bool = True,
    workflow_intent: str = "milk_analysis",
    continuation_instruction: str | None = None,
) -> dict[str, Any]:
    flow = _flow_from_analysis_context(analysis_context)
    checklist = _flow_checklist(flow)
    derived_missing = _flow_missing_fields(checklist)
    if derived_missing:
        missing_fields = derived_missing
    current_field = missing_fields[0] if missing_fields else None
    next_question = _flow_question_for_field(current_field) if current_field else ""
    flow["stage"] = "intake_collecting"
    flow["checklist"] = checklist
    flow["current_field"] = current_field
    flow["next_question"] = next_question
    workflow_control = _flow_workflow_control(flow, missing_fields=missing_fields, allowed_next_action="ask_user")
    flow["workflow_control"] = workflow_control
    flow["progress"] = workflow_control.get("step")
    field_guidance = _flow_field_guidance(current_field)
    flow["field_guidance"] = field_guidance
    flow["joint_reasoning_guidance"] = _flow_joint_reasoning_guidance()
    data = {
        "intake_state": _analysis_intake_state_for_storage(flow),
        "flow_state": _analysis_intake_state_for_storage(flow),
        "analysis_context": analysis_context,
        "checklist": checklist,
        "missing_fields": missing_fields,
        "current_field": current_field,
        "next_question": next_question,
        "remaining_count": len(missing_fields),
        "suggested_questions": [next_question] if next_question else [],
        "workflow_control": workflow_control,
        "progress": workflow_control.get("step"),
        "field_guidance": field_guidance,
        "joint_reasoning_guidance": _flow_joint_reasoning_guidance(),
        "quick_replies": _flow_quick_replies(current_field),
        "workflow_intent": workflow_intent,
        "milk_flow_decision": _milk_flow_decision_for_missing_context(
            missing_fields,
            stage="need_more_user_context",
            next_tool="milk_analysis_intake_manage",
        ),
        "executed_step": "intake_guard",
        "next_tool": "milk_analysis_intake_manage",
    }
    if continuation_instruction:
        data["continuation_instruction"] = continuation_instruction
    return {
        "ok": ok,
        "status": status,
        "summary": summary,
        "data": data,
        "assistant_followup": {"message": next_question},
    }


def _drop_empty_context(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in (None, "", [], {})
    }


def _flow_seed(previous: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": norm_text(previous.get("stage")) or "intake_collecting",
        "goal": norm_text(previous.get("goal")) or "milk_analysis",
        "current_field": norm_text(previous.get("current_field")),
        "infant_signals": previous.get("infant_signals") if isinstance(previous.get("infant_signals"), dict) else {},
        "maternal_symptoms": previous.get("maternal_symptoms") if isinstance(previous.get("maternal_symptoms"), dict) else {},
        "plan_type": norm_text(previous.get("plan_type")),
        "target_daily_ml": previous.get("target_daily_ml"),
        "delta_ml": previous.get("delta_ml"),
        "assessment_result": previous.get("assessment_result") if isinstance(previous.get("assessment_result"), dict) else {},
        "plan_preview": previous.get("plan_preview") if isinstance(previous.get("plan_preview"), dict) else {},
    }


def _flow_records_result(arguments: dict[str, Any]) -> dict[str, Any]:
    return dict(
        evaluate_milk_status(
            user_id=arguments["user_id"],
            as_of_time=arguments.get("as_of_time"),
            window_days=7,
            include_today=False,
        )
    )


def _flow_records_snapshot(records_data: dict[str, Any]) -> dict[str, Any]:
    source = records_data.get("source_record_context") if isinstance(records_data.get("source_record_context"), dict) else {}
    normality = records_data.get("milk_normality") if isinstance(records_data.get("milk_normality"), dict) else {}
    stats = normality.get("stats") if isinstance(normality.get("stats"), dict) else {}
    days = normality.get("days") if isinstance(normality.get("days"), list) else []
    valid_days = to_int(stats.get("valid_days"), 0)
    positive_days = [
        day
        for day in days
        if isinstance(day, dict)
        and day.get("ok") is True
        and _to_float(day.get("estimated_daily_milk_ml"), 0.0) > 0
    ]
    counts = source.get("record_counts") if isinstance(source.get("record_counts"), dict) else {}
    return {
        "status": "collected" if valid_days > 0 and positive_days else "missing",
        "valid_days": valid_days,
        "positive_days": len(positive_days),
        "record_counts": counts,
        "daily_rollups": source.get("daily_rollups") if isinstance(source.get("daily_rollups"), list) else [],
        "raw_records": source.get("raw_records") if isinstance(source.get("raw_records"), dict) else {},
    }


def _flow_context_updates(arguments: dict[str, Any], flow: dict[str, Any], user_update: str) -> tuple[dict[str, Any], dict[str, Any]]:
    infant = dict(flow.get("infant_signals") if isinstance(flow.get("infant_signals"), dict) else {})
    maternal = dict(flow.get("maternal_symptoms") if isinstance(flow.get("maternal_symptoms"), dict) else {})
    inferred_infant, inferred_maternal = _workflow_infer_context_from_text(user_update)
    infant.update(
        _trusted_infant_signal_argument_updates(
            inferred_infant,
            current_field=flow.get("current_field"),
            user_update=user_update,
        )
    )
    maternal.update(
        _trusted_maternal_symptom_argument_updates(
            inferred_maternal,
            current_field=flow.get("current_field"),
            user_update=user_update,
        )
    )
    infant.update(
        _trusted_infant_signal_argument_updates(
            _normalized_options(arguments.get("infant_signals")),
            current_field=flow.get("current_field"),
            user_update=user_update,
        )
    )
    maternal.update(
        _trusted_maternal_symptom_argument_updates(
            _normalized_options(arguments.get("maternal_symptoms")),
            current_field=flow.get("current_field"),
            user_update=user_update,
        )
    )
    _flow_apply_current_field_answer(flow.get("current_field"), user_update, infant, maternal)
    return infant, maternal


def _trusted_infant_signal_argument_updates(
    updates: dict[str, Any],
    *,
    current_field: Any,
    user_update: str,
) -> dict[str, Any]:
    if not updates:
        return {}
    trusted = dict(updates)
    if not _can_accept_infant_signal_group(current_field=current_field, user_update=user_update, group="wet_diapers"):
        trusted.pop("wet_diapers_24h", None)
    if not _can_accept_infant_signal_group(current_field=current_field, user_update=user_update, group="state"):
        for key in ("baby_state", "feeding_satisfaction", "poor_feeding", "poor_latch", "lethargy"):
            trusted.pop(key, None)
    if not _can_accept_infant_signal_group(current_field=current_field, user_update=user_update, group="growth"):
        for key in ("recent_weight", "weight_trend", "growth_concern"):
            trusted.pop(key, None)
    return trusted


def _can_accept_infant_signal_group(*, current_field: Any, user_update: str, group: str) -> bool:
    field = norm_text(current_field)
    text = norm_text(user_update)
    if not text:
        return False
    if group == "wet_diapers":
        if field == "infant_wet_diapers" and _flow_answer_matches_current_field(field, text):
            return True
        return _text_mentions_wet_diapers(text)
    if group == "state":
        if field == "infant_state_or_satisfaction" and _flow_answer_matches_current_field(field, text):
            return True
        return _text_mentions_infant_state(text)
    if group == "growth":
        if field == "infant_growth_signal" and _flow_answer_matches_current_field(field, text):
            return True
        return _text_answers_infant_growth(text)
    return False


def _trusted_maternal_symptom_argument_updates(
    updates: dict[str, Any],
    *,
    current_field: Any,
    user_update: str,
) -> dict[str, Any]:
    if not updates:
        return {}
    trusted = dict(updates)
    if _can_accept_red_flag_argument_updates(current_field=current_field, user_update=user_update):
        return trusted
    for key in FLOW_MATERNAL_RED_FLAG_FIELDS:
        trusted.pop(key, None)
    return trusted


def _can_accept_red_flag_argument_updates(*, current_field: Any, user_update: str) -> bool:
    if norm_text(current_field) == "maternal_red_flags":
        return True
    text = norm_text(user_update)
    if not text:
        return False
    red_flag_terms = ("发热", "发烧", "寒战", "发冷", "红肿", "红热", "发红", "硬块", "肿块", "结块", "疼痛加重", "越来越痛", "更痛")
    return any(term in text for term in red_flag_terms)


def _flow_user_update_invalidates_assessment(action: str, user_update: str) -> bool:
    if action == "get_state":
        return False
    return bool(norm_text(user_update))


def _flow_apply_current_field_answer(current_field: Any, user_update: str, infant: dict[str, Any], maternal: dict[str, Any]) -> None:
    field = norm_text(current_field)
    text = norm_text(user_update)
    if not field or not text:
        return
    if not _flow_answer_matches_current_field(field, text):
        return
    negative = any(token in text for token in ("没有", "没", "无", "否认", "不发", "不红", "不痛"))
    normal = any(token in text for token in ("正常", "还好", "可以", "稳定", "没问题", "不少"))
    if field == "infant_wet_diapers":
        infant.setdefault("wet_diapers_24h", text)
    elif field == "infant_state_or_satisfaction":
        infant.setdefault("baby_state", text)
        infant.setdefault("feeding_satisfaction", text)
    elif field == "infant_growth_signal":
        infant.setdefault("weight_trend", text)
        infant.setdefault("recent_weight", text)
    elif field == "maternal_red_flags":
        if negative or normal:
            maternal.update({"fever": False, "chills": False, "breast_redness": False, "lump_or_hard_area": False, "worsening_pain": False})
        else:
            maternal.setdefault("symptom_text", text)
    elif field == "maternal_breast_comfort":
        if any(token in text for token in ("胀", "涨", "排不空", "硬", "痛", "疼", "不舒服")):
            maternal["breast_fullness"] = True
            maternal["incomplete_emptying"] = True
        else:
            maternal.setdefault("symptom_text", text)


def _flow_answer_matches_current_field(field: str, text: str) -> bool:
    if field == "records_7d":
        return _text_answers_record_completeness(text)
    if field == "infant_wet_diapers":
        return _text_answers_wet_diapers(text)
    if field == "infant_state_or_satisfaction":
        return _text_answers_infant_state(text)
    if field == "infant_growth_signal":
        return _text_answers_infant_growth(text, allow_short_answer=True)
    if field == "maternal_red_flags":
        return _text_answers_maternal_red_flags(text)
    if field == "maternal_breast_comfort":
        return _text_answers_maternal_breast_comfort(text)
    return True


def _text_answers_record_completeness(text: str) -> bool:
    return any(
        token in text
        for token in (
            "漏记",
            "漏了",
            "没漏",
            "没有漏",
            "记录完整",
            "记录是完整",
            "完整的",
            "都记",
            "都有记",
            "全记",
            "补记录",
            "补充记录",
        )
    )


def _text_answers_wet_diapers(text: str) -> bool:
    if _text_mentions_wet_diapers(text):
        return True
    return any(token in text for token in ("挺多", "有点少", "偏少", "不少", "不太确定", "几片", "几次"))


def _text_mentions_wet_diapers(text: str) -> bool:
    if any(token in text for token in ("尿布", "尿量", "小便", "尿片", "纸尿裤", "尿湿", "尿")):
        return True
    return False


def _text_answers_infant_state(text: str) -> bool:
    if _text_mentions_infant_state(text):
        return True
    return any(
        token in text
        for token in (
            "变化不明显",
            "还不错",
            "不安稳",
            "正常",
            "还好",
            "可以",
            "稳定",
            "说不准",
            "不确定",
            "不太确定",
            "没太注意",
            "没注意",
            "不清楚",
            "不好说",
        )
    )


def _text_mentions_infant_state(text: str) -> bool:
    return any(token in text for token in ("精神", "状态", "吃奶", "吃完", "安稳", "哭闹", "满足", "烦躁", "嗜睡"))


def _text_answers_infant_growth(text: str, *, allow_short_answer: bool = False) -> bool:
    if any(token in text for token in ("体重", "增长", "称重", "称过", "没称", "长得", "长胖", "掉秤")):
        return True
    if not allow_short_answer:
        return False
    return any(
        token in text
        for token in (
            "看起来正常",
            "看着正常",
            "正常",
            "还好",
            "可以",
            "稳定",
            "没问题",
            "不太确定",
            "不确定",
            "说不准",
            "不清楚",
            "不知道",
            "没注意",
            "没太注意",
            "有点慢",
            "偏慢",
        )
    )


def _text_answers_maternal_red_flags(text: str) -> bool:
    if any(token in text for token in ("这些情况", "红旗", "异常")):
        return True
    return any(token in text for token in ("发热", "发烧", "寒战", "发冷", "红肿", "红热", "发红", "硬块", "肿块", "结块", "疼痛加重", "越来越痛", "更痛", "没有", "没", "无", "否认"))


def _text_answers_maternal_breast_comfort(text: str) -> bool:
    if any(token in text for token in ("乳房", "吸完", "亲喂后", "舒服", "胀", "涨", "排不空", "疼", "痛", "硬", "不舒服")):
        return True
    return any(token in text for token in ("还好", "正常", "可以", "稳定"))


def _flow_checklist(flow: dict[str, Any]) -> list[dict[str, Any]]:
    records = flow.get("records_snapshot") if isinstance(flow.get("records_snapshot"), dict) else {}
    infant = flow.get("infant_signals") if isinstance(flow.get("infant_signals"), dict) else {}
    maternal = flow.get("maternal_symptoms") if isinstance(flow.get("maternal_symptoms"), dict) else {}
    checks = {
        "records_7d": records.get("status") == "collected",
        "infant_wet_diapers": _flow_has_value(infant.get("wet_diapers_24h")),
        "infant_state_or_satisfaction": any(_flow_has_value(infant.get(key)) for key in ("baby_state", "feeding_satisfaction", "poor_feeding", "poor_latch", "lethargy")),
        "infant_growth_signal": any(_flow_has_value(infant.get(key)) for key in ("recent_weight", "weight_trend", "growth_concern")),
        "maternal_red_flags": _has_maternal_red_flag_answer(maternal),
        "maternal_breast_comfort": _has_positive_maternal_red_flag(maternal) or any(
            key in maternal
            for key in ("breast_fullness", "engorgement", "post_pump_fullness", "incomplete_emptying", "pain_level", "symptom_text")
        ),
    }
    return [
        {
            "id": field,
            "label": FLOW_FIELD_LABELS[field],
            "status": "collected" if checks.get(field) else "missing",
        }
        for field in FLOW_REQUIRED_FIELDS
    ]


def _flow_has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _has_maternal_red_flag_answer(maternal: dict[str, Any]) -> bool:
    if all(key in maternal for key in FLOW_MATERNAL_RED_FLAG_FIELDS):
        return True
    return _has_positive_maternal_red_flag(maternal)


def _has_positive_maternal_red_flag(maternal: dict[str, Any]) -> bool:
    return any(to_bool(maternal.get(key)) for key in FLOW_MATERNAL_RED_FLAG_FIELDS)


def _flow_missing_fields(checklist: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("id")) for item in checklist if item.get("status") != "collected"]


def _flow_question_for_field(field: str) -> str:
    if field == "records_7d":
        return "过去 7 天好像还缺少可计算的奶量记录。需要先补充吸奶、瓶喂母乳或补奶记录吗？"
    if field == "infant_wet_diapers":
        return "宝宝近 24 小时尿量或尿布情况大概怎么样？"
    if field == "infant_state_or_satisfaction":
        return "宝宝精神状态怎么样，吃奶后通常能安稳一会儿吗？"
    if field == "infant_growth_signal":
        return "宝宝最近体重增长看起来还正常吗？"
    if field == "maternal_red_flags":
        return "你有没有发热、寒战、乳房明显红肿、硬块，或疼痛越来越重？"
    if field == "maternal_breast_comfort":
        return "吸奶或亲喂后乳房是比较舒服，还是还会胀、排不空或疼？"
    return "我还需要再确认一个会影响判断的信息。"


def _flow_assessment_arguments(arguments: dict[str, Any], flow: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": arguments["user_id"],
        "as_of_time": arguments.get("as_of_time") or flow.get("as_of_time"),
        "window_days": 7,
        "include_today": False,
        "comprehensive_assessment": True,
        "workflow_intent": "milk_analysis",
        "infant_signals": flow.get("infant_signals") if isinstance(flow.get("infant_signals"), dict) else {},
        "maternal_symptoms": flow.get("maternal_symptoms") if isinstance(flow.get("maternal_symptoms"), dict) else {},
    }


def _flow_plan_type(arguments: dict[str, Any], flow: dict[str, Any]) -> str | None:
    explicit = norm_text(arguments.get("plan_type"))
    if explicit in ALL_PLAN_TYPES:
        return explicit
    value = norm_text(flow.get("plan_type"))
    if value in ALL_PLAN_TYPES:
        return value
    text = norm_text(arguments.get("user_update"))
    if "减奶" in text:
        return PLAN_TYPE_DECREASE
    if "稳奶" in text:
        return PLAN_TYPE_MAINTAIN
    if "追奶" in text or "增加" in text or "每天多" in text:
        return PLAN_TYPE_INCREASE
    return None


def _flow_plan_type_from_assessment(assessment: dict[str, Any]) -> str | None:
    data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
    flow_decision = data.get("milk_flow_decision") if isinstance(data.get("milk_flow_decision"), dict) else {}
    plan_decision = flow_decision.get("plan_decision") if isinstance(flow_decision.get("plan_decision"), dict) else {}
    value = norm_text(plan_decision.get("recommended_plan_type"))
    return value if value in ALL_PLAN_TYPES else None


def _flow_plan_type_from_flow(flow: dict[str, Any]) -> str | None:
    value = norm_text(flow.get("plan_type"))
    return value if value in ALL_PLAN_TYPES else None


def _flow_plan_preview_arguments(arguments: dict[str, Any], flow: dict[str, Any]) -> dict[str, Any]:
    options = _normalized_options(arguments.get("options"))
    assessment = flow.get("assessment_result") if isinstance(flow.get("assessment_result"), dict) else {}
    assessment_data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
    clinical = assessment_data.get("clinical_assessment") if isinstance(assessment_data.get("clinical_assessment"), dict) else {}
    if assessment_data:
        options["prepared_assessment"] = assessment_data
    if clinical:
        options["prepared_clinical_assessment"] = clinical
    if isinstance(clinical.get("growth_assessment"), dict):
        options["prepared_growth_assessment"] = clinical["growth_assessment"]
    options["infant_signals"] = flow.get("infant_signals") if isinstance(flow.get("infant_signals"), dict) else {}
    options["maternal_symptoms"] = flow.get("maternal_symptoms") if isinstance(flow.get("maternal_symptoms"), dict) else {}
    checklist = flow.get("checklist") if isinstance(flow.get("checklist"), list) else []
    if checklist and not _flow_missing_fields(checklist):
        options["_analysis_intake_complete"] = True
    return {
        "user_id": arguments["user_id"],
        "plan_type": _flow_plan_type_from_flow(flow),
        "plan_days": arguments.get("plan_days"),
        "target_daily_ml": flow.get("target_daily_ml"),
        "delta_ml": flow.get("delta_ml"),
        "source_plan_id": arguments.get("source_plan_id"),
        "as_of_time": arguments.get("as_of_time") or flow.get("as_of_time"),
        "options": options,
    }


def _flow_plan_preview_state(preview: dict[str, Any]) -> dict[str, Any]:
    data = preview.get("data") if isinstance(preview.get("data"), dict) else {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    return {
        "status": preview.get("status"),
        "draft": draft,
        "calendar_delta": data.get("calendar_delta") if isinstance(data.get("calendar_delta"), dict) else {},
        "idempotency_key": _milk_plan_preview_idempotency_key(draft),
    }


def _milk_plan_preview_idempotency_key(draft: dict[str, Any]) -> str:
    if not draft:
        return ""
    payload = json.dumps(draft, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"milk-plan-preview-{digest}"


def _flow_analysis_next_question(assessment: dict[str, Any]) -> str:
    data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
    flow_decision = data.get("milk_flow_decision") if isinstance(data.get("milk_flow_decision"), dict) else {}
    plan_decision = flow_decision.get("plan_decision") if isinstance(flow_decision.get("plan_decision"), dict) else {}
    if plan_decision.get("can_start_plan") is True:
        return "这些关键信息已经齐了。你想现在按这个方向生成一版奶量计划吗？"
    return "这些关键信息已经齐了，我先按当前结果给你一个下一步处理建议。"


def _flow_state_for_storage(flow: dict[str, Any]) -> dict[str, Any]:
    state = {
        "stage": flow.get("stage"),
        "goal": flow.get("goal"),
        "current_field": flow.get("current_field"),
        "next_question": flow.get("next_question"),
        "checklist": flow.get("checklist", []),
        "records_snapshot": flow.get("records_snapshot") if isinstance(flow.get("records_snapshot"), dict) else {},
        "infant_signals": flow.get("infant_signals") if isinstance(flow.get("infant_signals"), dict) else {},
        "maternal_symptoms": flow.get("maternal_symptoms") if isinstance(flow.get("maternal_symptoms"), dict) else {},
        "progress": flow.get("progress") if isinstance(flow.get("progress"), dict) else {},
        "workflow_control": flow.get("workflow_control") if isinstance(flow.get("workflow_control"), dict) else {},
        "field_guidance": flow.get("field_guidance") if isinstance(flow.get("field_guidance"), dict) else {},
        "joint_reasoning_guidance": flow.get("joint_reasoning_guidance") if isinstance(flow.get("joint_reasoning_guidance"), list) else [],
        "plan_type": flow.get("plan_type"),
        "target_daily_ml": flow.get("target_daily_ml"),
        "delta_ml": flow.get("delta_ml"),
    }
    assessment = flow.get("assessment_result") if isinstance(flow.get("assessment_result"), dict) else {}
    if assessment:
        state["assessment_result"] = assessment
    plan_preview = flow.get("plan_preview") if isinstance(flow.get("plan_preview"), dict) else {}
    if plan_preview:
        state["plan_preview"] = plan_preview
    return state


def _evaluate_milk_analysis_core(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    arguments = _arguments_with_cached_milk_context(arguments, inputs)
    comprehensive_assessment = _should_run_comprehensive_milk_assessment(arguments, inputs)
    assessment_arguments = _milk_assessment_arguments(arguments, comprehensive_assessment=comprehensive_assessment)
    result = dict(evaluate_milk_status(**assessment_arguments))
    clinical_gate = _clinical_gate_for_analysis(arguments, result=result, comprehensive_assessment=comprehensive_assessment)
    if clinical_gate is not None:
        return clinical_gate
    _attach_clinical_assessment(result, arguments)
    _attach_milk_flow_decision(result)
    return result


def _workflow_infer_context_from_text(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = norm_text(text).lower()
    if not normalized:
        return {}, {}
    infant: dict[str, Any] = {}
    maternal: dict[str, Any] = {}
    negative = any(token in normalized for token in ("没有", "没", "无", "否认", "不发", "不红", "不痛", "not"))
    if any(token in normalized for token in ("红旗", "异常")) and negative:
        maternal.update({"fever": False, "chills": False, "breast_redness": False, "lump_or_hard_area": False, "worsening_pain": False})
    if any(token in normalized for token in ("发热", "发烧", "fever")):
        maternal["fever"] = not negative
    if any(token in normalized for token in ("寒战", "发冷", "chills")):
        maternal["chills"] = not negative
    if any(token in normalized for token in ("红肿", "红热", "发红", "redness")):
        maternal["breast_redness"] = not negative
    if any(token in normalized for token in ("硬块", "肿块", "结块", "lump")):
        maternal["lump_or_hard_area"] = not negative
    if any(token in normalized for token in ("疼痛加重", "更痛", "越来越痛", "worsening")):
        maternal["worsening_pain"] = not negative
    if any(token in normalized for token in ("胀", "涨", "排不空", "吸完还")):
        maternal["breast_fullness"] = True
        maternal["incomplete_emptying"] = True
    if any(token in normalized for token in ("尿布正常", "尿量正常", "尿不少", "小便正常")):
        infant["wet_diapers_24h"] = "normal"
    if any(token in normalized for token in ("精神正常", "精神好", "精神可以", "状态正常")):
        infant["baby_state"] = "normal"
    if any(token in normalized for token in ("吃完满足", "吃完还好", "吃奶正常")):
        infant["feeding_satisfaction"] = "normal"
    if any(token in normalized for token in ("体重正常", "增长正常", "体重增长")):
        infant["weight_trend"] = "normal"
    return infant, maternal


def _workflow_plan_numbers_from_text(text: str) -> tuple[float | None, float | None]:
    normalized = norm_text(text)
    if not normalized:
        return None, None

    target_daily_ml: float | None = None
    delta_ml: float | None = None
    target_match = re.search(r"(?:目标|做到|达到|到)\D{0,8}(\d+(?:\.\d+)?)\s*(?:ml|毫升)?", normalized, flags=re.IGNORECASE)
    if target_match:
        target_daily_ml = _workflow_float(target_match.group(1))

    delta_match = re.search(r"(?:每天|一天)?(?:多|增加|加|少|减少|减)\s*(\d+(?:\.\d+)?)\s*(?:ml|毫升)?", normalized, flags=re.IGNORECASE)
    if delta_match:
        delta_ml = _workflow_float(delta_match.group(1))
    return target_daily_ml, delta_ml


def _workflow_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _with_milk_plan_card(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    if not result.get("ok") or not draft or str(result.get("status") or "").strip() != "plan_preview_ready":
        return result
    result["card"] = _milk_plan_card(draft, card_status=str(data.get("card_status") or "preview"), data=data)
    result["assistant_followup"] = {"message": _milk_plan_preview_followup_message(data)}
    return result


def _milk_plan_card(draft: dict[str, Any], *, card_status: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    card_data = dict(data or {})
    card_data["draft"] = draft
    card_data["card_status"] = card_status
    return {
        "id": _card_id("milk-plan", {"start_at": draft.get("plan_type"), "end_at": draft.get("plan_days")}),
        "card_type": "milk_plan_card",
        "schema_version": "1.0",
        "card_json": _build_milk_plan_card_json(card_data),
    }


def _should_run_comprehensive_milk_assessment(arguments: dict[str, Any], inputs: RuntimeInputs) -> bool:
    explicit = arguments.get("comprehensive_assessment")
    if isinstance(explicit, bool):
        return explicit
    if isinstance(explicit, str):
        normalized = explicit.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False

    workflow_intent = str(arguments.get("workflow_intent") or arguments.get("intent") or "").strip().lower()
    if workflow_intent in {"milk_analysis", "comprehensive_milk_assessment", "analysis"}:
        return True

    user_message = str(inputs.get("user_message") or "").strip().lower()
    if not user_message:
        return False

    plan_tokens = (
        "生成计划",
        "做个计划",
        "做一个计划",
        "制定计划",
        "追奶计划",
        "稳奶计划",
        "减奶计划",
        "温和追奶",
        "帮我追奶",
        "帮我稳奶",
        "帮我减奶",
        "同步到日历",
        "写入日历",
        "保存计划",
        "计划草稿",
        "milk plan",
    )
    if any(token in user_message for token in plan_tokens):
        return False

    analysis_tokens = (
        "分析",
        "看看",
        "看一下",
        "最近吸奶",
        "吸奶情况",
        "奶量情况",
        "最近情况",
        "趋势",
        "够不够",
        "是否正常",
        "正常吗",
        "偏低",
        "偏高",
        "低于",
        "高于",
        "怎么样",
        "analysis",
        "trend",
    )
    return any(token in user_message for token in analysis_tokens)


def _create_milk_plan_preview_core(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    plan_type = arguments.get("plan_type")
    target_daily_ml = arguments.get("target_daily_ml")
    if target_daily_ml is None:
        target_daily_ml = arguments.get("custom_target_daily_ml")
    delta_ml = arguments.get("delta_ml")
    source_plan_id = arguments.get("source_plan_id") or arguments.get("plan_id")
    options = _options_with_cached_milk_assessment(
        _normalized_options(arguments.get("options")),
        inputs,
    )

    target_validation: dict[str, Any] | None = None
    if (target_daily_ml is not None or delta_ml is not None) and plan_type is not None:
        target_validation = dict(
            validate_milk_plan_target(
                user_id=arguments["user_id"],
                plan_type=plan_type,
                target_daily_ml=target_daily_ml,
                delta_ml=delta_ml,
                as_of_time=arguments.get("as_of_time"),
            )
        )
        validation_data = target_validation.get("data") if isinstance(target_validation.get("data"), dict) else {}
        if validation_data.get("valid") is False:
            return {
                "ok": False,
                "status": "milk_plan_target_invalid",
                "summary": target_validation.get("summary", "计划目标不符合当前边界。"),
                "data": {"target_validation": validation_data},
                "assistant_followup": {
                    "message": "这个目标现在有点偏急了。\n\n我可以先帮你把目标调得温和一点，再生成计划，这样身体会更好跟上。"
                },
            }
        if target_daily_ml is None and validation_data.get("target_daily_ml") is not None:
            target_daily_ml = validation_data.get("target_daily_ml")

    if source_plan_id is not None:
        clinical_gate = _clinical_gate_for_plan(arguments, plan_type=plan_type, options=options)
        if clinical_gate is not None:
            return clinical_gate
        preview = dict(
            regenerate_milk_plan_preview(
                user_id=arguments["user_id"],
                plan_id=source_plan_id,
                plan_type=plan_type,
                plan_days=arguments.get("plan_days"),
                custom_target_daily_ml=target_daily_ml,
                as_of_time=arguments.get("as_of_time"),
                options=options,
            )
        )
    else:
        if plan_type is None:
            return {
                "ok": False,
                "status": "milk_plan_preview_missing_plan_type",
                "summary": "缺少 plan_type，无法生成新的奶量计划草稿。",
                "data": {
                    "missing_fields": ["plan_type"],
                    "milk_flow_decision": _milk_flow_decision_for_missing_plan_type(),
                },
                "assistant_followup": {"message": "我先确认一下方向，这样不会帮你排偏：你现在更想追奶、稳奶，还是减奶？"},
            }
        clinical_gate = _clinical_gate_for_plan(arguments, plan_type=plan_type, options=options)
        if clinical_gate is not None:
            return clinical_gate
        preview = dict(
            preview_milk_plan(
                user_id=arguments["user_id"],
                plan_type=plan_type,
                plan_days=arguments.get("plan_days"),
                custom_target_daily_ml=target_daily_ml,
                as_of_time=arguments.get("as_of_time"),
                options=options,
            )
        )

    if target_validation:
        data = preview.setdefault("data", {})
        if isinstance(data, dict):
            data["target_validation"] = target_validation.get("data", {})
    _attach_milk_plan_flow_decision(preview)
    return preview


def _clinical_gate_for_plan(arguments: dict[str, Any], *, plan_type: Any, options: dict[str, Any]) -> dict[str, Any] | None:
    intake_complete = bool(options.get("_analysis_intake_complete"))
    prepared_clinical = options.get("prepared_clinical_assessment")
    if isinstance(prepared_clinical, dict) and prepared_clinical:
        clinical_data = dict(prepared_clinical)
        if intake_complete and _missing_clinical_context_fields(clinical_data):
            clinical_data = _clinical_data_from_plan_options(arguments, plan_type=plan_type, options=options)
    else:
        clinical_data = _clinical_data_from_plan_options(arguments, plan_type=plan_type, options=options)
    plan_gate = clinical_data.get("plan_gate") if isinstance(clinical_data.get("plan_gate"), dict) else {}
    if plan_gate.get("allowed") is False:
        decision = _milk_flow_decision_for_plan_blocked(clinical_data, str(plan_type or ""))
        return {
            "ok": False,
            "status": "milk_plan_clinical_gate_blocked",
            "summary": str(plan_gate.get("reason") or "当前不适合直接生成奶量计划。"),
            "data": {
                "clinical_assessment": _compact_clinical_data(clinical_data),
                "requires_confirmation": False,
                "milk_flow_decision": decision,
            },
            "assistant_followup": {"message": _milk_plan_gate_followup_message(clinical_data)},
        }
    missing_fields = _missing_clinical_context_fields(clinical_data)
    if missing_fields and not intake_complete:
        current_field, next_question = _clinical_context_current_question(missing_fields)
        return {
            "ok": False,
            "status": "milk_plan_needs_clinical_context",
            "summary": "奶量计划流程正在等待补齐宝宝状态和妈妈乳房情况。",
            "data": {
                "clinical_assessment": _compact_clinical_data(clinical_data),
                "missing_fields": missing_fields,
                "suggested_questions": [next_question] if next_question else [],
                "current_field": current_field,
                "next_question": next_question,
                "remaining_count": len(missing_fields),
                "workflow_intent": "milk_plan_preview_create",
                "milk_flow_decision": _milk_flow_decision_for_missing_context(
                    missing_fields,
                    stage="need_more_user_context",
                    next_tool="milk_analysis_intake_manage",
                ),
                "continuation_instruction": (
                    "当前仍处于奶量计划生成流程。"
                    "继续向用户确认 current_field 对应的信息；"
                    "收集齐宝宝状态和妈妈乳房/全身状态后，再调用 milk_plan_preview_create 生成计划。"
                    "不要一次追问多个缺失项，不要说最后一个或只差一个，也不要直接生成计划。"
                ),
                "requires_confirmation": False,
            },
            "assistant_followup": {"message": _clinical_context_followup_message(missing_fields)},
        }
    options.setdefault("prepared_assessment", clinical_data.get("milk_assessment"))
    options.setdefault("prepared_growth_assessment", clinical_data.get("growth_assessment"))
    return None


def _clinical_data_from_plan_options(arguments: dict[str, Any], *, plan_type: Any, options: dict[str, Any]) -> dict[str, Any]:
    prepared_assessment = options.get("prepared_assessment") if isinstance(options.get("prepared_assessment"), dict) else None
    prepared_from_context = bool(options.get("_prepared_assessment_from_context"))
    prepared_window_days = _to_int(options.get("_prepared_assessment_window_days"), _milk_assessment_window_days(prepared_assessment))
    needs_plan_window = prepared_from_context and prepared_window_days < 7
    plan_window_days = max(_to_int(arguments.get("window_days"), 7), 7)
    clinical = _evaluate_milk_context_status(
        user_id=arguments["user_id"],
        as_of_time=arguments.get("as_of_time"),
        window_days=7 if needs_plan_window else plan_window_days,
        include_today=False,
        milk_assessment=None if needs_plan_window else prepared_assessment,
        growth_assessment=options.get("prepared_growth_assessment") if isinstance(options.get("prepared_growth_assessment"), dict) else None,
        maternal_symptoms=options.get("maternal_symptoms") if isinstance(options.get("maternal_symptoms"), dict) else {},
        infant_signals=options.get("infant_signals") if isinstance(options.get("infant_signals"), dict) else {},
        requested_plan_type=str(plan_type or ""),
    )
    return clinical.get("data") if isinstance(clinical.get("data"), dict) else {}


def _clinical_gate_for_analysis(arguments: dict[str, Any], *, result: dict[str, Any], comprehensive_assessment: bool) -> dict[str, Any] | None:
    if not comprehensive_assessment:
        return None
    clinical = _clinical_assessment_for_result(arguments, result=result, requested_plan_type="")
    clinical_data = clinical.get("data") if isinstance(clinical.get("data"), dict) else {}
    missing_fields = _missing_clinical_context_fields(clinical_data)
    if missing_fields:
        current_field, next_question = _clinical_context_current_question(missing_fields)
        decision = _milk_flow_decision_for_missing_context(
            missing_fields,
            stage="need_more_user_context",
            next_tool="milk_analysis_intake_manage",
        )
        return {
            "ok": True,
            "status": "needs_clinical_context",
            "summary": "还需要先确认宝宝近 24 小时状态和妈妈乳房情况，再继续分析吸奶和奶量。",
            "data": {
                "clinical_assessment": _compact_clinical_data(clinical_data),
                "workflow_intent": "milk_analysis",
                "milk_flow_decision": decision,
                "continuation_instruction": (
                    "当前仍处于奶量/吸奶分析流程。下一轮用户回复通常是在补充这些判断信息，"
                    "不要因为出现“疼、红肿、硬块、发热”等词就切换成独立健康咨询或直接调用 web_search。"
                    "如果没有明显红旗信号，应把用户回答提炼进 infant_signals / maternal_symptoms，"
                    "然后继续调用 milk_analysis_intake_manage，由采集状态机决定下一步。"
                    "如果仍有 missing_fields，必须只追问 current_field 对应的一个问题；"
                    "不要一次追问多个缺失项，不要说最后一个或只差一个，不要直接输出完整分析或制定计划。"
                ),
                "missing_fields": missing_fields,
                "suggested_questions": [next_question] if next_question else [],
                "current_field": current_field,
                "next_question": next_question,
                "remaining_count": len(missing_fields),
            },
            "assistant_followup": {"message": _clinical_context_followup_message(missing_fields)},
        }
    risk_level = str(clinical_data.get("risk_level") or "").strip()
    if risk_level in {"medical_recommended", "urgent"}:
        decision = _milk_flow_decision_for_plan_blocked(clinical_data, "")
        return {
            "ok": True,
            "status": "analysis_medical_gate_blocked",
            "summary": "当前有需要优先医学评估的信号，先不要只看奶量数据下结论。",
            "data": {"clinical_assessment": _compact_clinical_data(clinical_data), "milk_flow_decision": decision},
            "assistant_followup": {"message": "这次我们先不只看奶量数字。\n\n你描述的情况更适合先联系医生或线下医疗渠道确认；等身体这边稳住了，我再陪你继续看奶量和计划。"},
        }
    if risk_level == "ibclc_recommended":
        decision = _milk_flow_decision_for_plan_blocked(clinical_data, "")
        return {
            "ok": True,
            "status": "analysis_ibclc_gate_blocked",
            "summary": "当前更适合先结合 IBCLC 看含乳、吸奶或亲喂效果，以及乳房不适。",
            "data": {"clinical_assessment": _compact_clinical_data(clinical_data), "milk_flow_decision": decision},
            "assistant_followup": {"message": "这次不只是看奶量数字就能说清楚。\n\n更适合把含乳、排乳和乳房不适一起看一遍；如果你愿意，我可以帮你打开 IBCLC 咨询入口，让顾问一起看。"},
        }
    if str(clinical_data.get("data_confidence") or "").strip() == "low":
        decision = _milk_flow_decision_for_more_records()
        return {
            "ok": True,
            "status": "needs_more_records_for_analysis",
            "summary": "当前关键记录不足，先补充记录后再做奶量分析。",
            "data": {"clinical_assessment": _compact_clinical_data(clinical_data), "milk_flow_decision": decision},
            "assistant_followup": {"message": "现在可计算奶量的记录还不够完整，先不用急着下结论。\n\n你可以先补一下过去几天有毫升数的吸奶、瓶喂母乳或补奶记录；如果只有亲喂，我会结合已有瓶喂参考估算，不需要你重复补充频次。补完后我再帮你重新分析，会更接近真实情况。"},
        }
    return None


def _attach_clinical_assessment(result: dict[str, Any], arguments: dict[str, Any]) -> None:
    clinical = _clinical_assessment_for_result(arguments, result=result, requested_plan_type="")
    clinical_data = clinical.get("data") if isinstance(clinical.get("data"), dict) else {}
    data = result.get("data")
    if isinstance(data, dict):
        data["clinical_assessment"] = _compact_clinical_data(clinical_data)


def _clinical_assessment_for_result(arguments: dict[str, Any], *, result: dict[str, Any], requested_plan_type: str) -> dict[str, Any]:
    return _evaluate_milk_context_status(
        user_id=arguments["user_id"],
        as_of_time=arguments.get("as_of_time"),
        window_days=_to_int(arguments.get("window_days"), 7),
        include_today=bool(arguments.get("include_today")),
        milk_assessment=result,
        maternal_symptoms=arguments.get("maternal_symptoms") if isinstance(arguments.get("maternal_symptoms"), dict) else {},
        infant_signals=arguments.get("infant_signals") if isinstance(arguments.get("infant_signals"), dict) else {},
        requested_plan_type=requested_plan_type,
    )


def _evaluate_milk_context_status(
    *,
    user_id: str,
    as_of_time: Any = None,
    window_days: int = 7,
    include_today: bool = False,
    milk_assessment: dict[str, Any] | None = None,
    growth_assessment: dict[str, Any] | None = None,
    maternal_symptoms: dict[str, Any] | None = None,
    infant_signals: dict[str, Any] | None = None,
    requested_plan_type: str | None = None,
) -> dict[str, Any]:
    milk_result = _ensure_milk_assessment_data(
        user_id=user_id,
        as_of_time=as_of_time,
        window_days=window_days,
        include_today=include_today,
        milk_assessment=milk_assessment,
    )
    milk_data = milk_result.get("data") if isinstance(milk_result.get("data"), dict) else {}
    infant = infant_signals or {}
    maternal = maternal_symptoms or {}
    growth_data = _ensure_growth_status_data(
        user_id=user_id,
        as_of_time=as_of_time,
        growth_assessment=growth_assessment,
        infant_signals=infant,
    )

    domains = {
        "record_completeness": _record_completeness_domain(milk_data, infant, maternal),
        "milk_volume": _milk_volume_domain(milk_data),
        "infant_intake": _infant_intake_domain(infant),
        "infant_growth": _infant_growth_domain(growth_data),
        "maternal_breast_symptoms": _maternal_symptoms_domain(maternal),
    }
    risk_level, risk_reasons = _resolve_milk_context_risk(domains)
    plan_gate = _plan_gate_for_milk_context(
        risk_level=risk_level,
        domains=domains,
        requested_plan_type=requested_plan_type,
    )
    data = {
        "risk_level": risk_level,
        "data_confidence": domains["record_completeness"]["data_confidence"],
        "domains": domains,
        "risk_reasons": risk_reasons,
        "plan_gate": plan_gate,
        "next_actions": _next_actions_for_milk_context(risk_level, domains, plan_gate),
        "evidence": [_evidence_item(evidence_id) for evidence_id in _evidence_ids_for_milk_context(domains, risk_level)],
        "milk_assessment": milk_data,
        "growth_assessment": growth_data,
    }
    return {
        "ok": True,
        "status": "milk_context_status_ready",
        "summary": _summary_for_milk_context(risk_level, plan_gate),
        "data": data,
    }


def _ensure_milk_assessment_data(
    *,
    user_id: str,
    as_of_time: Any,
    window_days: int,
    include_today: bool,
    milk_assessment: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(milk_assessment, dict) and milk_assessment:
        if "data" in milk_assessment and isinstance(milk_assessment.get("data"), dict):
            return dict(milk_assessment)
        return {"ok": True, "status": "prepared_milk_assessment", "data": dict(milk_assessment)}
    return dict(
        evaluate_milk_status(
            user_id=user_id,
            as_of_time=as_of_time,
            window_days=max(to_int(window_days, 7), 1),
            include_today=include_today,
        )
    )


def _ensure_growth_status_data(
    *,
    user_id: str,
    as_of_time: Any,
    growth_assessment: dict[str, Any] | None,
    infant_signals: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(growth_assessment, dict) and growth_assessment:
        return dict(growth_assessment.get("data") if isinstance(growth_assessment.get("data"), dict) else growth_assessment)
    if _has_growth_related_signal(infant_signals):
        result = evaluate_infant_growth(user_id=user_id, infant_id=None, as_of_time=as_of_time)
        return dict(result.get("data") if isinstance(result.get("data"), dict) else {})
    return {}


def _record_completeness_domain(
    milk_data: dict[str, Any],
    infant_signals: dict[str, Any],
    maternal_symptoms: dict[str, Any],
) -> dict[str, Any]:
    missing = milk_data.get("missing_data") if isinstance(milk_data.get("missing_data"), list) else []
    normality = milk_data.get("milk_normality") if isinstance(milk_data.get("milk_normality"), dict) else {}
    stats = normality.get("stats") if isinstance(normality.get("stats"), dict) else {}
    valid_days = to_int(stats.get("valid_days"), 0)
    window = milk_data.get("window") if isinstance(milk_data.get("window"), dict) else {}
    window_days = max(to_int(window.get("window_days"), 1), 1)

    signal_count = sum(
        1
        for value in (
            infant_signals.get("wet_diapers_24h"),
            infant_signals.get("stool_24h"),
            infant_signals.get("baby_state"),
            infant_signals.get("recent_weight"),
            maternal_symptoms.get("pain_level"),
        )
        if norm_text(value)
    )
    if valid_days <= 0:
        confidence = "low"
    elif valid_days < min(window_days, 3) or missing:
        confidence = "medium"
    else:
        confidence = "high"

    return {
        "status": "complete" if confidence == "high" else "partial",
        "data_confidence": confidence,
        "valid_days": valid_days,
        "window_days": window_days,
        "missing_data": missing,
        "extra_signal_count": signal_count,
    }


def _milk_volume_domain(milk_data: dict[str, Any]) -> dict[str, Any]:
    normality = milk_data.get("milk_normality") if isinstance(milk_data.get("milk_normality"), dict) else {}
    status = norm_text(normality.get("overall_status")) or "unknown"
    days = normality.get("days") if isinstance(normality.get("days"), list) else []
    valid = [item for item in days if isinstance(item, dict) and item.get("ok") is True]
    low_days = [item for item in valid if norm_text(item.get("status")) == "low"]
    high_days = [item for item in valid if norm_text(item.get("status")) == "high"]
    latest = sorted(valid, key=lambda item: norm_text(item.get("date")))[-1] if valid else {}
    return {
        "status": status,
        "latest_status": norm_text(latest.get("status")) or "unknown",
        "valid_days": len(valid),
        "low_days": len(low_days),
        "high_days": len(high_days),
        "estimated_daily_milk_ml": latest.get("estimated_daily_milk_ml"),
        "estimated_breastfeeding_ml": latest.get("estimated_breastfeeding_ml"),
        "rule_hit": latest.get("rule_hit"),
    }


def _infant_intake_domain(infant_signals: dict[str, Any]) -> dict[str, Any]:
    wet_diapers_raw = norm_text(infant_signals.get("wet_diapers_24h"))
    wet_diapers = _optional_int(infant_signals.get("wet_diapers_24h"))
    wet_diapers_provided = bool(wet_diapers_raw)
    baby_state = norm_text(infant_signals.get("baby_state")).lower()
    feeding_satisfaction = norm_text(infant_signals.get("feeding_satisfaction")).lower()
    poor_intake = to_bool(infant_signals.get("poor_feeding")) or to_bool(infant_signals.get("poor_latch"))
    lethargic = to_bool(infant_signals.get("lethargy")) or any(token in baby_state for token in ("嗜睡", "精神差", "无力", "letharg"))
    unsettled_after_feeding = any(token in feeding_satisfaction for token in ("不安稳", "很快", "哭", "找奶", "不满足", "fussy"))
    fewer_wet_diapers = (wet_diapers is not None and wet_diapers < 4) or _wet_diaper_text_suggests_low(wet_diapers_raw)
    if lethargic or fewer_wet_diapers or poor_intake or unsettled_after_feeding:
        status = "concern"
    elif not wet_diapers_provided and not baby_state and not feeding_satisfaction:
        status = "unknown"
    else:
        status = "reassuring"
    return {
        "status": status,
        "provided_fields": sorted(str(key) for key in infant_signals.keys()),
        "wet_diapers_24h": wet_diapers,
        "wet_diapers_text": wet_diapers_raw if wet_diapers is None else "",
        "wet_diapers_provided": wet_diapers_provided,
        "baby_state": infant_signals.get("baby_state"),
        "feeding_satisfaction": infant_signals.get("feeding_satisfaction"),
        "poor_feeding": poor_intake,
        "lethargy": lethargic,
    }


def _wet_diaper_text_suggests_low(text: str) -> bool:
    if not text:
        return False
    reassuring_tokens = ("正常", "差不多", "和平时", "没少", "没有少", "不少", "够", "还好", "可以")
    if any(token in text for token in reassuring_tokens):
        return False
    return any(token in text for token in ("尿少", "变少", "少了", "偏少", "很少", "明显少"))


def _infant_growth_domain(growth_data: dict[str, Any]) -> dict[str, Any]:
    status = norm_text(growth_data.get("status") or growth_data.get("overall_status"))
    if not status:
        status = "unknown"
    normalized = "concern" if status in {"abnormal", "drop", "significant_drop", "low", "high"} else status
    return {
        "status": normalized,
        "raw_status": status,
        "summary": growth_data.get("summary"),
    }


def _maternal_symptoms_domain(maternal_symptoms: dict[str, Any]) -> dict[str, Any]:
    fever = to_bool(maternal_symptoms.get("fever"))
    chills = to_bool(maternal_symptoms.get("chills"))
    redness = to_bool(maternal_symptoms.get("breast_redness"))
    lump = to_bool(maternal_symptoms.get("lump_or_hard_area"))
    worsening_pain = to_bool(maternal_symptoms.get("worsening_pain"))
    nipple_damage = to_bool(maternal_symptoms.get("nipple_damage"))
    recurrent_plug = to_bool(maternal_symptoms.get("recurrent_plug"))
    breast_fullness = _has_breast_fullness_signal(maternal_symptoms)
    pain_level = _optional_int(maternal_symptoms.get("pain_level"))

    if fever and (chills or redness or lump or worsening_pain):
        status = "medical_concern"
    elif redness or lump or recurrent_plug or nipple_damage or (pain_level is not None and pain_level >= 6):
        status = "ibclc_concern"
    elif maternal_symptoms:
        status = "reassuring"
    else:
        status = "unknown"
    return {
        "status": status,
        "provided_fields": sorted(str(key) for key in maternal_symptoms.keys()),
        "fever": fever,
        "chills": chills,
        "breast_redness": redness,
        "lump_or_hard_area": lump,
        "worsening_pain": worsening_pain,
        "nipple_damage": nipple_damage,
        "recurrent_plug": recurrent_plug,
        "breast_fullness": breast_fullness,
        "fullness_without_red_flags": breast_fullness and not (fever or chills or redness or lump or worsening_pain),
        "pain_level": pain_level,
    }


def _resolve_milk_context_risk(domains: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    maternal = domains["maternal_breast_symptoms"]
    infant_intake = domains["infant_intake"]
    growth = domains["infant_growth"]
    milk = domains["milk_volume"]
    completeness = domains["record_completeness"]

    if maternal.get("status") == "medical_concern":
        reasons.append("妈妈有发热/寒战合并乳房红肿、硬块或疼痛加重信号。")
        return RISK_MEDICAL_RECOMMENDED, reasons
    if infant_intake.get("lethargy") is True:
        reasons.append("宝宝精神状态提示需要优先专业评估。")
        return RISK_MEDICAL_RECOMMENDED, reasons
    if infant_intake.get("status") == "concern" and growth.get("status") == "concern":
        reasons.append("宝宝摄入和生长信号都需要关注。")
        return RISK_MEDICAL_RECOMMENDED, reasons
    if maternal.get("status") == "ibclc_concern":
        reasons.append("妈妈有乳房不适、乳头损伤或反复堵奶信号。")
        return RISK_IBCLC_RECOMMENDED, reasons
    if infant_intake.get("status") == "concern":
        reasons.append("宝宝摄入信号不够安心。")
        return RISK_IBCLC_RECOMMENDED, reasons
    if growth.get("status") == "concern":
        reasons.append("宝宝生长信号需要结合专业评估。")
        return RISK_IBCLC_RECOMMENDED, reasons
    if completeness.get("data_confidence") == "low":
        reasons.append("关键记录不足，暂时不适合下确定判断。")
        return RISK_WATCH, reasons
    if milk.get("status") in {"under_supply_alert", "over_supply_alert"} or milk.get("latest_status") in {"low", "high"}:
        reasons.append("奶量数据出现连续或关键阶段偏离参考的信号。")
        return RISK_WATCH, reasons
    return RISK_LOW, reasons


def _plan_gate_for_milk_context(*, risk_level: str, domains: dict[str, dict[str, Any]], requested_plan_type: str | None) -> dict[str, Any]:
    requested = norm_text(requested_plan_type)
    milk_status = norm_text(domains["milk_volume"].get("status"))
    maternal_status = norm_text(domains["maternal_breast_symptoms"].get("status"))
    infant_status = norm_text(domains["infant_intake"].get("status"))
    growth_status = norm_text(domains["infant_growth"].get("status"))
    confidence = norm_text(domains["record_completeness"].get("data_confidence"))

    if risk_level in {RISK_MEDICAL_RECOMMENDED, RISK_URGENT}:
        return {
            "allowed": False,
            "allowed_plan_types": [],
            "blocked_plan_types": ALL_PLAN_TYPES,
            "reason": "当前有需要优先医学评估的信号，先不要进入普通奶量计划。",
        }
    if risk_level == RISK_IBCLC_RECOMMENDED:
        allowed = [PLAN_TYPE_MAINTAIN]
        return {
            "allowed": requested in {"", PLAN_TYPE_MAINTAIN},
            "allowed_plan_types": allowed,
            "blocked_plan_types": [item for item in ALL_PLAN_TYPES if item not in allowed],
            "reason": "当前更适合先看含乳、吸奶或亲喂效果，以及乳房不适，再决定是否追奶/减奶。",
        }
    if confidence == "low":
        return {
            "allowed": False,
            "allowed_plan_types": [],
            "blocked_plan_types": ALL_PLAN_TYPES,
            "reason": "关键记录不足，先补充记录后再生成计划。",
        }
    if requested == PLAN_TYPE_DECREASE and (maternal_status in {"ibclc_concern", "medical_concern"} or infant_status == "concern"):
        return {
            "allowed": False,
            "allowed_plan_types": [PLAN_TYPE_MAINTAIN],
            "blocked_plan_types": [PLAN_TYPE_INCREASE, PLAN_TYPE_DECREASE],
            "reason": "有乳房不适或宝宝摄入信号时，不适合直接减奶。",
        }
    if requested == PLAN_TYPE_DECREASE and milk_status != "over_supply_alert" and growth_status == "concern":
        return {
            "allowed": False,
            "allowed_plan_types": [PLAN_TYPE_MAINTAIN],
            "blocked_plan_types": [PLAN_TYPE_INCREASE, PLAN_TYPE_DECREASE],
            "reason": "宝宝生长信号不稳时，不适合直接减奶。",
        }
    return {
        "allowed": True,
        "allowed_plan_types": ALL_PLAN_TYPES,
        "blocked_plan_types": [],
        "reason": "当前没有阻断普通奶量计划的高风险信号。",
    }


def _next_actions_for_milk_context(risk_level: str, domains: dict[str, dict[str, Any]], plan_gate: dict[str, Any]) -> list[str]:
    if risk_level == RISK_MEDICAL_RECOMMENDED:
        return ["优先联系医生/医院或 IBCLC。", "暂停普通追奶、稳奶或减奶计划判断。"]
    if risk_level == RISK_IBCLC_RECOMMENDED:
        return ["建议让 IBCLC 一起看含乳、吸奶或亲喂效果和乳房不适。", "先记录 24 小时尿布、喂养和乳房舒适度。"]
    if not plan_gate.get("allowed"):
        return ["先补充关键记录。", "记录完整后再判断是否生成奶量计划。"]
    if domains["milk_volume"].get("status") == "under_supply_alert":
        maternal = domains["maternal_breast_symptoms"]
        if maternal.get("fullness_without_red_flags") is True:
            return ["如果用户明确需要计划，可以继续生成温和追奶计划，并把胀/排不空作为计划约束。", "连续记录 3 天后复盘宝宝信号和妈妈舒适度。"]
        return ["如果用户明确需要计划，可以继续生成温和追奶计划。", "连续记录 3 天后复盘宝宝信号和妈妈舒适度。"]
    if domains["milk_volume"].get("status") == "over_supply_alert":
        return ["先观察 3-5 天。", "如果持续胀痛、堵奶或喷乳明显，再考虑温和减奶。"]
    return ["可以继续按当前节奏观察。", "需要时可生成稳奶计划。"]


def _evidence_ids_for_milk_context(domains: dict[str, dict[str, Any]], risk_level: str) -> list[str]:
    ids = ["CDC_BREASTFEEDING_FREQUENCY"]
    if domains["maternal_breast_symptoms"].get("status") in {"medical_concern", "ibclc_concern"}:
        ids.append("ABM_MASTITIS_PROTOCOL")
    if domains["milk_volume"].get("status") == "over_supply_alert":
        ids.append("ABM_HYPERLACTATION_PROTOCOL")
    if domains["infant_growth"].get("status") == "concern":
        ids.append("WHO_GROWTH_STANDARDS")
    if risk_level in {RISK_LOW, RISK_WATCH}:
        ids.append("CDC_PUMPING_BREAST_MILK")
    return list(dict.fromkeys(ids))


def _evidence_item(evidence_id: str) -> dict[str, str]:
    source = EVIDENCE_SOURCES.get(evidence_id, {})
    return {
        "id": evidence_id,
        "title": source.get("title", evidence_id),
        "source": source.get("source", ""),
    }


def _summary_for_milk_context(risk_level: str, plan_gate: dict[str, Any]) -> str:
    if risk_level == RISK_MEDICAL_RECOMMENDED:
        return "当前有需要优先医学评估的信号。"
    if risk_level == RISK_IBCLC_RECOMMENDED:
        return "当前建议优先结合 IBCLC 评估。"
    if not plan_gate.get("allowed"):
        return "当前数据还不足以生成奶量计划。"
    if risk_level == RISK_WATCH:
        return "当前适合谨慎观察，并可按规则生成温和计划。"
    return "当前未发现阻断普通奶量计划的高风险信号。"


def _has_growth_related_signal(infant_signals: dict[str, Any]) -> bool:
    return any(
        norm_text(infant_signals.get(key))
        for key in ("recent_weight", "weight_trend", "growth_concern", "baby_state", "wet_diapers_24h")
    )


def _has_breast_fullness_signal(maternal_symptoms: dict[str, Any]) -> bool:
    for key in ("breast_fullness", "engorgement", "post_pump_fullness", "incomplete_emptying"):
        if to_bool(maternal_symptoms.get(key)):
            return True
    text = " ".join(
        norm_text(maternal_symptoms.get(key))
        for key in ("symptom_text", "description", "notes")
        if norm_text(maternal_symptoms.get(key))
    )
    return any(token in text for token in ("胀", "涨", "排不空", "没排空", "没有排空", "吸完还胀", "吸完还涨"))


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _missing_clinical_context_fields(clinical_data: dict[str, Any]) -> list[str]:
    domains = clinical_data.get("domains") if isinstance(clinical_data.get("domains"), dict) else {}
    infant = domains.get("infant_intake") if isinstance(domains.get("infant_intake"), dict) else {}
    growth = domains.get("infant_growth") if isinstance(domains.get("infant_growth"), dict) else {}
    maternal = domains.get("maternal_breast_symptoms") if isinstance(domains.get("maternal_breast_symptoms"), dict) else {}
    infant_fields = {str(item) for item in infant.get("provided_fields", [])} if isinstance(infant.get("provided_fields"), list) else set()
    maternal_fields = {str(item) for item in maternal.get("provided_fields", [])} if isinstance(maternal.get("provided_fields"), list) else set()
    missing: list[str] = []
    red_flag_fields = set(FLOW_MATERNAL_RED_FLAG_FIELDS)
    red_flag_positive = any(to_bool(maternal.get(key)) for key in red_flag_fields)
    if infant.get("wet_diapers_provided") is not True:
        missing.append("infant_wet_diapers")
    if not str(infant.get("baby_state") or "").strip() and not str(infant.get("feeding_satisfaction") or "").strip():
        missing.append("infant_state_or_feeding_satisfaction")
    if str(growth.get("status") or "").strip() == "unknown" and not ({"recent_weight", "weight_trend", "growth_concern"} & infant_fields):
        missing.append("infant_growth_signal")
    if not red_flag_fields.issubset(maternal_fields) and not red_flag_positive:
        missing.append("maternal_red_flags")
    comfort_fields = {"breast_fullness", "engorgement", "post_pump_fullness", "incomplete_emptying", "pain_level", "symptom_text"}
    if not (comfort_fields & maternal_fields) and not red_flag_positive:
        missing.append("maternal_breast_comfort")
    return missing


def _clinical_context_questions(missing_fields: list[str]) -> list[str]:
    questions: list[str] = []
    if "infant_wet_diapers" in missing_fields:
        questions.append("宝宝近 24 小时尿量/尿布大概正常吗？")
    if "infant_state_or_feeding_satisfaction" in missing_fields:
        questions.append("宝宝精神状态怎么样，吃奶后通常能安稳一会儿吗？")
    if "infant_growth_signal" in missing_fields:
        questions.append("宝宝最近体重增长看起来还正常吗？")
    if "maternal_red_flags" in missing_fields:
        questions.append("你有没有发热、寒战、乳房明显红肿、硬块，或疼痛越来越重？")
    if "maternal_breast_comfort" in missing_fields:
        questions.append("吸奶或亲喂后乳房是比较舒服，还是还会胀、排不空或疼？")
    return questions


def _clinical_context_current_question(missing_fields: list[str]) -> tuple[str | None, str]:
    current_field = next((str(item).strip() for item in missing_fields if str(item).strip()), "")
    if not current_field:
        return None, ""
    questions = _clinical_context_questions([current_field])
    return current_field, questions[0] if questions else ""


def _clinical_context_followup_message(missing_fields: list[str]) -> str:
    _, question = _clinical_context_current_question(missing_fields)
    if not question:
        return "我已经拿到关键情况了，可以继续看奶量。"
    return "我先确认一项会影响判断的信息。\n\n" + question


def _compact_clinical_data(clinical_data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: clinical_data[key]
        for key in ("risk_level", "data_confidence", "domains", "risk_reasons", "plan_gate", "next_actions", "evidence")
        if key in clinical_data
    }


def _options_with_cached_milk_assessment(options: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    state = inputs.get("_milk_management_state")
    if not isinstance(state, dict):
        return options
    intake = state.get("analysis_intake") if isinstance(state.get("analysis_intake"), dict) else {}
    intake_assessment = intake.get("assessment_result") if isinstance(intake.get("assessment_result"), dict) else {}
    if intake_assessment:
        intake_data = intake_assessment.get("data") if isinstance(intake_assessment.get("data"), dict) else {}
        if intake_data and not isinstance(options.get("prepared_assessment"), dict):
            options["prepared_assessment"] = intake_data
            options["_prepared_assessment_from_context"] = True
            options["_prepared_assessment_window_days"] = _milk_assessment_window_days(intake_data)
        intake_clinical = intake_data.get("clinical_assessment") if isinstance(intake_data.get("clinical_assessment"), dict) else {}
        if intake_clinical and not isinstance(options.get("prepared_clinical_assessment"), dict):
            options["prepared_clinical_assessment"] = intake_clinical
        return options
    return options


def _milk_assessment_window_days(assessment_data: Any) -> int:
    if not isinstance(assessment_data, dict):
        return 0
    window = assessment_data.get("window") if isinstance(assessment_data.get("window"), dict) else {}
    return _to_int(window.get("window_days"), 0)


def _arguments_with_cached_milk_context(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    state = inputs.get("_milk_management_state")
    if not isinstance(state, dict):
        return arguments
    intake = state.get("analysis_intake") if isinstance(state.get("analysis_intake"), dict) else {}
    assessment = intake.get("assessment_result") if isinstance(intake.get("assessment_result"), dict) else {}
    data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
    clinical = data.get("clinical_assessment") if isinstance(data.get("clinical_assessment"), dict) else {}
    if not clinical and isinstance(intake.get("analysis_context"), dict):
        context = intake["analysis_context"]
        merged = dict(arguments)
        if not isinstance(merged.get("infant_signals"), dict) and isinstance(context.get("infant_signals"), dict):
            merged["infant_signals"] = context["infant_signals"]
        if not isinstance(merged.get("maternal_symptoms"), dict) and isinstance(context.get("maternal_symptoms"), dict):
            merged["maternal_symptoms"] = context["maternal_symptoms"]
        return merged
    if not clinical:
        return arguments
    domains = clinical.get("domains") if isinstance(clinical.get("domains"), dict) else {}
    previous_infant = _signals_from_previous_infant_domain(domains.get("infant_intake"))
    previous_maternal = _signals_from_previous_maternal_domain(domains.get("maternal_breast_symptoms"))
    if not previous_infant and not previous_maternal:
        return arguments

    merged = dict(arguments)
    current_infant = merged.get("infant_signals") if isinstance(merged.get("infant_signals"), dict) else {}
    current_maternal = merged.get("maternal_symptoms") if isinstance(merged.get("maternal_symptoms"), dict) else {}
    if previous_infant:
        merged["infant_signals"] = {**previous_infant, **current_infant}
    if previous_maternal:
        merged["maternal_symptoms"] = {**previous_maternal, **current_maternal}
    return merged


def _arguments_with_cached_milk_plan_preview(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    if str(arguments.get("operation") or "").strip() != "create":
        return arguments
    state = inputs.get("_milk_management_state")
    if not isinstance(state, dict):
        return arguments
    intake = state.get("analysis_intake") if isinstance(state.get("analysis_intake"), dict) else {}
    preview = intake.get("plan_preview") if isinstance(intake.get("plan_preview"), dict) else {}
    if not preview:
        preview = state.get("last_plan_preview")
    if not isinstance(preview, dict) or str(preview.get("status") or "").strip() != "plan_preview_ready":
        return arguments
    draft = preview.get("draft") if isinstance(preview.get("draft"), dict) else {}
    if not draft:
        return arguments

    merged = dict(arguments)
    merged["confirmed_plan"] = draft
    if not norm_text(merged.get("idempotency_key")):
        key = norm_text(preview.get("idempotency_key"))
        if key:
            merged["idempotency_key"] = key
    if not norm_text(merged.get("calendar_write_strategy")):
        strategy = _calendar_write_strategy_from_preview(preview)
        if not strategy:
            strategy = _calendar_write_strategy_from_user_message(inputs.get("user_message"))
        if strategy:
            merged["calendar_write_strategy"] = strategy
    return merged


def _calendar_write_strategy_from_preview(preview: dict[str, Any]) -> str:
    calendar_delta = preview.get("calendar_delta") if isinstance(preview.get("calendar_delta"), dict) else {}
    if calendar_delta.get("calendar_write_strategy_required") or calendar_delta.get("requires_calendar_write_strategy"):
        return ""
    return norm_text(calendar_delta.get("recommended_calendar_write_strategy"))


def _calendar_write_strategy_from_user_message(message: Any) -> str:
    text = norm_text(message)
    if not text:
        return ""
    if any(token in text for token in ("不要", "不用", "先不", "暂不", "取消", "不保存", "不同步")):
        return ""
    if any(token in text for token in ("替换", "覆盖", "取代", "换掉", "旧计划", "未来未完成")):
        return "replace_future_plan_tasks"
    if any(token in text for token in ("追加", "加到", "保留现有", "保留原有", "不覆盖", "现有日程")):
        return "append"
    return ""


def _arguments_with_calendar_reschedule_from_message(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    text = norm_text(inputs.get("user_message"))
    if not text:
        return arguments
    merged = dict(arguments)
    if not _calendar_reschedule_has_dates(merged):
        dates = _calendar_reschedule_dates_from_message(text, inputs)
        if len(dates) == 1:
            merged["target_date"] = dates[0]
            merged.setdefault("target_dates", "[]")
        elif len(dates) > 1:
            merged["target_dates"] = json.dumps(dates, ensure_ascii=False)
            if not norm_text(merged.get("target_date")):
                merged["target_date"] = None
    if not _parse_json_list(merged.get("busy_windows")):
        window = _calendar_busy_window_from_message(text)
        if window:
            merged["busy_windows"] = json.dumps([window], ensure_ascii=False)
    if not _parse_json_list(merged.get("adjustable_item_types")):
        merged["adjustable_item_types"] = json.dumps(["吸奶", "亲喂"], ensure_ascii=False)
    if not to_int(merged.get("default_duration_minutes"), 0):
        merged["default_duration_minutes"] = 20
    if not to_int(merged.get("min_gap_minutes"), 0):
        merged["min_gap_minutes"] = 90
    if "include_busy_events" not in merged:
        merged["include_busy_events"] = True
    return merged


def _calendar_reschedule_has_dates(arguments: dict[str, Any]) -> bool:
    if norm_text(arguments.get("target_date")):
        return True
    if _parse_json_list(arguments.get("target_dates")):
        return True
    return bool(norm_text(arguments.get("start_date")) and norm_text(arguments.get("end_date")))


def _calendar_reschedule_dates_from_message(text: str, inputs: RuntimeInputs) -> list[str]:
    base = _calendar_reschedule_base_date(inputs)
    if "后天" in text:
        return [(base + timedelta(days=2)).date().isoformat()]
    if "明天" in text:
        return [(base + timedelta(days=1)).date().isoformat()]
    if "今天" in text:
        return [base.date().isoformat()]

    days = _calendar_reschedule_day_count_from_message(text)
    if days <= 0:
        return []
    start = base + timedelta(days=1)
    return [(start + timedelta(days=offset)).date().isoformat() for offset in range(days)]


def _calendar_reschedule_base_date(inputs: RuntimeInputs) -> datetime:
    for key in ("message_sent_at", "current_date"):
        parsed = _parse_handler_datetime(inputs.get(key))
        if parsed is not None:
            return parsed
    return datetime.now()


def _parse_handler_datetime(value: Any) -> datetime | None:
    text = norm_text(value)
    if not text:
        return None
    for candidate in (text, text.replace("/", "-")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.replace("/", "-"), fmt)
        except ValueError:
            continue
    return None


def _calendar_reschedule_day_count_from_message(text: str) -> int:
    match = re.search(r"(?:接下来|未来|之后|后面)\s*([一二两三四五六七八九十\d]+)\s*天", text)
    if match:
        return max(min(_chinese_or_int(match.group(1)), 14), 0)
    if any(term in text for term in ("接下来几天", "未来几天", "之后几天", "后面几天")):
        return 3
    if "每天" in text and any(term in text for term in ("接下来", "未来", "之后", "后面")):
        return 3
    return 0


def _calendar_busy_window_from_message(text: str) -> dict[str, str] | None:
    match = re.search(
        r"(上午|早上|下午|晚上|中午)?\s*(\d{1,2})(?:[:：](\d{1,2}))?\s*(?:~|～|-|到|至)\s*(\d{1,2})(?:[:：](\d{1,2}))?\s*点?",
        text,
    )
    if not match:
        return None
    period, start_hour, start_minute, end_hour, end_minute = match.groups()
    start = _clock_text_from_parts(start_hour, start_minute, period)
    end = _clock_text_from_parts(end_hour, end_minute, period)
    if not start or not end:
        return None
    return {"start_time": start, "end_time": end, "content": _calendar_busy_content_from_message(text)}


def _clock_text_from_parts(hour_text: str, minute_text: str | None, period: str | None) -> str:
    hour = to_int(hour_text, -1)
    minute = to_int(minute_text, 0) if minute_text else 0
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return ""
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def _calendar_busy_content_from_message(text: str) -> str:
    if "会议" in text or "有会" in text or "开会" in text:
        return "会议"
    if "产检" in text:
        return "产检"
    if "外出" in text or "出门" in text:
        return "外出"
    if "通勤" in text:
        return "通勤"
    return "不可用时间"


def _chinese_or_int(value: Any) -> int:
    text = norm_text(value)
    if text.isdigit():
        return to_int(text, 0)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if text in digits:
        return digits[text]
    if text.startswith("十") and len(text) == 2:
        return 10 + digits.get(text[1], 0)
    if text.endswith("十") and len(text) == 2:
        return digits.get(text[0], 0) * 10
    if len(text) == 3 and text[1] == "十":
        return digits.get(text[0], 0) * 10 + digits.get(text[2], 0)
    return 0


def _arguments_with_cached_calendar_adjustment(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    state = inputs.get("_milk_management_state")
    if not isinstance(state, dict):
        return arguments
    pending = state.get("pending_calendar_adjustment") if isinstance(state.get("pending_calendar_adjustment"), dict) else {}
    if not pending:
        return arguments
    proposal = pending.get("proposal") if isinstance(pending.get("proposal"), dict) else {}
    operation = norm_text(pending.get("operation"))
    if operation not in {"apply_adjustment", "apply_reschedule"} or not proposal:
        return arguments

    merged = dict(arguments)
    if norm_text(merged.get("operation")) not in {"apply_adjustment", "apply_reschedule"}:
        merged["operation"] = operation
    if not norm_text(merged.get("target_date")):
        target_date = norm_text(pending.get("target_date")) or norm_text(proposal.get("target_date"))
        if target_date:
            merged["target_date"] = target_date
    if _calendar_mutate_proposal_is_empty(merged.get("proposal")):
        merged["proposal"] = proposal
    if not norm_text(merged.get("idempotency_key")):
        key = norm_text(pending.get("idempotency_key")) or _calendar_adjustment_idempotency_key(proposal)
        if key:
            merged["idempotency_key"] = key
    return merged


def _calendar_mutate_proposal_is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        return not value
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "{}":
            return True
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return False
        return not isinstance(parsed, dict) or not parsed
    return False


def _calendar_adjustment_idempotency_key(proposal: dict[str, Any]) -> str:
    if not proposal:
        return ""
    payload = json.dumps(proposal, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"milk-calendar-adjustment-{digest}"


def _signals_from_previous_infant_domain(domain_value: Any) -> dict[str, Any]:
    domain = domain_value if isinstance(domain_value, dict) else {}
    provided = {str(item) for item in domain.get("provided_fields", [])} if isinstance(domain.get("provided_fields"), list) else set()
    signals: dict[str, Any] = {}
    for key in ("wet_diapers_24h", "baby_state", "feeding_satisfaction", "poor_feeding", "poor_latch", "lethargy", "recent_weight", "weight_trend", "growth_concern"):
        value = domain.get(key)
        if key in provided or _provided_signal_value(value):
            signals[key] = value
    if "wet_diapers_24h" not in signals and domain.get("wet_diapers_text"):
        signals["wet_diapers_24h"] = domain.get("wet_diapers_text")
    return signals


def _signals_from_previous_maternal_domain(domain_value: Any) -> dict[str, Any]:
    domain = domain_value if isinstance(domain_value, dict) else {}
    provided = {str(item) for item in domain.get("provided_fields", [])} if isinstance(domain.get("provided_fields"), list) else set()
    signals: dict[str, Any] = {}
    for key in (
        "fever",
        "chills",
        "breast_redness",
        "lump_or_hard_area",
        "worsening_pain",
        "nipple_damage",
        "recurrent_plug",
        "breast_fullness",
        "engorgement",
        "post_pump_fullness",
        "incomplete_emptying",
        "pain_level",
        "symptom_text",
    ):
        value = domain.get(key)
        if key in provided or _provided_signal_value(value):
            signals[key] = value
    return signals


def _provided_signal_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value is True
    return value not in (None, "", [])


def _attach_milk_flow_decision(result: dict[str, Any]) -> None:
    data = result.get("data")
    if not isinstance(data, dict):
        return
    data["milk_flow_decision"] = _milk_flow_decision_for_assessment(data)


def _attach_milk_plan_flow_decision(result: dict[str, Any]) -> None:
    data = result.get("data")
    if not isinstance(data, dict):
        return
    if isinstance(data.get("milk_flow_decision"), dict):
        return
    if str(result.get("status") or "").strip() == "plan_preview_ready":
        data["milk_flow_decision"] = {
            "stage": "plan_preview_ready",
            "missing_user_inputs": [],
            "plan_decision": {
                "can_start_plan": True,
                "recommended_plan_type": _plan_type_from_draft(data.get("draft")),
                "reason_for_user": "已经生成计划草稿，保存前需要用户确认。",
                "next_tool": "milk_plan_mutate",
            },
        }
    elif str(result.get("status") or "").strip() == "plan_preview_not_recommended":
        eligibility = data.get("eligibility") if isinstance(data.get("eligibility"), dict) else {}
        data["milk_flow_decision"] = {
            "stage": "plan_blocked",
            "missing_user_inputs": [],
            "plan_decision": {
                "can_start_plan": False,
                "recommended_plan_type": eligibility.get("suggested_plan_type"),
                "reason_for_user": str(eligibility.get("message") or result.get("summary") or "当前不适合直接生成计划。"),
                "next_tool": None,
            },
        }


def _milk_flow_decision_for_assessment(data: dict[str, Any]) -> dict[str, Any]:
    normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    status = str(normality.get("overall_status") or data.get("assessment_status") or "").strip()
    clinical = data.get("clinical_assessment") if isinstance(data.get("clinical_assessment"), dict) else {}
    missing_fields = _missing_clinical_context_fields(clinical)
    if missing_fields:
        return _milk_flow_decision_for_missing_context(
            missing_fields,
            stage="need_more_user_context",
            next_tool="milk_analysis_intake_manage",
        )
    plan_gate = clinical.get("plan_gate") if isinstance(clinical.get("plan_gate"), dict) else {}
    if plan_gate.get("allowed") is False:
        return _milk_flow_decision_for_plan_blocked(clinical, "")
    if status == "under_supply_alert":
        return {
            "stage": "plan_ready_to_preview",
            "missing_user_inputs": [],
            "facts_from_database": _milk_flow_database_facts(data),
            "plan_decision": {
                "can_start_plan": True,
                "recommended_plan_type": "increase_milk",
                "reason_for_user": "近 7 天奶量产出偏低；宝宝和妈妈当前信息没有提示需要先暂停计划。",
                "next_tool": "milk_plan_preview_create",
            },
        }
    if status == "normal":
        return {
            "stage": "analysis_complete",
            "missing_user_inputs": [],
            "facts_from_database": _milk_flow_database_facts(data),
            "plan_decision": {
                "can_start_plan": True,
                "recommended_plan_type": "maintain_milk",
                "reason_for_user": "近期奶量大体在可接受范围；如需更稳定安排，可进入稳奶计划。",
                "next_tool": "milk_plan_preview_create",
            },
        }
    return {
        "stage": "analysis_complete",
        "missing_user_inputs": [],
        "facts_from_database": _milk_flow_database_facts(data),
        "plan_decision": {
            "can_start_plan": False,
            "recommended_plan_type": None,
            "reason_for_user": "当前没有明确的新计划方向，先解释分析结论或继续观察。",
            "next_tool": None,
        },
    }


def _milk_flow_database_facts(data: dict[str, Any]) -> dict[str, Any]:
    normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    days = normality.get("days") if isinstance(normality.get("days"), list) else []
    valid_days = [item for item in days if isinstance(item, dict) and item.get("ok") is True]
    rhythm = data.get("recent_milk_rhythm") if isinstance(data.get("recent_milk_rhythm"), dict) else {}
    rhythm_summary = rhythm.get("summary") if isinstance(rhythm.get("summary"), dict) else {}
    return {
        "window": data.get("window"),
        "valid_days": len(valid_days),
        "low_days": len([item for item in valid_days if str(item.get("status") or "") == "low"]),
        "recent_rhythm_usable_for_schedule": rhythm_summary.get("usable_for_schedule"),
        "recent_rhythm_basis_date": rhythm_summary.get("basis_date"),
        "typical_pumping_times": rhythm_summary.get("typical_pumping_times"),
        "typical_nursing_times": rhythm_summary.get("typical_nursing_times"),
    }


def _milk_flow_decision_for_missing_context(missing_fields: list[str], *, stage: str, next_tool: str) -> dict[str, Any]:
    current_field, next_question = _clinical_context_current_question(missing_fields)
    return {
        "stage": stage,
        "missing_user_inputs": missing_fields,
        "current_missing_field": current_field,
        "current_question": next_question,
        "remaining_count": len(missing_fields),
        "plan_decision": {
            "can_start_plan": False,
            "recommended_plan_type": None,
            "reason_for_user": "缺少会影响下一步安排的宝宝或妈妈状态。",
            "next_tool": next_tool,
        },
    }


def _milk_flow_decision_for_more_records() -> dict[str, Any]:
    return {
        "stage": "need_more_records",
        "missing_user_inputs": [],
        "plan_decision": {
            "can_start_plan": False,
            "recommended_plan_type": None,
            "reason_for_user": "近期记录不足。",
            "next_tool": None,
        },
    }


def _milk_flow_decision_for_missing_plan_type() -> dict[str, Any]:
    return {
        "stage": "need_plan_direction",
        "missing_user_inputs": ["plan_type"],
        "plan_decision": {
            "can_start_plan": False,
            "recommended_plan_type": None,
            "reason_for_user": "缺少计划方向。",
            "next_tool": "milk_plan_preview_create",
        },
    }


def _milk_flow_decision_for_plan_blocked(clinical_data: dict[str, Any], plan_type: str) -> dict[str, Any]:
    plan_gate = clinical_data.get("plan_gate") if isinstance(clinical_data.get("plan_gate"), dict) else {}
    reason = str(plan_gate.get("reason") or "当前不适合直接生成计划。")
    return {
        "stage": "plan_blocked",
        "missing_user_inputs": [],
        "plan_decision": {
            "can_start_plan": False,
            "recommended_plan_type": plan_type or None,
            "reason_for_user": reason,
            "next_tool": None,
        },
    }


def _plan_type_from_draft(value: Any) -> str | None:
    draft = value if isinstance(value, dict) else {}
    plan_type = str(draft.get("plan_type") or "").strip()
    return plan_type or None


def _normalized_options(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _build_milk_analysis_card_json(data: dict[str, Any]) -> dict[str, Any]:
    window = data.get("window") if isinstance(data.get("window"), dict) else {}
    pumping = data.get("pumping_summary") if isinstance(data.get("pumping_summary"), dict) else {}
    calendar = data.get("calendar_task_summary") if isinstance(data.get("calendar_task_summary"), dict) else {}
    normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    window_days = max(_to_int(window.get("window_days"), _to_int(calendar.get("days"), 1)), 1)
    days = normality.get("days") if isinstance(normality.get("days"), list) else []
    valid_days = [item for item in days if isinstance(item, dict) and item.get("ok") is True]
    estimated_values = [_to_float(item.get("estimated_daily_milk_ml"), 0.0) for item in valid_days if item.get("estimated_daily_milk_ml") is not None]
    estimated_range = _range_text(estimated_values, "ml/天")
    reference_range = _reference_interval_text(valid_days)
    total_ml = _to_float(pumping.get("total_ml"), 0.0)
    daily_measured_ml = total_ml / window_days if window_days > 0 else 0.0
    status = str(normality.get("overall_status") or data.get("assessment_status") or "").strip()
    status_label, status_tone = _milk_status_label(status)
    headline = _milk_analysis_headline(status)
    trend_text = _milk_trend_text(valid_days)

    return {
        "title": "奶量分析",
        "subtitle": _window_label(window, fallback_days=window_days),
        "status": status,
        "status_label": status_label,
        "status_tone": status_tone,
        "headline": headline,
        "sections": [
            {
                "id": "milk",
                "title": "数据统计",
                "tone": "attention" if status in {"under_supply_alert", "over_supply_alert"} else "normal",
                "metrics": [
                    {"label": "实测吸奶", "value": f"{_format_number(daily_measured_ml)} ml/天", "detail": f"近 {window_days} 天共 {_format_number(total_ml)} ml"},
                    {"label": "含亲喂估算", "value": estimated_range or "—", "detail": "亲喂部分为估算"},
                    {"label": "参考区间", "value": reference_range or "—", "detail": "同阶段常见范围"},
                ],
                "items": [trend_text],
            },
            {
                "id": "daily_records",
                "title": "近7天记录",
                "tone": "default",
                "items": _milk_daily_record_items(valid_days),
            },
            {
                "id": "next",
                "title": "下一步",
                "tone": "default",
                "items": [_milk_next_step(status)],
            },
        ],
    }


def _milk_daily_record_items(days: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for day in days[-7:]:
        date_text = _short_date(str(day.get("date") or ""))
        estimated = day.get("estimated_daily_milk_ml")
        pumping_ml = _to_float(day.get("pumping_ml_total"), 0.0)
        pumping_count = _to_int(day.get("pumping_count"), 0)
        nursing_count = _to_int(day.get("breastfeeding_count"), 0)
        bottle_ml = _to_float(day.get("breastmilk_bottle_ml"), 0.0)
        reference = day.get("yield_reference") if isinstance(day.get("yield_reference"), dict) else {}
        ref_low = _to_float(reference.get("p15"), 0.0)
        ref_high = _to_float(reference.get("p85"), 0.0)
        status = _milk_day_status_text(str(day.get("status") or ""))

        parts: list[str] = []
        if estimated is not None:
            parts.append(f"估算{_format_number(_to_float(estimated))} ml")
        if pumping_ml > 0 or pumping_count > 0:
            parts.append(f"吸奶{_format_number(pumping_ml)} ml/{pumping_count}次")
        if nursing_count > 0:
            parts.append(f"亲喂{nursing_count}次")
        if bottle_ml > 0:
            parts.append(f"母乳瓶喂{_format_number(bottle_ml)} ml")
        if ref_low > 0 and ref_high > 0:
            parts.append(f"参考{_format_number(ref_low)}-{_format_number(ref_high)} ml/天")
        if status:
            parts.append(f"状态{status}")
        if parts:
            prefix = f"{date_text}：" if date_text else ""
            items.append(prefix + "，".join(parts))
    return items or ["近 7 天没有足够可计算的逐日奶量记录。"]


def _milk_day_status_text(status: str) -> str:
    if status == "low":
        return "偏低"
    if status == "high":
        return "偏高"
    if status == "normal":
        return "正常"
    if status in {"error", "insufficient_data"}:
        return "记录不足"
    return ""


def _build_milk_plan_card_json(data: dict[str, Any]) -> dict[str, Any]:
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    rules = draft.get("plan_rules") if isinstance(draft.get("plan_rules"), dict) else {}
    observation = draft.get("observation_context") if isinstance(draft.get("observation_context"), dict) else {}
    control = draft.get("control_strategy") if isinstance(draft.get("control_strategy"), dict) else {}
    card_status = str(data.get("card_status") or "preview").strip()
    is_confirmed = card_status in {"confirmed", "saved", "created"}
    plan_days = _to_int(draft.get("plan_days"), 0)
    target_daily_ml = _to_float(draft.get("target_daily_ml"), 0.0)
    current_daily_ml = _to_float(draft.get("current_daily_ml"), 0.0)
    delta_ml = max(target_daily_ml - current_daily_ml, 0.0)
    desired_count = _to_int(rules.get("desired_pumping_count"), 0)
    current_count = _to_int(rules.get("current_pumping_count"), 0)
    planned_count = desired_count or current_count
    added = max(desired_count - current_count, 0)
    plan_type = str(draft.get("plan_type") or "").strip()
    how_items = [
        str(control.get("session_goal") or _milk_plan_session_goal(draft)),
        str(control.get("when_to_stop_each_time") or _milk_plan_stop_rule(draft)),
    ]
    if plan_type != "increase_milk":
        how_items.append(str(control.get("review_timing") or _milk_plan_review_rule(draft)))

    return {
        "title": _milk_plan_title(draft),
        "status": "confirmed" if is_confirmed else "preview",
        "status_label": "已确认" if is_confirmed else "待确认",
        "status_tone": "normal" if is_confirmed else "attention",
        "sections": [
            {
                "id": "target",
                "title": "目标",
                "tone": "normal",
                "items": [
                    f"当前每日奶量约 {_format_number(current_daily_ml)} ml，目标约 {_format_number(target_daily_ml)} ml。",
                    f"这轮先温和增加约 {_format_number(delta_ml)} ml/天。",
                ],
            },
            {
                "id": "plan",
                "title": "计划",
                "tone": "info",
                "metrics": [
                    {"label": "周期", "value": f"{plan_days} 天", "detail": "从明天开始"},
                    {
                        "label": "现在",
                        "value": f"{_format_number(_to_float(observation.get('calendar_pump_tasks_per_day'), 0.0))} 次/天",
                        "detail": "吸奶任务",
                    },
                    {
                        "label": "计划",
                        "value": f"{planned_count} 次/天",
                        "detail": "吸奶任务",
                    },
                ],
                "items": [
                    "先在当前吸奶/亲喂节奏上微调，三天后我们根据奶量变化重新调整。",
                    f"保留原有 {current_count} 个吸奶任务，新增 {added} 个吸奶任务。",
                    "具体日程表不在这里展开，可以向我提问，也可以在保存计划后到计划页查看。",
                ],
            },
            {
                "id": "how",
                "title": "每次怎么做",
                "tone": "default",
                "items": how_items,
            },
        ],
    }


def _milk_plan_title(draft: dict[str, Any]) -> str:
    plan_type = str(draft.get("plan_type") or "").strip()
    if plan_type == "increase_milk":
        return "追奶计划"
    if plan_type == "maintain_milk":
        return "稳奶计划"
    if plan_type == "decrease_milk":
        return "减奶计划"

    plan_name = str(draft.get("plan_name") or "").strip()
    if "追奶" in plan_name:
        return "追奶计划"
    if "稳奶" in plan_name:
        return "稳奶计划"
    if "减奶" in plan_name:
        return "减奶计划"
    return plan_name or "奶量计划"


def _milk_plan_session_goal(draft: dict[str, Any]) -> str:
    plan_type = str(draft.get("plan_type") or "").strip()
    if plan_type == "increase_milk":
        return "每次结束吸奶时看是否还有胀感，如果有的话可以多吸一会儿（1～2分钟）直到胀感减轻或消失。"
    if plan_type == "decrease_milk":
        return "每次只吸到舒服，不追求排得很空。"
    return "每次稳定、舒服地移出即可。"


def _milk_plan_stop_rule(draft: dict[str, Any]) -> str:
    plan_type = str(draft.get("plan_type") or "").strip()
    if plan_type == "increase_milk":
        return "吸奶过程中如果有明显痛感，暂停吸奶并联系医生或IBCLC顾问，我可以帮你在线接通IBCLC顾问。"
    if plan_type == "decrease_milk":
        return "胀得难受时少量移出到舒服就停。"
    return "吸奶到奶流明显变慢、乳房舒服一些就可以。"


def _milk_plan_review_rule(draft: dict[str, Any]) -> str:
    plan_type = str(draft.get("plan_type") or "").strip()
    if plan_type == "decrease_milk":
        return "每 2-3 天看胀痛、硬块、总量和宝宝状态，再决定下一步。"
    if plan_type == "increase_milk":
        return ""
    return "第 3 天和第 7 天复盘奶量、宝宝表现和妈妈舒适度。"


def _milk_plan_preview_followup_message(data: dict[str, Any]) -> str:
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    title = _milk_plan_title(draft)
    overview = _milk_plan_followup_overview(draft)
    calendar_delta = data.get("calendar_delta") if isinstance(data.get("calendar_delta"), dict) else {}
    requires_strategy = bool(calendar_delta.get("requires_calendar_write_strategy") or calendar_delta.get("calendar_write_strategy_required"))
    if requires_strategy:
        return (
            f"{title}已经准备好了。\n\n"
            f"{overview}\n\n"
            "如果要同步到计划页，我先和你确认一下：是追加到现有日程，还是替换未来未完成的旧计划任务？"
        )
    return (
        f"{title}已经准备好了。\n\n"
        f"{overview}\n\n"
        "如果方向没问题，我可以帮你同步到计划页；也可以先照着你的作息，把时间再调顺一点。"
    )


def _milk_plan_followup_overview(draft: dict[str, Any]) -> str:
    rules = draft.get("plan_rules") if isinstance(draft.get("plan_rules"), dict) else {}
    plan_days = _to_int(draft.get("plan_days"), 0)
    desired_count = _to_int(rules.get("desired_pumping_count"), 0)
    current_count = _to_int(rules.get("current_pumping_count"), 0)
    planned_count = desired_count or current_count or _daily_schedule_count(draft)
    period = f"从明天开始，连续 {plan_days} 天" if plan_days > 0 else "从明天开始"
    count_line = f"{period}，每天安排 {planned_count} 次吸奶任务。" if planned_count > 0 else f"{period}执行。"
    return f"{count_line}\n\n每次怎么做：{_milk_plan_session_guidance(draft)}"


def _daily_schedule_count(draft: dict[str, Any]) -> int:
    templates = draft.get("daily_schedule_templates") if isinstance(draft.get("daily_schedule_templates"), list) else []
    for template in templates:
        if not isinstance(template, dict):
            continue
        items = template.get("items") if isinstance(template.get("items"), list) else []
        if items:
            return len(items)
    template = draft.get("daily_schedule_template") if isinstance(draft.get("daily_schedule_template"), dict) else {}
    items = template.get("items") if isinstance(template.get("items"), list) else []
    return len(items)


def _milk_plan_session_guidance(draft: dict[str, Any]) -> str:
    plan_type = str(draft.get("plan_type") or "").strip()
    if plan_type == "increase_milk":
        return "每次结束吸奶时看是否还有胀感，如果有的话可以多吸一会儿（1～2分钟）直到胀感减轻或消失。吸奶过程中如果有明显痛感，暂停吸奶并联系医生或IBCLC顾问，我可以帮你在线接通IBCLC顾问。"
    if plan_type == "decrease_milk":
        return "单次吸奶或亲喂不用刻意排得特别空，重点是让身体舒服下来；如果只是胀，吸到不难受就可以停，不要继续给身体太强的增奶信号。"
    return "单次吸奶或亲喂保持稳定、舒服即可；吸奶到奶流明显变慢、乳房舒服一些就可以，亲喂就看宝宝吞咽和满足感。"


def _milk_plan_saved_followup_message(result: dict[str, Any]) -> str:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    overview = _milk_plan_followup_overview(plan) if plan else ""
    inserted = _to_int(data.get("inserted_calendar_count"), 0)
    if inserted > 0:
        if overview:
            return (
                "已经同步到计划页了，接下来会按这个节奏提醒你。\n\n"
                f"{overview}\n\n"
                "你可以去「计划」里查看这几天的安排。最近几天如果有会议、外出、上班、睡眠安排，或者其他不方便吸奶的时间，也可以告诉我，我再帮你把时间调顺一点。"
            )
        return (
            "已经同步到计划页了，接下来会按这个节奏提醒你。\n\n"
            "你可以去「计划」里查看这几天的安排。最近几天如果有会议、外出、上班、睡眠安排，或者其他不方便吸奶的时间，也可以告诉我，我再帮你把时间调顺一点。"
        )
    if overview:
        return (
            "计划已经保存好了，你可以去「计划」里查看。\n\n"
            f"{overview}\n\n"
            "最近几天如果有会议、外出、上班、睡眠安排，或者其他不方便吸奶的时间，也可以告诉我，我再帮你把时间调顺一点。"
        )
    return (
        "计划已经保存好了，你可以去「计划」里查看。\n\n"
        "最近几天如果有会议、外出、上班、睡眠安排，或者其他不方便吸奶的时间，也可以告诉我，我再帮你把时间调顺一点。"
    )


def _milk_plan_gate_followup_message(clinical_data: dict[str, Any]) -> str:
    risk_level = str(clinical_data.get("risk_level") or "").strip()
    if risk_level in {"medical_recommended", "urgent"}:
        return (
            "现在先不急着做奶量计划，这一步我们慢一点来。\n\n"
            "你描述的情况更适合先联系医生或线下医疗渠道确认；等身体这边稳住了，我再陪你继续整理奶量安排。"
        )
    if risk_level == "ibclc_recommended":
        return (
            "现在先不急着做完整计划，这不是你做得不够好。\n\n"
            "更适合先把含乳、排乳和乳房不适一起看一遍；如果你愿意，我可以帮你打开 IBCLC 咨询入口。"
        )
    return (
        "现在先不急着做完整计划。我们把会影响判断的信息补齐，后面会更稳。\n\n"
        "你补充完这些情况后，我再继续陪你做安排。"
    )


def _query_plan(arguments: dict[str, Any]) -> dict[str, Any]:
    plan_id = arguments.get("plan_id")
    if plan_id is not None:
        return dict(get_milk_plan(user_id=arguments["user_id"], plan_id=plan_id))
    return dict(
        list_milk_plans(
            user_id=arguments["user_id"],
            plan_type=arguments.get("plan_type"),
            limit=arguments.get("limit", 10),
        )
    )


def _mutate_plan(arguments: dict[str, Any]) -> dict[str, Any]:
    operation = _operation(arguments)
    if operation == "create":
        plan = arguments.get("confirmed_plan")
        validation = dict(validate_milk_plan(user_id=arguments["user_id"], plan=plan))
        validation_data = validation.get("data") if isinstance(validation.get("data"), dict) else {}
        if not validation.get("ok") or validation_data.get("valid") is False:
            return {
                "ok": False,
                "status": "milk_plan_invalid",
                "summary": validation.get("summary", "计划校验未通过。"),
                "data": {"validation": validation_data},
            }
        idempotency_key = norm_text(arguments.get("idempotency_key")) or _milk_plan_preview_idempotency_key(_parse_json_object(plan))
        if not idempotency_key:
            return {
                "ok": False,
                "status": "milk_plan_invalid",
                "summary": "缺少计划同步标识，暂时不能保存计划。",
                "data": {"validation": validation_data},
            }
        result = dict(
            apply_milk_plan(
                user_id=arguments["user_id"],
                confirmed_plan=plan,
                idempotency_key=idempotency_key,
                calendar_write_strategy=arguments.get("calendar_write_strategy"),
            )
        )
        result.setdefault("data", {})
        if isinstance(result["data"], dict):
            result["data"]["validation"] = validation_data
        if result.get("ok") and isinstance(plan, dict):
            result["card"] = _milk_plan_card(plan, card_status="confirmed")
            result["assistant_followup"] = {"message": _milk_plan_saved_followup_message(result)}
        return result
    if operation == "update":
        plan = _plan_from_patch_for_validation(arguments.get("patch"))
        if plan:
            validation = dict(validate_milk_plan(user_id=arguments["user_id"], plan=plan))
            validation_data = validation.get("data") if isinstance(validation.get("data"), dict) else {}
            if not validation.get("ok") or validation_data.get("valid") is False:
                return {
                    "ok": False,
                    "status": "milk_plan_invalid",
                    "summary": validation.get("summary", "计划校验未通过。"),
                    "data": {"validation": validation_data},
                }
        return dict(
            update_milk_plan(
                user_id=arguments["user_id"],
                plan_id=arguments.get("plan_id"),
                patch=arguments.get("patch"),
                idempotency_key=arguments["idempotency_key"],
                reexpand_calendar=bool(arguments.get("reexpand_calendar")),
            )
        )
    if operation == "delete":
        return dict(
            delete_milk_plan(
                user_id=arguments["user_id"],
                plan_id=arguments.get("plan_id"),
                idempotency_key=arguments["idempotency_key"],
                delete_calendar_items=bool(arguments.get("delete_calendar_items")),
            )
        )
    raise ValueError(f"Unsupported milk_plan_mutate operation: {operation}")


def _mutate_record(arguments: dict[str, Any]) -> dict[str, Any]:
    operation = _operation(arguments)
    if operation == "create":
        return dict(
            create_record(
                **_pick(arguments, "user_id", "record_kind", "occurred_at", "amount_ml", "duration_minutes", "infant_id", "title", "idempotency_key")
            )
        )
    if operation == "update":
        return dict(update_record(**_pick(arguments, "user_id", "record_kind", "record_id", "patch", "idempotency_key")))
    if operation == "delete":
        return dict(delete_record(**_pick(arguments, "user_id", "record_kind", "record_id", "idempotency_key")))
    raise ValueError(f"Unsupported milk_record_mutate operation: {operation}")


def _query_calendar(arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    query_mode = str(arguments.get("query_mode") or "range").strip()
    runtime_date = _runtime_target_date(inputs) or datetime.now().date().isoformat()
    target_date = str(arguments.get("target_date") or runtime_date).strip()
    if query_mode == "current_plan":
        return dict(
            get_current_calendar_plan(
                user_id=arguments["user_id"],
                target_date=target_date,
                lookahead_days=arguments.get("lookahead_days", 14),
                item_type=arguments.get("item_type"),
                include_items=arguments.get("include_items") is not False,
                limit=arguments.get("limit", 200),
            )
        )
    if query_mode in {"by_date", "day"}:
        return dict(
            get_calendar_day(
                user_id=arguments["user_id"],
                target_date=target_date,
                plan_id=arguments.get("plan_id"),
                item_type=arguments.get("item_type"),
            )
        )
    if query_mode == "today_overview":
        return dict(get_today_overview(user_id=arguments["user_id"], target_date=target_date, plan_id=arguments.get("plan_id")))
    if query_mode == "today_summary":
        return dict(get_today_summary(user_id=arguments["user_id"], target_date=target_date, plan_id=arguments.get("plan_id")))
    return dict(
        get_calendar_range(
            user_id=arguments["user_id"],
            start_at=arguments.get("start_at") or target_date,
            end_at=arguments.get("end_at") or target_date,
            plan_id=arguments.get("plan_id"),
            item_type=arguments.get("item_type"),
            include_items=arguments.get("include_items") is not False,
            limit=arguments.get("limit", 200),
        )
    )


def _mutate_calendar(arguments: dict[str, Any]) -> dict[str, Any]:
    operation = _operation(arguments)
    if operation == "apply_adjustment":
        return dict(apply_calendar_adjustment(**_pick(arguments, "user_id", "target_date", "proposal", "idempotency_key")))
    if operation == "apply_reschedule":
        return dict(apply_calendar_reschedule(**_pick(arguments, "user_id", "target_date", "proposal", "idempotency_key")))
    if operation in {"range_shift", "range_delete", "patch_items"}:
        mapped_operation = {"range_shift": "shift", "range_delete": "delete", "patch_items": "patch_items"}[operation]
        return dict(
            update_calendar_range(
                user_id=arguments["user_id"],
                start_at=arguments.get("start_at"),
                end_at=arguments.get("end_at"),
                operation=mapped_operation,
                patch=arguments.get("patch"),
                plan_id=arguments.get("plan_id"),
                item_type=arguments.get("item_type"),
                idempotency_key=arguments["idempotency_key"],
            )
        )
    if operation == "update_item":
        return dict(update_calendar_item(**_calendar_item_update_arguments(arguments)))
    if operation == "delete_item":
        return dict(delete_calendar_item(user_id=arguments["user_id"], item_id=arguments.get("item_id")))
    raise ValueError(f"Unsupported milk_calendar_mutate operation: {operation}")


def _with_user_id(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    arguments = {key: value for key, value in args.items() if key != "_tool_name"}
    user_id = inputs.get("user_id") or inputs.get("user_profile", {}).get("user_id")
    if not user_id:
        raise ValueError("Milk-management tools require a user_id in runtime inputs.")
    arguments["user_id"] = str(user_id)
    return arguments


def _pick(arguments: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: arguments[key] for key in keys if key in arguments}


def _operation(arguments: dict[str, Any]) -> str:
    return str(arguments.get("operation") or "").strip()


def _runtime_target_date(inputs: RuntimeInputs) -> str:
    for key in ("current_date", "message_sent_at"):
        value = str(inputs.get(key) or "").strip()
        if len(value) >= 10:
            return value[:10]
    return ""


def _plan_from_patch_for_validation(patch: Any) -> dict[str, Any]:
    patch_data = _parse_json_object(patch)
    for key in ("confirmed_plan", "plan", "draft"):
        value = patch_data.get(key)
        if isinstance(value, dict):
            return value
    plan_payload = patch_data.get("plan_payload")
    if isinstance(plan_payload, dict) and isinstance(plan_payload.get("plan"), dict):
        return plan_payload["plan"]
    return {}


def _card_id(prefix: str, window: Any) -> str:
    if isinstance(window, dict):
        left = str(window.get("start_at") or "").strip()[:10]
        right = str(window.get("end_at") or "").strip()[:10]
        token = "-".join(part for part in (left, right) if part)
        if token:
            return f"{prefix}-{token}"
    return prefix


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_number(value: float | int) -> str:
    number = float(value)
    if abs(number - round(number)) < 0.05:
        return str(int(round(number)))
    return f"{number:.1f}"


def _window_label(window: dict[str, Any], *, fallback_days: int) -> str:
    start_at = str(window.get("start_at") or "").strip()
    end_at = str(window.get("end_at") or "").strip()
    start = _short_date(start_at)
    end = _short_date(end_at)
    if start and end:
        return f"近 {fallback_days} 天 · {start}-{end}"
    return f"近 {fallback_days} 天"


def _short_date(value: str) -> str:
    if len(value) < 10:
        return ""
    try:
        month = int(value[5:7])
        day = int(value[8:10])
    except ValueError:
        return ""
    return f"{month}/{day}"


def _feeding_parts(type_counts: dict[str, Any], *, window_days: int) -> str:
    labels: list[str] = []
    for raw_type, raw_count in type_counts.items():
        count = _to_int(raw_count, 0)
        if count <= 0:
            continue
        daily = _format_number(count / max(window_days, 1))
        labels.append(f"{daily} 条{str(raw_type).strip() or '喂养'}")
    return "、".join(labels)


def _range_text(values: list[float], unit: str) -> str:
    cleaned = [value for value in values if value > 0]
    if not cleaned:
        return ""
    low = min(cleaned)
    high = max(cleaned)
    if abs(low - high) < 0.5:
        return f"{_format_number(low)} {unit}"
    return f"{_format_number(low)}-{_format_number(high)} {unit}"


def _reference_interval_text(days: list[dict[str, Any]]) -> str:
    lows: list[float] = []
    highs: list[float] = []
    for item in days:
        reference = item.get("yield_reference")
        if not isinstance(reference, dict):
            continue
        low = _to_float(reference.get("p15"), 0.0)
        high = _to_float(reference.get("p85"), 0.0)
        if low > 0:
            lows.append(low)
        if high > 0:
            highs.append(high)
    if not lows and not highs:
        return ""
    lower = min(lows) if lows else min(highs)
    upper = max(highs) if highs else max(lows)
    if abs(lower - upper) < 0.5:
        return f"{_format_number(lower)} ml/天"
    return f"{_format_number(lower)}-{_format_number(upper)} ml/天"


def _milk_status_label(status: str) -> tuple[str, str]:
    if status == "under_supply_alert":
        return "低于参考区间", "attention"
    if status == "over_supply_alert":
        return "高于参考区间", "attention"
    if status == "normal":
        return "整体正常", "normal"
    return "需补记录", "insufficient"


def _milk_analysis_headline(status: str) -> str:
    if status == "under_supply_alert":
        return "最近整体偏低。已结合近 7 天记录、宝宝状态和妈妈状态完成综合判断；下一步先确认是否需要生成奶量计划。"
    if status == "over_supply_alert":
        return "最近整体偏高。先不用紧张，也不要突然减吸；已结合记录口径、宝宝状态和妈妈状态完成综合判断，后续调整要温和小步。"
    if status == "normal":
        return "最近整体在同阶段常见范围内，说明现在的节奏是能撑住的。先按这个你能坚持的方式走就好。"
    return "现在记录还不太完整，先不用急着下结论。我们把关键记录补一补，再判断会更准。"


def _milk_trend_text(days: list[dict[str, Any]]) -> str:
    values = [_to_float(item.get("estimated_daily_milk_ml"), 0.0) for item in days if item.get("estimated_daily_milk_ml") is not None]
    if len(values) < 2:
        return "目前记录还少，暂时不急着判断趋势。"
    first = values[0]
    last = values[-1]
    if last < first * 0.9:
        return "这几天有一点往下走的迹象，先按综合奶量分析流程确认关键信息，再判断是否需要调整。"
    if last > first * 1.1:
        return "这几天有一点往上走的迹象，可以先观察能不能稳定住。"
    return "没有看到明显一路下降或上升，整体只是有些小波动。"


def _milk_next_step(status: str) -> str:
    if status == "under_supply_alert":
        return "按综合奶量分析流程确认关键信息；信息齐后只询问是否生成奶量计划。"
    if status == "over_supply_alert":
        return "按综合奶量分析流程确认宝宝和妈妈状态；信息齐后再判断是否生成温和减奶或舒适调整计划。"
    if status == "normal":
        return "继续保持现在的记录节奏，过两三天再一起看一次总量和宝宝状态。"
    return "先补一两天关键记录，尤其是吸奶量、亲喂情况，以及宝宝尿布和体重信号。"


def _calendar_item_update_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = _pick(arguments, "user_id", "item_id")
    patch = arguments.get("patch")
    if patch:
        patch_data = _parse_json_object(patch)
        for key in ("start_time", "end_time", "content", "item_type", "finish"):
            if key in patch_data and key not in normalized:
                normalized[key] = patch_data[key]
        if "type" in patch_data and "item_type" not in normalized:
            normalized["item_type"] = patch_data["type"]
    for key in ("start_time", "end_time", "content", "item_type", "finish"):
        if key in arguments and key not in normalized:
            normalized[key] = arguments[key]
    return normalized


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []
