from __future__ import annotations

import json
from typing import Any

from ..services.milk_management.assessment import evaluate_milk_status
from ..services.milk_management.calendar import (
    apply_calendar_adjustment,
    apply_calendar_reschedule,
    delete_calendar_item,
    get_calendar_range,
    preview_calendar_adjustment,
    preview_day_reschedule,
    update_calendar_range,
    update_calendar_item,
)
from ..services.milk_management.clinical_assessment import evaluate_lactation_clinical_status
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
from ..types import RuntimeInputs

def execute_milk_management_tool(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    name = str(args.get("_tool_name") or "")
    arguments = _with_user_id(args, inputs)
    if name in {"milk_status_query", "milk_task_complete", "infant_growth_mutate"} and not arguments.get("target_date"):
        runtime_date = _runtime_target_date(inputs)
        if runtime_date:
            arguments["target_date"] = runtime_date

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
        return _query_calendar(arguments)
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
    if name == "milk_assessment_evaluate":
        render_card = _should_render_milk_analysis_card(arguments, inputs)
        assessment_arguments = _milk_assessment_arguments(arguments, render_card=render_card)
        result = dict(evaluate_milk_status(**assessment_arguments))
        clinical_gate = _clinical_gate_for_analysis(arguments, result=result, render_card=render_card)
        if clinical_gate is not None:
            return clinical_gate
        _attach_clinical_assessment(result, arguments)
        if render_card:
            return _with_milk_analysis_card(result)
        return result
    if name == "milk_clinical_assessment_evaluate":
        return dict(
            evaluate_lactation_clinical_status(
                **_pick(
                    arguments,
                    "user_id",
                    "as_of_time",
                    "window_days",
                    "include_today",
                    "maternal_symptoms",
                    "infant_signals",
                    "requested_plan_type",
                )
            )
        )
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
    if name == "milk_plan_preview":
        return _with_milk_plan_card(_preview_plan(arguments))
    raise ValueError(f"Unknown milk-management tool: {name}")


def _with_milk_analysis_card(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if not result.get("ok") or not data:
        return result
    result["card"] = {
        "id": _card_id("milk-analysis", data.get("window")),
        "card_type": "milk_analysis_card",
        "schema_version": "1.0",
        "card_json": _build_milk_analysis_card_json(data),
    }
    result["assistant_followup"] = {"message": _milk_analysis_followup_message(data)}
    return result


def _milk_assessment_arguments(arguments: dict[str, Any], *, render_card: bool) -> dict[str, Any]:
    assessment_arguments = _pick(arguments, "user_id", "as_of_time", "window_days", "include_today")
    if render_card:
        assessment_arguments["window_days"] = 7
        assessment_arguments["include_today"] = False
    return assessment_arguments


def _with_milk_plan_card(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    if not result.get("ok") or not draft:
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


def _should_render_milk_analysis_card(arguments: dict[str, Any], inputs: RuntimeInputs) -> bool:
    explicit = arguments.get("render_card")
    if isinstance(explicit, bool):
        return explicit
    if isinstance(explicit, str):
        normalized = explicit.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False

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


def _preview_plan(arguments: dict[str, Any]) -> dict[str, Any]:
    plan_type = arguments.get("plan_type")
    target_daily_ml = arguments.get("target_daily_ml")
    if target_daily_ml is None:
        target_daily_ml = arguments.get("custom_target_daily_ml")
    delta_ml = arguments.get("delta_ml")
    source_plan_id = arguments.get("source_plan_id") or arguments.get("plan_id")
    options = _normalized_options(arguments.get("options"))

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
                "data": {"missing_fields": ["plan_type"]},
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
    return preview


def _clinical_gate_for_plan(arguments: dict[str, Any], *, plan_type: Any, options: dict[str, Any]) -> dict[str, Any] | None:
    clinical = evaluate_lactation_clinical_status(
        user_id=arguments["user_id"],
        as_of_time=arguments.get("as_of_time"),
        window_days=_to_int(arguments.get("window_days"), 1),
        include_today=False,
        milk_assessment=options.get("prepared_assessment") if isinstance(options.get("prepared_assessment"), dict) else None,
        growth_assessment=options.get("prepared_growth_assessment") if isinstance(options.get("prepared_growth_assessment"), dict) else None,
        maternal_symptoms=options.get("maternal_symptoms") if isinstance(options.get("maternal_symptoms"), dict) else {},
        infant_signals=options.get("infant_signals") if isinstance(options.get("infant_signals"), dict) else {},
        requested_plan_type=str(plan_type or ""),
    )
    clinical_data = clinical.get("data") if isinstance(clinical.get("data"), dict) else {}
    plan_gate = clinical_data.get("plan_gate") if isinstance(clinical_data.get("plan_gate"), dict) else {}
    if plan_gate.get("allowed") is False:
        return {
            "ok": False,
            "status": "milk_plan_clinical_gate_blocked",
            "summary": str(plan_gate.get("reason") or clinical.get("summary") or "当前不适合直接生成奶量计划。"),
            "data": {
                "clinical_assessment": _compact_clinical_data(clinical_data),
                "requires_confirmation": False,
            },
            "assistant_followup": {"message": _milk_plan_gate_followup_message(clinical_data)},
        }
    options.setdefault("prepared_assessment", clinical_data.get("milk_assessment"))
    options.setdefault("prepared_growth_assessment", clinical_data.get("growth_assessment"))
    return None


def _clinical_gate_for_analysis(arguments: dict[str, Any], *, result: dict[str, Any], render_card: bool) -> dict[str, Any] | None:
    if not render_card:
        return None
    clinical = _clinical_assessment_for_result(arguments, result=result, requested_plan_type="")
    clinical_data = clinical.get("data") if isinstance(clinical.get("data"), dict) else {}
    if not _has_minimal_clinical_context(clinical_data):
        return {
            "ok": True,
            "status": "needs_clinical_context",
            "summary": "还需要先确认宝宝近 24 小时状态和妈妈乳房情况，再继续分析吸奶和奶量。",
            "data": {
                "clinical_assessment": _compact_clinical_data(clinical_data),
                "workflow_intent": "milk_analysis",
                "continuation_instruction": (
                    "当前仍处于奶量/吸奶分析流程。下一轮用户回复通常是在补充这些判断信息，"
                    "不要因为出现“疼、红肿、硬块、发热”等词就切换成独立健康咨询或直接调用 web_search。"
                    "如果没有明显红旗信号，应把用户回答提炼进 infant_signals / maternal_symptoms，"
                    "然后继续调用 milk_assessment_evaluate 完成原本的奶量分析。"
                ),
                "missing_fields": ["infant_signals", "maternal_symptoms"],
                "suggested_questions": [
                    "宝宝近 24 小时尿布和精神状态怎么样？",
                    "你现在有没有发热、乳房明显红肿、硬块，或疼痛越来越重？",
                ],
            },
            "assistant_followup": {
                "message": (
                    "我还想先确认几件会影响判断的信息，这样不会只盯着奶量数字看。\n\n"
                    "宝宝近 24 小时尿布和精神状态怎么样？你现在有没有发热、乳房明显红肿、硬块，或疼痛越来越重？"
                )
            },
        }
    risk_level = str(clinical_data.get("risk_level") or "").strip()
    if risk_level in {"medical_recommended", "urgent"}:
        return {
            "ok": True,
            "status": "analysis_medical_gate_blocked",
            "summary": "当前有需要优先医学评估的信号，先不要只看奶量数据下结论。",
            "data": {"clinical_assessment": _compact_clinical_data(clinical_data)},
            "assistant_followup": {"message": "这次我们先不只看奶量数字。\n\n你描述的情况更适合先联系医生或线下医疗渠道确认；等身体这边稳住了，我再陪你继续看奶量和计划。"},
        }
    if risk_level == "ibclc_recommended":
        return {
            "ok": True,
            "status": "analysis_ibclc_gate_blocked",
            "summary": "当前更适合先结合 IBCLC 看含乳、移乳效率或乳房不适。",
            "data": {"clinical_assessment": _compact_clinical_data(clinical_data)},
            "assistant_followup": {"message": "这次不只是看奶量数字就能说清楚。\n\n更适合把含乳、排乳和乳房不适一起看一遍；如果你愿意，我可以帮你打开 IBCLC 咨询入口，让顾问一起看。"},
        }
    if str(clinical_data.get("data_confidence") or "").strip() == "low":
        return {
            "ok": True,
            "status": "needs_more_records_for_analysis",
            "summary": "当前关键记录不足，先补充记录后再做奶量分析。",
            "data": {"clinical_assessment": _compact_clinical_data(clinical_data)},
            "assistant_followup": {"message": "现在记录还不够完整，先不用急着下结论。\n\n你可以先补一下最近的吸奶、亲喂或瓶喂记录；补完后我再帮你重新分析，会更接近真实情况。"},
        }
    return None


def _attach_clinical_assessment(result: dict[str, Any], arguments: dict[str, Any]) -> None:
    clinical = _clinical_assessment_for_result(arguments, result=result, requested_plan_type="")
    clinical_data = clinical.get("data") if isinstance(clinical.get("data"), dict) else {}
    data = result.get("data")
    if isinstance(data, dict):
        data["clinical_assessment"] = _compact_clinical_data(clinical_data)


def _clinical_assessment_for_result(arguments: dict[str, Any], *, result: dict[str, Any], requested_plan_type: str) -> dict[str, Any]:
    return dict(
        evaluate_lactation_clinical_status(
            user_id=arguments["user_id"],
            as_of_time=arguments.get("as_of_time"),
            window_days=_to_int(arguments.get("window_days"), 7),
            include_today=bool(arguments.get("include_today")),
            milk_assessment=result,
            maternal_symptoms=arguments.get("maternal_symptoms") if isinstance(arguments.get("maternal_symptoms"), dict) else {},
            infant_signals=arguments.get("infant_signals") if isinstance(arguments.get("infant_signals"), dict) else {},
            requested_plan_type=requested_plan_type,
        )
    )


def _has_minimal_clinical_context(clinical_data: dict[str, Any]) -> bool:
    domains = clinical_data.get("domains") if isinstance(clinical_data.get("domains"), dict) else {}
    infant = domains.get("infant_intake") if isinstance(domains.get("infant_intake"), dict) else {}
    maternal = domains.get("maternal_breast_symptoms") if isinstance(domains.get("maternal_breast_symptoms"), dict) else {}
    infant_known = str(infant.get("status") or "").strip() != "unknown"
    maternal_known = str(maternal.get("status") or "").strip() != "unknown"
    return infant_known or maternal_known


def _compact_clinical_data(clinical_data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: clinical_data[key]
        for key in ("risk_level", "data_confidence", "domains", "risk_reasons", "plan_gate", "next_actions", "evidence")
        if key in clinical_data
    }


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
                "id": "next",
                "title": "下一步",
                "tone": "default",
                "items": [_milk_next_step(status)],
            },
        ],
    }


