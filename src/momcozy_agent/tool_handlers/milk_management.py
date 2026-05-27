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
        result = dict(evaluate_milk_status(**_pick(arguments, "user_id", "as_of_time", "window_days", "include_today")))
        if _should_render_milk_analysis_card(arguments, inputs):
            return _with_milk_analysis_card(result)
        return result
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
    return result


def _with_milk_plan_card(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    if not result.get("ok") or not draft:
        return result
    result["card"] = {
        "id": _card_id("milk-plan", {"start_at": draft.get("plan_type"), "end_at": draft.get("plan_days")}),
        "card_type": "milk_plan_card",
        "schema_version": "1.0",
        "card_json": _build_milk_plan_card_json(data),
    }
    return result


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
            }
        if target_daily_ml is None and validation_data.get("target_daily_ml") is not None:
            target_daily_ml = validation_data.get("target_daily_ml")

    if source_plan_id is not None:
        preview = dict(
            regenerate_milk_plan_preview(
                user_id=arguments["user_id"],
                plan_id=source_plan_id,
                plan_type=plan_type,
                plan_days=arguments.get("plan_days"),
                custom_target_daily_ml=target_daily_ml,
                as_of_time=arguments.get("as_of_time"),
                options=arguments.get("options"),
            )
        )
    else:
        if plan_type is None:
            return {
                "ok": False,
                "status": "milk_plan_preview_missing_plan_type",
                "summary": "缺少 plan_type，无法生成新的奶量计划草稿。",
                "data": {"missing_fields": ["plan_type"]},
            }
        preview = dict(
            preview_milk_plan(
                user_id=arguments["user_id"],
                plan_type=plan_type,
                plan_days=arguments.get("plan_days"),
                custom_target_daily_ml=target_daily_ml,
                as_of_time=arguments.get("as_of_time"),
                options=arguments.get("options"),
            )
        )

    if target_validation:
        data = preview.setdefault("data", {})
        if isinstance(data, dict):
            data["target_validation"] = target_validation.get("data", {})
    return preview