def _build_milk_plan_card_json(data: dict[str, Any]) -> dict[str, Any]:
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    rules = draft.get("plan_rules") if isinstance(draft.get("plan_rules"), dict) else {}
    observation = draft.get("observation_context") if isinstance(draft.get("observation_context"), dict) else {}
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
                        "label": "原方案",
                        "value": f"{_format_number(_to_float(observation.get('calendar_pump_tasks_per_day'), 0.0))} 次/天",
                        "detail": "吸奶任务",
                    },
                    {
                        "label": "新方案",
                        "value": f"{planned_count} 次/天",
                        "detail": "吸奶任务",
                    },
                ],
                "items": [
                    "先按现有节奏微调，不一下子大改。",
                    f"保留原有 {current_count} 个吸奶提醒，新增/强化 {added} 个关键时段。",
                    "具体时间表先不全部展开，需要时再展开具体时段。",
                ],
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


def _milk_analysis_followup_message(data: dict[str, Any]) -> str:
    normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    status = str(normality.get("overall_status") or data.get("assessment_status") or "").strip()
    if status == "under_supply_alert":
        return (
            "我把近期奶量情况看完了。先别急着责怪自己，奶量波动很常见，我们先把记录和节奏一项项顺清楚。\n\n"
            "如果这些记录已经完整，我可以接着陪你做一份温和追奶计划；如果你担心有漏记，我们先补齐，再判断会更踏实。"
        )
    if status == "over_supply_alert":
        return (
            "我把近期奶量情况看完了。先不用急着一下子减吸，身体通常更吃温和、慢一点的调整。\n\n"
            "接下来我们可以先把节奏调得舒服些；如果你有胀痛、硬块或不舒服，也可以先从这里看。"
        )
    if status == "normal":
        return (
            "我把近期奶量情况看完了。现在这个节奏整体撑得住，可以先松一口气。\n\n"
            "接下来更适合稳住，不用大改；如果你愿意，我也可以陪你做一份稳奶计划。"
        )
    return (
        "我先帮你看了一遍近期奶量情况。现在还差一点关键信息，我们先不急着下结论。\n\n"
        "你把关键记录补一下，我再陪你继续判断要不要做计划。"
    )