def _build_milk_analysis_card_json(data: dict[str, Any]) -> dict[str, Any]:
    window = data.get("window") if isinstance(data.get("window"), dict) else {}
    pumping = data.get("pumping_summary") if isinstance(data.get("pumping_summary"), dict) else {}
    feeding = data.get("feeding_summary") if isinstance(data.get("feeding_summary"), dict) else {}
    calendar = data.get("calendar_task_summary") if isinstance(data.get("calendar_task_summary"), dict) else {}
    normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    window_days = max(_to_int(window.get("window_days"), _to_int(calendar.get("days"), 1)), 1)
    pumping_count = _to_int(pumping.get("count"), 0)
    feeding_count = _to_int(feeding.get("count"), 0)
    records_total = pumping_count + feeding_count
    daily_records = _format_number(records_total / window_days) if records_total else "0"
    daily_pumping_records = _format_number(pumping_count / window_days) if pumping_count else "0"
    feed_type_counts = feeding.get("type_counts") if isinstance(feeding.get("type_counts"), dict) else {}
    feed_parts = _feeding_parts(feed_type_counts, window_days=window_days)
    record_parts = "、".join(part for part in (f"{daily_pumping_records} 条吸奶", feed_parts) if part)
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
                    {"label": "记录与补录", "value": f"{daily_records} 条/天", "detail": record_parts or "吸奶、亲喂、瓶喂"},
                    {"label": "实测吸奶", "value": f"{_format_number(daily_measured_ml)} ml/天", "detail": f"近 {window_days} 天共 {_format_number(total_ml)} ml"},
                    {"label": "含亲喂估算", "value": estimated_range or "—", "detail": "亲喂部分为估算"},
                    {"label": "参考区间", "value": reference_range or "—", "detail": "同阶段 P15-P85"},
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
    calendar_delta = data.get("calendar_delta") if isinstance(data.get("calendar_delta"), dict) else {}
    date_range = calendar_delta.get("date_range") if isinstance(calendar_delta.get("date_range"), dict) else {}
    plan_days = _to_int(draft.get("plan_days"), 0)
    plan_name = str(draft.get("plan_name") or "奶量计划草稿").strip()
    target_daily_ml = _to_float(draft.get("target_daily_ml"), 0.0)
    current_daily_ml = _to_float(draft.get("current_daily_ml"), 0.0)
    delta_ml = max(target_daily_ml - current_daily_ml, 0.0)
    desired_count = _to_int(rules.get("desired_pumping_count"), 0)
    current_count = _to_int(rules.get("current_pumping_count"), 0)
    added = max(desired_count - current_count, 0)
    inserted_count = _to_int(calendar_delta.get("draft_calendar_task_count"), 0)
    start_date = str(date_range.get("start_date") or "").strip()
    end_date = str(date_range.get("end_date") or "").strip()

    return {
        "title": plan_name,
        "subtitle": "草稿 · 待确认后同步到日历",
        "status": "preview",
        "status_label": "待确认",
        "status_tone": "attention",
        "headline": f"先做一个从明天开始的 {plan_days} 天草稿，不直接改今天的安排。",
        "sections": [
            {
                "id": "direction",
                "title": "计划方向",
                "tone": "info",
                "metrics": [
                    {"label": "周期", "value": f"{plan_days} 天", "detail": "从明天开始"},
                    {
                        "label": "原节奏",
                        "value": f"{_format_number(_to_float(observation.get('calendar_pump_tasks_per_day'), 0.0))} 个/天",
                        "detail": "日历吸奶提醒",
                    },
                    {
                        "label": "实测记录",
                        "value": f"{_format_number(_to_float(observation.get('recorded_pumping_logs_per_day'), 0.0))} 条/天",
                        "detail": "带奶量吸奶记录",
                    },
                ],
                "items": ["先按现有节奏微调，不一下子大改。"],
            },
            {
                "id": "target",
                "title": "目标",
                "tone": "normal",
                "items": [
                    f"当前参考日奶量约 {_format_number(current_daily_ml)} ml，目标约 {_format_number(target_daily_ml)} ml。",
                    f"这轮先温和增加约 {_format_number(delta_ml)} ml/天。",
                ],
            },
            {
                "id": "arrangement",
                "title": "安排",
                "tone": "default",
                "items": [
                    f"保留原有 {current_count} 个吸奶提醒，新增/强化 {added} 个关键时段。",
                    "具体时间表先不全部展开，确认后再写入日历。",
                ],
            },
            {
                "id": "calendar",
                "title": "同步到日历",
                "tone": "attention",
                "items": [
                    f"确认后会影响 {start_date or '明天'} 到 {end_date or '计划结束日'} 的提醒。",
                    f"预计写入 {inserted_count} 个计划任务；保存前仍需要你确认。",
                ],
            },
        ],
    }


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
        return "低于参考", "attention"
    if status == "over_supply_alert":
        return "高于参考", "attention"
    if status == "normal":
        return "整体正常", "normal"
    return "需补记录", "insufficient"


def _milk_analysis_headline(status: str) -> str:
    if status == "under_supply_alert":
        return "你最近吸奶节奏很稳定哦～不过从总量看，最近整体还是低于参考标准，需要稍微关注一下。"
    if status == "over_supply_alert":
        return "最近总量偏高一些，先别急着加量，我们重点看舒适度和是否有胀痛。"
    if status == "normal":
        return "最近整体在参考范围内，先保持现在能坚持的节奏就好。"
    return "目前记录还不够完整，先把关键记录补齐，再判断会更稳。"


def _milk_trend_text(days: list[dict[str, Any]]) -> str:
    values = [_to_float(item.get("estimated_daily_milk_ml"), 0.0) for item in days if item.get("estimated_daily_milk_ml") is not None]
    if len(values) < 2:
        return "暂时还看不出完整趋势。"
    first = values[0]
    last = values[-1]
    if last < first * 0.9:
        return "趋势上有下降信号，建议先看最近几天是否漏记、睡眠和排空情况。"
    if last > first * 1.1:
        return "趋势上有回升信号，可以继续观察是否能稳定住。"
    return "没有看到明显一路下滑，更像是低位稳定、有小幅波动。"


def _milk_next_step(status: str) -> str:
    if status == "under_supply_alert":
        return "今天先不要猛加量；如果你愿意，可以做一个从明天开始的 7 天温和追奶草稿。"
    if status == "over_supply_alert":
        return "先别继续加刺激，重点观察胀痛、硬块和宝宝实际摄入。"
    if status == "normal":
        return "继续保持记录，第 3 天再复盘一次总量和宝宝状态。"
    return "先补一两天关键记录，尤其是吸奶量、亲喂和宝宝尿布/体重信号。"


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