def _milk_plan_preview_followup_message(data: dict[str, Any]) -> str:
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    title = _milk_plan_title(draft)
    session_guidance = _milk_plan_session_guidance(draft)
    calendar_delta = data.get("calendar_delta") if isinstance(data.get("calendar_delta"), dict) else {}
    requires_strategy = bool(calendar_delta.get("requires_calendar_write_strategy") or calendar_delta.get("calendar_write_strategy_required"))
    if requires_strategy:
        return (
            f"{title}我先帮你理好了。它不是让你硬扛，而是帮你把接下来几天变得更有把握一点。\n\n"
            f"{session_guidance}\n\n"
            "如果要同步到计划页，我先和你确认一下：是追加到现有日程，还是替换未来未完成的旧计划任务？"
        )
    return (
        f"{title}我先帮你理好了。它不是让你硬扛，而是帮你把接下来几天变得更有把握一点。\n\n"
        f"{session_guidance}\n\n"
        "如果方向没问题，我可以帮你同步到计划页；也可以先照着你的作息，把时间再调顺一点。"
    )


def _milk_plan_session_guidance(draft: dict[str, Any]) -> str:
    plan_type = str(draft.get("plan_type") or "").strip()
    if plan_type == "increase_milk":
        return "单次吸奶或亲喂的重点是有效移出，不是把自己耗到很久；吸奶到奶流明显变慢后再多 1-2 分钟就够了，亲喂就看吞咽变少和宝宝状态。不舒服时先停下来，我们优先调吸力、法兰或含乳。"
    if plan_type == "decrease_milk":
        return "单次吸奶或亲喂不用刻意排得特别空，重点是让身体舒服下来；如果只是胀，吸到不难受就可以停，不要继续给身体太强的增奶信号。"
    return "单次吸奶或亲喂不用硬拖很久，重点是稳定、舒服地移出；吸奶到奶流明显变慢、乳房舒服一些就可以，亲喂就看宝宝吞咽和满足感。"


def _milk_plan_saved_followup_message(result: dict[str, Any]) -> str:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    inserted = _to_int(data.get("inserted_calendar_count"), 0)
    if inserted > 0:
        return (
            "已经把计划同步到了日程，接下来会定时提醒。你不用一直靠脑子记着，我们先让提醒帮你托住节奏。\n\n"
            "先按照这个计划执行一段时间就好，后面我再陪你看奶量、宝宝状态和你的作息，一起慢慢调整。"
        )
    return (
        "计划已经保存好了。你不用一下子把后面每一步都想清楚，我们先把方向稳住。\n\n"
        "如果这几天有会议、外出、旅行、上班或想多睡一段，我也可以继续陪你把时间顺一下。"
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
        result = dict(
            apply_milk_plan(
                user_id=arguments["user_id"],
                confirmed_plan=plan,
                idempotency_key=arguments["idempotency_key"],
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


def _query_calendar(arguments: dict[str, Any]) -> dict[str, Any]:
    query_mode = str(arguments.get("query_mode") or "range").strip()
    if query_mode == "today_overview":
        return dict(get_today_overview(**_pick(arguments, "user_id", "target_date", "plan_id")))
    if query_mode == "today_summary":
        return dict(get_today_summary(**_pick(arguments, "user_id", "target_date", "plan_id")))
    return dict(
        get_calendar_range(
            user_id=arguments["user_id"],
            start_at=arguments.get("start_at") or arguments.get("target_date"),
            end_at=arguments.get("end_at") or arguments.get("target_date"),
            plan_id=arguments.get("plan_id"),
            item_type=arguments.get("item_type"),
            include_items=bool(arguments.get("include_items")),
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
    if arguments.get("user_id"):
        return arguments
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
        return "最近整体偏低。你这几天吸奶节奏其实保持得很稳，先别给自己压力；更可能和单次排空、夜间或清晨间隔、亲喂估算或记录完整度有关。"
    if status == "over_supply_alert":
        return "最近整体偏高。先不用紧张，也不要突然减吸；更可能和最近刺激变多、单次排空更充分、间隔变化，或记录口径有关。"
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
        return "这几天有一点往下走的迹象，先看看有没有漏记、休息不足，或者每次没有排得很空。"
    if last > first * 1.1:
        return "这几天有一点往上走的迹象，可以先观察能不能稳定住。"
    return "没有看到明显一路下降或上升，整体只是有些小波动。"


def _milk_next_step(status: str) -> str:
    if status == "under_supply_alert":
        return "先确认这几天有没有没记进来的吸奶、手挤、其他吸奶器或线下记录；如果记录已经完整，再从明天开始做温和追奶计划。"
    if status == "over_supply_alert":
        return "先别继续加吸或延长时间，重点观察胀痛、硬块和宝宝实际摄入。"
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
