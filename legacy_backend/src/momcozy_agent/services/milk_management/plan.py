from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from .assessment import evaluate_milk_status
from .db import fetch_all, fetch_one, transaction
from .growth import evaluate_infant_growth
from .rhythm import build_recent_milk_rhythm_context
from .schemas import (
    CALENDAR_TYPE_NURSING,
    CALENDAR_SOURCE_SYSTEM,
    CALENDAR_TYPE_PUMP,
    PLAN_TYPE_DECREASE,
    PLAN_TYPE_INCREASE,
    PLAN_TYPE_MAINTAIN,
    ServiceResult,
    error_result,
    norm_text,
    ok_result,
    parse_datetime,
    to_bool,
    to_int,
)

SUPPORTED_PLAN_TYPES = {PLAN_TYPE_INCREASE, PLAN_TYPE_MAINTAIN, PLAN_TYPE_DECREASE}
INCREASE_MIN_DAILY_DELTA_ML = 50.0
INCREASE_SMALL_GAP_LIMIT_ML = 300.0
INCREASE_LOW_FREQUENCY_TARGET = 8
INCREASE_HIGHER_FREQUENCY_TARGET = 10
INCREASE_REGULAR_PUMP_MINUTES = 15
INCREASE_PP_MINUTES = 60
INCREASE_DEFAULT_PLAN_DAYS = 3
DECREASE_MIN_DAILY_DELTA_ML = 50.0
DECREASE_DEFAULT_DAILY_DELTA_ML = 80.0
DECREASE_MAX_PLAN_DAYS = 28
DAYTIME_START_MINUTE = 6 * 60
DAYTIME_END_MINUTE = 22 * 60
DAYTIME_STEP_MINUTES = 15
PLAN_MIN_PUMP_GAP_MINUTES = 60
MAX_NIGHT_PUMP_TASKS = 1
CALENDAR_WRITE_STRATEGY_APPEND = "append"
CALENDAR_WRITE_STRATEGY_REPLACE_FUTURE_PLAN_TASKS = "replace_future_plan_tasks"
SUPPORTED_CALENDAR_WRITE_STRATEGIES = {
    CALENDAR_WRITE_STRATEGY_APPEND,
    CALENDAR_WRITE_STRATEGY_REPLACE_FUTURE_PLAN_TASKS,
}


def preview_milk_plan(
    *,
    user_id: str,
    plan_type: str,
    plan_days: int | None = None,
    custom_target_daily_ml: float | None = None,
    as_of_time: str | None = None,
    options: dict[str, Any] | str | None = None,
) -> ServiceResult:
    """Generate a conservative plan draft without writing storage."""

    uid = norm_text(user_id)
    normalized_type = _normalize_plan_type(plan_type)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法生成计划草稿。")
    if normalized_type not in SUPPORTED_PLAN_TYPES:
        return error_result("unsupported_plan_type", "目前只支持追奶、稳奶或减奶计划。")

    parsed_options = _parse_options(options)
    prepared_assessment = _prepared_data_from_options(
        parsed_options,
        "prepared_assessment",
        "prepared_assessment_data",
        "assessment",
        "assessment_data",
    )
    prepared_from_context = to_bool(parsed_options.get("_prepared_assessment_from_context"))
    if prepared_assessment and (not prepared_from_context or _assessment_window_days(prepared_assessment) >= 7):
        assessment_data = prepared_assessment
    else:
        assessment = _evaluate_plan_window(uid, as_of_time=as_of_time)
        evaluated_data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
        assessment_data = evaluated_data if _assessment_has_milk_basis(evaluated_data) else prepared_assessment or evaluated_data

    prepared_growth = _prepared_data_from_options(
        parsed_options,
        "prepared_growth_assessment",
        "prepared_growth_assessment_data",
        "growth_assessment",
        "growth_data",
    )
    if prepared_growth:
        growth_data = prepared_growth
    else:
        growth = _evaluate_growth_for_plan(uid, as_of_time=as_of_time)
        growth_data = growth.get("data") if isinstance(growth.get("data"), dict) else {}
    eligibility = evaluate_plan_eligibility(
        milk_assessment=assessment_data,
        growth_assessment=growth_data,
        requested_plan_type=normalized_type,
        observed_persistent_abnormal=to_bool(
            parsed_options.get("observed_persistent_abnormal")
            or parsed_options.get("persistent_abnormal_confirmed")
        ),
    )
    if not bool(eligibility.get("eligible")):
        return ok_result(
            "plan_preview_not_recommended",
            norm_text(eligibility.get("message")) or "当前暂不建议生成泌乳计划。",
            {
                "requires_confirmation": False,
                "eligibility": eligibility,
                "assessment": assessment_data,
                "growth_assessment": growth_data,
                "options": parsed_options,
            },
        )

    suggested_type = _normalize_plan_type(eligibility.get("suggested_plan_type"))
    if suggested_type in SUPPORTED_PLAN_TYPES and suggested_type != normalized_type:
        normalized_type = suggested_type

    pumping_summary = assessment_data.get("pumping_summary") if isinstance(assessment_data.get("pumping_summary"), dict) else {}
    feeding_summary = assessment_data.get("feeding_summary") if isinstance(assessment_data.get("feeding_summary"), dict) else {}
    calendar_task_summary = (
        assessment_data.get("calendar_task_summary")
        if isinstance(assessment_data.get("calendar_task_summary"), dict)
        else {}
    )
    milk_normality = assessment_data.get("milk_normality") if isinstance(assessment_data.get("milk_normality"), dict) else {}
    window = assessment_data.get("window") if isinstance(assessment_data.get("window"), dict) else {}
    window_days = max(to_int(window.get("window_days"), 7), 1)

    plan_context = _collect_recent_plan_context(uid, as_of_time=as_of_time)
    recorded_pumping_count = _daily_event_count(pumping_summary, window_days=window_days)
    calendar_pumping_count = max(
        _daily_calendar_pump_task_count(calendar_task_summary, window_days=window_days),
        len(_list_from_context(plan_context, "calendar_pump_times")),
    )
    schedule_basis = _schedule_basis_from_plan_context(
        plan_context,
        pumping_summary=pumping_summary,
        feeding_summary=feeding_summary,
        window_days=window_days,
    )
    plan_context["schedule_basis"] = schedule_basis
    pumping_count = max(recorded_pumping_count, calendar_pumping_count)
    current_frequency = max(
        _current_frequency(pumping_summary, feeding_summary, window_days=window_days),
        pumping_count + _daily_direct_breastfeeding_count(feeding_summary, window_days=window_days),
    )
    days = _resolve_plan_days(
        plan_type=normalized_type,
        requested_days=plan_days,
        current_frequency=current_frequency,
        current_pumping_count=pumping_count,
        infant_age_months=plan_context.get("infant_age_months"),
    )
    current_daily_ml = _current_daily_ml(pumping_summary, window_days=window_days, milk_normality=milk_normality)
    if current_daily_ml <= 0:
        return ok_result(
            "milk_plan_needs_milk_records",
            "过去 7 天还没有可用于计算计划目标的有效奶量数据，请先补充吸奶奶量、瓶喂奶量，或可估算亲喂量的瓶喂参考。",
            {
                "requires_confirmation": False,
                "missing_fields": ["recent_milk_volume"],
                "suggested_questions": [
                    "过去 7 天每天大约吸奶多少 ml？如果有亲喂，也请补充瓶喂或补奶量，方便估算亲喂量。"
                ],
                "workflow_intent": "milk_plan_preview_create",
                "milk_flow_decision": {
                    "stage": "need_more_record_context",
                    "missing_user_inputs": ["recent_milk_volume"],
                    "plan_decision": {
                        "can_start_plan": False,
                        "recommended_plan_type": normalized_type,
                        "reason_for_user": "缺少过去 7 天可用于计算计划目标的有效奶量数据。",
                        "next_tool": "milk_plan_preview_create",
                    },
                },
                "assessment": assessment_data,
                "growth_assessment": growth_data,
                "options": parsed_options,
            },
        )
    preview_target_daily_ml = _target_daily_ml_from_options(
        plan_type=normalized_type,
        current_daily_ml=current_daily_ml,
        custom_target_daily_ml=custom_target_daily_ml,
        options=parsed_options,
    )
    target_daily_ml = _target_daily_ml(
        plan_type=normalized_type,
        current_daily_ml=current_daily_ml,
        custom_target_daily_ml=preview_target_daily_ml,
        milk_normality=milk_normality,
    )
    plan_rules = _plan_rules(
        plan_type=normalized_type,
        current_daily_ml=current_daily_ml,
        target_daily_ml=target_daily_ml,
        current_frequency=current_frequency,
        pumping_count=pumping_count,
        milk_normality=milk_normality,
        infant_age_months=plan_context.get("infant_age_months"),
    )
    maternal_symptoms = _maternal_symptoms_from_options(parsed_options)
    if _has_breast_fullness_without_red_flags(maternal_symptoms):
        plan_rules["breast_fullness_without_red_flags"] = True
    if (
        normalized_type == PLAN_TYPE_DECREASE
        and bool(plan_rules.get("medical_confirmation_required"))
        and not to_bool(parsed_options.get("medical_confirmation_confirmed"))
    ):
        return ok_result(
            "plan_preview_needs_medical_confirmation",
            "当前总频次较高，开始减奶前建议先确认已排除高泌乳素血症等病理因素。",
            {
                "requires_confirmation": True,
                "requires_medical_confirmation": True,
                "confirmation_question": "是否已经排除高泌乳素血症等病理因素，并仍希望继续制定减奶计划？",
                "strategy_interval_days": plan_rules.get("strategy_interval_days"),
                "strategy_summary": "确认后按每7天减少1次吸奶推进；每次不排空，胀痛时少量排出缓解并冷敷。",
                "assessment": assessment_data,
                "options": parsed_options,
            },
        )
    schedule_items = _build_plan_schedule_items(
        plan_type=normalized_type,
        plan_context=plan_context,
        pumping_count=pumping_count,
        current_frequency=current_frequency,
        current_daily_ml=current_daily_ml,
        target_daily_ml=target_daily_ml,
        desired_count=to_int(plan_rules.get("desired_pumping_count"), 0),
        require_pp=bool(plan_rules.get("require_pp")),
    )
    if normalized_type == PLAN_TYPE_DECREASE:
        plan_rules["stage_targets"] = _build_decrease_stage_targets(
            current_pumping_count=max(len(schedule_items), pumping_count),
            plan_days=days,
            interval_days=to_int(plan_rules.get("strategy_interval_days"), 7),
        )
    schedule_templates = _daily_schedule_templates(normalized_type, days, schedule_items, plan_rules)
    title = _plan_title(normalized_type)
    rule_notes = _plan_rule_notes(normalized_type, plan_rules)
    control_strategy = _plan_control_strategy(normalized_type, plan_rules)
    draft = {
        "plan_type": normalized_type,
        "plan_name": f"{title}{days}天",
        "plan_days": days,
        "summary": _plan_summary(normalized_type, current_daily_ml, target_daily_ml, days),
        "current_daily_ml": current_daily_ml,
        "target_daily_ml": target_daily_ml,
        "current_frequency": current_frequency,
        "schedule_basis": schedule_basis,
        "observation_context": _plan_observation_context(
            pumping_summary=pumping_summary,
            feeding_summary=feeding_summary,
            calendar_task_summary=calendar_task_summary,
            plan_context=plan_context,
            window_days=window_days,
        ),
        "milk_status": milk_normality.get("overall_status"),
        "plan_rules": plan_rules,
        "generation_context": _public_plan_generation_context(plan_context),
        "daily_targets": _daily_targets(days, current_daily_ml, target_daily_ml),
        "checkpoint_days": _checkpoint_days(days, plan_rules),
        "daily_schedule_template": {
            "items": schedule_templates[0]["items"] if schedule_templates else schedule_items,
        },
        "daily_schedule_templates": schedule_templates,
        "control_strategy": control_strategy,
        "rule_notes": rule_notes,
        "advice": rule_notes,
        "review_note": _review_note(normalized_type),
        "repeat_note": _repeat_note(normalized_type, days, plan_rules),
        "watch_items": _watch_items(normalized_type),
        "eligibility": eligibility,
        "medical_disclaimer": "本计划仅供家庭喂养管理参考，不能替代医生、儿科医生或 IBCLC 的专业建议。",
        "source": "service_preview_v1",
    }
    calendar_delta = _calendar_write_delta(user_id=uid, plan=draft)
    preview_validation = _preview_validation_data(user_id=uid, draft=draft)
    if preview_validation.get("valid") is not True:
        violations = preview_validation.get("violations") if isinstance(preview_validation.get("violations"), list) else []
        return ok_result(
            "plan_preview_needs_revision",
            str(violations[0]) if violations else "计划草稿校验未通过。",
            {
                "requires_confirmation": False,
                "draft": draft,
                "validation": preview_validation,
                "calendar_delta": calendar_delta,
                "assessment": assessment_data,
                "growth_assessment": growth_data,
                "eligibility": eligibility,
                "options": parsed_options,
            },
        )
    return ok_result(
        "plan_preview_ready",
        draft["summary"],
        {
            "requires_confirmation": True,
            "draft": draft,
            "validation": preview_validation,
            "calendar_delta": calendar_delta,
            "assessment": assessment_data,
            "growth_assessment": growth_data,
            "eligibility": eligibility,
            "options": parsed_options,
        },
    )


def apply_milk_plan(
    *,
    user_id: str,
    confirmed_plan: dict[str, Any] | str,
    idempotency_key: str,
    calendar_write_strategy: str | None = None,
) -> ServiceResult:
    """Persist a confirmed plan to `milk_plan` and expand it into `calendar`."""

    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法保存计划。")
    if not norm_text(idempotency_key):
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")

    plan = _normalize_confirmed_plan(confirmed_plan)
    if not plan:
        return error_result("invalid_confirmed_plan", "confirmed_plan 不是有效计划。")

    plan_type = _normalize_plan_type(plan.get("plan_type"))
    if plan_type not in SUPPORTED_PLAN_TYPES:
        return error_result("unsupported_plan_type", "目前只支持追奶、稳奶或减奶计划。")
    plan_days = max(to_int(plan.get("plan_days"), 1), 1)
    plan_name = norm_text(plan.get("plan_name")) or f"{_plan_title(plan_type)}{plan_days}天"
    summary = norm_text(plan.get("summary")) or "已保存奶量计划。"
    strategy_input = norm_text(calendar_write_strategy)
    strategy = _normalize_calendar_write_strategy(strategy_input)
    if strategy_input and not strategy:
        return error_result(
            "invalid_calendar_write_strategy",
            "日程写入方式无效，请选择追加或替换未来未完成计划任务。",
            data={"allowed_strategies": sorted(SUPPORTED_CALENDAR_WRITE_STRATEGIES)},
        )
    if not strategy:
        strategy = CALENDAR_WRITE_STRATEGY_APPEND

    calendar_delta = _calendar_write_delta(
        user_id=uid,
        plan=plan,
        calendar_write_strategy=strategy if strategy_input else None,
    )
    if calendar_delta.get("requires_calendar_write_strategy") and not strategy_input:
        return error_result(
            "calendar_write_strategy_required",
            "当前未来日程已有奶量计划任务，请先确认是追加到现有日程，还是替换未来未完成计划任务。",
            data={"calendar_delta": calendar_delta, "allowed_strategies": sorted(SUPPORTED_CALENDAR_WRITE_STRATEGIES)},
        )
    payload = {
        "ok": True,
        "plan_generated": True,
        "plan_type": plan_type,
        "summary": summary,
        "plan": plan,
        "idempotency_key": norm_text(idempotency_key),
        "calendar_write_strategy": strategy,
    }

    replaced_calendar_count = 0
    with transaction() as conn:
        existing = conn.execute(
            """
            SELECT plan_id
            FROM milk_plan
            WHERE user_id = ?
              AND plan_payload_json LIKE ?
            ORDER BY plan_id DESC
            LIMIT 1
            """,
            (uid, f'%"idempotency_key": "{norm_text(idempotency_key)}"%'),
        ).fetchone()
        if existing is not None:
            plan_id = int(existing["plan_id"])
            inserted_count = 0
        else:
            if strategy == CALENDAR_WRITE_STRATEGY_REPLACE_FUTURE_PLAN_TASKS:
                replaced_calendar_count = _delete_future_plan_calendar_rows(conn, user_id=uid, plan_days=plan_days)
            cursor = conn.execute(
                """
                INSERT INTO milk_plan (
                    user_id, plan_name, plan_type, plan_days, plan_summary,
                    milestone_summary, milestone_list, plan_payload_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    uid,
                    plan_name,
                    plan_type,
                    plan_days,
                    summary,
                    norm_text(plan.get("milestone_summary")),
                    json.dumps(plan.get("milestone_list") or [], ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            plan_id = int(cursor.lastrowid or 0)
            inserted_count = _insert_calendar_rows(conn, user_id=uid, plan_id=plan_id, plan=plan)

    calendar_rows = fetch_all(
        """
        SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
               content, type, source, is_milk_pump, finish, created_at, modified_at
        FROM calendar
        WHERE user_id = ?
          AND plan_id = ?
        ORDER BY date ASC, start_time ASC, task_id ASC
        """,
        (uid, plan_id),
    )
    return ok_result(
        "plan_applied",
        "计划已保存，并已写入 calendar。",
        {
            "plan_id": plan_id,
            "user_id": uid,
            "plan_name": plan_name,
            "plan_type": plan_type,
            "plan_days": plan_days,
            "inserted_calendar_count": inserted_count,
            "replaced_calendar_count": replaced_calendar_count,
            "calendar_write_strategy": strategy,
            "calendar_delta": calendar_delta,
            "calendar_items": calendar_rows,
        },
    )


def list_milk_plans(
    *,
    user_id: str,
    plan_type: str | None = None,
    limit: int = 10,
) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法读取计划列表。")

    params: list[Any] = [uid]
    clauses = ["user_id = ?"]
    normalized_type = _normalize_plan_type(plan_type) if plan_type else ""
    if normalized_type:
        clauses.append("plan_type = ?")
        params.append(normalized_type)
    params.append(max(to_int(limit, 10), 1))
    rows = fetch_all(
        f"""
        SELECT plan_id, user_id, plan_name, plan_type, plan_days, plan_summary,
               milestone_summary, milestone_list, plan_payload_json, created_at, updated_at
        FROM milk_plan
        WHERE {" AND ".join(clauses)}
        ORDER BY plan_id DESC
        LIMIT ?
        """,
        params,
    )
    plans = [_normalize_plan_row(row, include_payload=False) for row in rows]
    return ok_result(
        "milk_plans_loaded",
        f"已读取 {len(plans)} 个奶量计划。",
        {"user_id": uid, "plans": plans, "count": len(plans)},
    )


def get_milk_plan(*, user_id: str, plan_id: int | None = None) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法读取计划。")

    params: list[Any] = [uid]
    plan_clause = ""
    if plan_id is not None:
        plan_clause = "AND plan_id = ?"
        params.append(int(plan_id))
    row = fetch_one(
        f"""
        SELECT plan_id, user_id, plan_name, plan_type, plan_days, plan_summary,
               milestone_summary, milestone_list, plan_payload_json, created_at, updated_at
        FROM milk_plan
        WHERE user_id = ?
          {plan_clause}
        ORDER BY plan_id DESC
        LIMIT 1
        """,
        params,
    )
    if not row:
        return error_result("milk_plan_not_found", "未找到对应的奶量计划。")
    normalized = _normalize_plan_row(row, include_payload=True)
    calendar_rows = fetch_all(
        """
        SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
               content, type, source, is_milk_pump, finish, created_at, modified_at
        FROM calendar
        WHERE user_id = ?
          AND plan_id = ?
        ORDER BY date ASC, start_time ASC, task_id ASC
        """,
        (uid, normalized.get("plan_id")),
    )
    return ok_result(
        "milk_plan_loaded",
        "已读取奶量计划。",
        {"plan": normalized, "calendar_items": calendar_rows},
    )


def delete_milk_plan(
    *,
    user_id: str,
    plan_id: int,
    idempotency_key: str,
    delete_calendar_items: bool = True,
) -> ServiceResult:
    uid = norm_text(user_id)
    pid = to_int(plan_id, 0)
    if not uid or pid <= 0:
        return error_result("missing_required_field", "user_id and plan_id are required.")
    if not norm_text(idempotency_key):
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")

    with transaction() as conn:
        existing = conn.execute(
            """
            SELECT plan_id, user_id, plan_name, plan_type, plan_days, plan_summary,
                   milestone_summary, milestone_list, plan_payload_json, created_at, updated_at
            FROM milk_plan
            WHERE user_id = ?
              AND plan_id = ?
            """,
            (uid, pid),
        ).fetchone()
        if existing is None:
            return ok_result(
                "milk_plan_already_absent",
                "该奶量计划已不存在。",
                {"plan_id": pid, "deleted_plan_count": 0, "deleted_calendar_count": 0},
            )
        deleted_calendar_count = 0
        if delete_calendar_items:
            cursor = conn.execute(
                """
                DELETE FROM calendar
                WHERE user_id = ?
                  AND plan_id = ?
                """,
                (uid, pid),
            )
            deleted_calendar_count = int(cursor.rowcount or 0)
        cursor = conn.execute(
            """
            DELETE FROM milk_plan
            WHERE user_id = ?
              AND plan_id = ?
            """,
            (uid, pid),
        )
    return ok_result(
        "milk_plan_deleted",
        "奶量计划已删除。",
        {
            "plan_id": pid,
            "deleted_plan_count": int(cursor.rowcount or 0),
            "deleted_calendar_count": deleted_calendar_count,
            "deleted_plan": _normalize_plan_row(dict(existing), include_payload=False),
        },
    )


def update_milk_plan(
    *,
    user_id: str,
    plan_id: int,
    patch: dict[str, Any] | str,
    idempotency_key: str,
    reexpand_calendar: bool = False,
) -> ServiceResult:
    uid = norm_text(user_id)
    pid = to_int(plan_id, 0)
    if not uid or pid <= 0:
        return error_result("missing_required_field", "user_id and plan_id are required.")
    if not norm_text(idempotency_key):
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")

    normalized_patch = _parse_options(patch)
    if not normalized_patch:
        return error_result("empty_patch", "没有可更新的计划字段。")

    existing = fetch_one(
        """
        SELECT plan_id, user_id, plan_name, plan_type, plan_days, plan_summary,
               milestone_summary, milestone_list, plan_payload_json, created_at, updated_at
        FROM milk_plan
        WHERE user_id = ?
          AND plan_id = ?
        """,
        (uid, pid),
    )
    if not existing:
        return error_result("milk_plan_not_found", "未找到对应的奶量计划。")

    payload_plan = _extract_plan_from_patch(normalized_patch)
    updates = _plan_updates_from_patch(normalized_patch, payload_plan)
    if not updates:
        return error_result("empty_patch", "没有可更新的计划字段。")

    reexpanded_count = 0
    with transaction() as conn:
        set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
        conn.execute(
            f"""
            UPDATE milk_plan
            SET {set_clause},
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
              AND plan_id = ?
            """,
            [*updates.values(), uid, pid],
        )
        if reexpand_calendar and payload_plan:
            conn.execute(
                """
                DELETE FROM calendar
                WHERE user_id = ?
                  AND plan_id = ?
                """,
                (uid, pid),
            )
            reexpanded_count = _insert_calendar_rows(conn, user_id=uid, plan_id=pid, plan=payload_plan)

    refreshed = get_milk_plan(user_id=uid, plan_id=pid)
    data = refreshed.get("data") if isinstance(refreshed.get("data"), dict) else {}
    return ok_result(
        "milk_plan_updated",
        "奶量计划已更新。",
        {"plan": data.get("plan"), "reexpanded_calendar_count": reexpanded_count},
    )


def regenerate_milk_plan_preview(
    *,
    user_id: str,
    plan_id: int | None = None,
    plan_type: str | None = None,
    plan_days: int | None = None,
    custom_target_daily_ml: float | None = None,
    as_of_time: str | None = None,
    options: dict[str, Any] | str | None = None,
) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法重新生成计划草稿。")

    existing_result = get_milk_plan(user_id=uid, plan_id=plan_id) if plan_id is not None else None
    existing_plan = {}
    if existing_result and existing_result.get("ok"):
        data = existing_result.get("data") if isinstance(existing_result.get("data"), dict) else {}
        existing_plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}

    payload = existing_plan.get("plan_payload") if isinstance(existing_plan.get("plan_payload"), dict) else {}
    saved_draft = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    target_type = _normalize_plan_type(plan_type or existing_plan.get("plan_type") or saved_draft.get("plan_type"))
    target_days = plan_days if plan_days is not None else to_int(existing_plan.get("plan_days") or saved_draft.get("plan_days"), 7)
    target_ml = custom_target_daily_ml
    if target_ml is None and isinstance(saved_draft, dict):
        raw_target = saved_draft.get("target_daily_ml")
        target_ml = float(raw_target) if isinstance(raw_target, (int, float)) and raw_target > 0 else None

    preview = preview_milk_plan(
        user_id=uid,
        plan_type=target_type,
        plan_days=target_days,
        custom_target_daily_ml=target_ml,
        as_of_time=as_of_time,
        options=options,
    )
    if isinstance(preview.get("data"), dict):
        preview["data"]["regenerated_from_plan_id"] = existing_plan.get("plan_id") if existing_plan else None
    return preview


def validate_milk_plan_target(
    *,
    user_id: str,
    plan_type: str,
    target_daily_ml: float | None = None,
    delta_ml: float | None = None,
    as_of_time: str | None = None,
) -> ServiceResult:
    uid = norm_text(user_id)
    normalized_type = _normalize_plan_type(plan_type)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法校验计划目标。")
    if normalized_type not in {PLAN_TYPE_INCREASE, PLAN_TYPE_DECREASE, PLAN_TYPE_MAINTAIN}:
        return error_result("unsupported_plan_type", "目前只支持追奶、稳奶或减奶目标校验。")

    assessment = _evaluate_plan_window(uid, as_of_time=as_of_time)
    assessment_data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
    pumping_summary = assessment_data.get("pumping_summary") if isinstance(assessment_data.get("pumping_summary"), dict) else {}
    milk_normality = assessment_data.get("milk_normality") if isinstance(assessment_data.get("milk_normality"), dict) else {}
    window = assessment_data.get("window") if isinstance(assessment_data.get("window"), dict) else {}
    window_days = max(to_int(window.get("window_days"), 7), 1)
    current_daily_ml = _current_daily_ml(pumping_summary, window_days=window_days, milk_normality=milk_normality)
    latest_day = _latest_valid_normality_day(milk_normality)
    reference = latest_day.get("yield_reference") if isinstance(latest_day.get("yield_reference"), dict) else {}
    requested_decrease_delta = _requested_decrease_delta_ml(
        current_daily_ml=current_daily_ml,
        target_daily_ml=target_daily_ml,
        delta_ml=delta_ml,
    )
    target = _resolve_target_daily_ml(
        plan_type=normalized_type,
        current_daily_ml=current_daily_ml,
        target_daily_ml=target_daily_ml,
        delta_ml=delta_ml,
    )

    violations: list[str] = []
    warnings: list[str] = []
    if target is None or target < 0:
        violations.append("目标奶量无效，请提供大于等于 0 的目标。")
    elif normalized_type == PLAN_TYPE_INCREASE:
        if target <= current_daily_ml:
            violations.append("追奶目标应高于当前参考日奶量。")
        if target - current_daily_ml < 50:
            warnings.append("目标增量小于 50ml/天，实际意义可能有限。")
        p85 = reference.get("p85")
        if isinstance(p85, (int, float)) and target > float(p85):
            violations.append(f"目标超过 P85 参考上限（约 {float(p85):.0f}ml/天），不建议。")
    elif normalized_type == PLAN_TYPE_DECREASE:
        if target >= current_daily_ml:
            violations.append("减奶目标应低于当前参考日奶量。")
        decrease = requested_decrease_delta if requested_decrease_delta is not None else current_daily_ml - target
        if decrease < DECREASE_MIN_DAILY_DELTA_ML:
            violations.append(f"减奶目标下限为 {DECREASE_MIN_DAILY_DELTA_ML:.0f}ml/天。")
        if decrease > current_daily_ml:
            violations.append(f"减奶目标不能超过当前参考日奶量（约 {current_daily_ml:.0f}ml/天）。")
    else:
        if abs(target - current_daily_ml) > 50:
            warnings.append("稳奶目标通常应接近当前参考日奶量，若要明显增减请改用追奶或减奶计划。")

    valid = len(violations) == 0
    return ok_result(
        "milk_plan_target_validated",
        "计划目标校验通过。" if valid else violations[0],
        {
            "valid": valid,
            "status": "valid" if valid else "invalid",
            "violations": violations,
            "warnings": warnings,
            "current_daily_ml": current_daily_ml,
            "target_daily_ml": round(float(target or 0.0), 1),
            "target_delta_ml": round(max(current_daily_ml - float(target or 0.0), 0.0), 1),
            "reference": reference,
            "milk_status": milk_normality.get("overall_status"),
        },
    )


def validate_milk_plan(
    *,
    user_id: str,
    plan: dict[str, Any] | str,
) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法校验计划。")
    normalized = _normalize_confirmed_plan(plan)
    if not normalized:
        return error_result("invalid_plan", "plan 不是有效计划。")

    plan_type = _normalize_plan_type(normalized.get("plan_type"))
    violations: list[str] = []
    warnings: list[str] = []
    if plan_type not in SUPPORTED_PLAN_TYPES:
        violations.append("计划类型无效，目前只支持追奶、稳奶或减奶。")
    if max(to_int(normalized.get("plan_days"), 0), 0) <= 0:
        violations.append("计划天数必须大于 0。")

    templates = _plan_templates_for_validation(normalized)
    if not templates:
        violations.append("计划时间表不能为空。")
    for template in templates:
        _validate_template_times(template, violations)
    if plan_type == PLAN_TYPE_INCREASE:
        _validate_increase_templates(templates, violations, warnings)
    if plan_type == PLAN_TYPE_DECREASE:
        _validate_decrease_templates(templates, violations, warnings)
    if plan_type in SUPPORTED_PLAN_TYPES:
        _validate_plan_boundary_constraints(normalized, templates, violations, warnings)

    valid = len(violations) == 0
    return ok_result(
        "milk_plan_validated",
        "计划校验通过。" if valid else violations[0],
        {
            "valid": valid,
            "status": "valid" if valid else "invalid",
            "violations": violations,
            "warnings": warnings,
            "schedule_preview_lines": _validation_preview_lines(templates),
        },
    )


def _preview_validation_data(*, user_id: str, draft: dict[str, Any]) -> dict[str, Any]:
    validation = validate_milk_plan(user_id=user_id, plan=draft)
    data = validation.get("data") if isinstance(validation.get("data"), dict) else {}
    return dict(data)


def evaluate_plan_eligibility(
    *,
    milk_assessment: dict[str, Any],
    growth_assessment: dict[str, Any] | None = None,
    requested_plan_type: str | None = None,
    observed_persistent_abnormal: bool = False,
) -> dict[str, Any]:
    """Decide whether plan generation is appropriate after milk/growth assessment."""

    growth_assessment = growth_assessment if isinstance(growth_assessment, dict) else {}
    milk_status = _milk_status_for_eligibility(milk_assessment)
    growth_status = _growth_status_for_eligibility(growth_assessment)
    requested = _normalize_plan_type(requested_plan_type)

    if milk_status == "high" and growth_status == "normal" and observed_persistent_abnormal:
        return {
            "eligible": True,
            "recommended_action": "generate_plan_after_observation",
            "suggested_plan_type": PLAN_TYPE_DECREASE,
            "observation_days": None,
            "reason": "milk_high_growth_normal_persistent",
            "milk_status": milk_status,
            "growth_status": growth_status,
            "message": "奶量偏高且已观察3-5天仍持续异常，可生成减奶计划草稿；仍需避免突然减奶并继续观察宝宝摄入和妈妈不适。",
            "rule_notes": ["每阶段只减少一个吸奶点。", "若胀痛、硬块、发烧或宝宝摄入变化，暂停并寻求专业评估。"],
            "suggestions": ["每阶段只减少一个吸奶点。", "若胀痛、硬块、发烧或宝宝摄入变化，暂停并寻求专业评估。"],
        }

    if milk_status == "high" and growth_status == "normal":
        return {
            "eligible": False,
            "recommended_action": "observe",
            "suggested_plan_type": None,
            "observation_days": 5,
            "reason": "milk_high_growth_normal",
            "milk_status": milk_status,
            "growth_status": growth_status,
            "message": "奶量偏高但宝宝生长评估正常，暂不建议生成追奶或减奶计划；建议先观察3-5天，调整生活节奏并继续记录，若持续异常再生成计划。",
            "rule_notes": [
                "继续记录每次吸奶量、亲喂和瓶喂情况。",
                "避免因为单日偏高就突然减奶。",
                "关注胀痛、硬块、发烧、宝宝摄入和尿布情况。",
            ],
            "suggestions": [
                "继续记录每次吸奶量、亲喂和瓶喂情况。",
                "避免因为单日偏高就突然减奶。",
                "关注胀痛、硬块、发烧、宝宝摄入和尿布情况。",
            ],
        }

    if milk_status == "normal" and growth_status == "normal":
        return {
            "eligible": requested in {"", PLAN_TYPE_MAINTAIN},
            "recommended_action": "maintain_plan",
            "suggested_plan_type": PLAN_TYPE_MAINTAIN,
            "observation_days": None,
            "reason": "milk_normal_growth_normal",
            "milk_status": milk_status,
            "growth_status": growth_status,
            "message": "奶量和宝宝生长评估均正常，适合生成稳奶计划；如用户要求追奶或减奶，应先解释目前更适合维持节奏。",
            "rule_notes": ["保持当前可执行节奏。", "第3天和第7天复盘记录、宝宝表现和妈妈舒适度。"],
            "suggestions": ["保持当前可执行节奏。", "第3天和第7天复盘记录、宝宝表现和妈妈舒适度。"],
        }

    if milk_status == "normal" and growth_status == "abnormal":
        return {
            "eligible": False,
            "recommended_action": "verify_records_and_seek_care",
            "suggested_plan_type": None,
            "observation_days": None,
            "reason": "milk_normal_growth_abnormal",
            "milk_status": milk_status,
            "growth_status": growth_status,
            "message": "奶量数据正常但宝宝生长评估异常，暂不建议直接生成追奶或减奶计划；请先核查记录，询问近期是否生病、进食下降或测量误差，并建议关注及就医评估。",
            "rule_notes": [
                "核查身高、体重、测量时间和喂养记录是否准确。",
                "询问近期是否发烧、腹泻、呕吐、进食变少或睡眠精神状态变化。",
                "建议联系儿科医生或专业人员评估生长情况。",
            ],
            "suggestions": [
                "核查身高、体重、测量时间和喂养记录是否准确。",
                "询问近期是否发烧、腹泻、呕吐、进食变少或睡眠精神状态变化。",
                "建议联系儿科医生或专业人员评估生长情况。",
            ],
        }

    if milk_status in {"low", "high"} and growth_status == "abnormal":
        suggested = PLAN_TYPE_INCREASE if milk_status == "low" else PLAN_TYPE_DECREASE
        return {
            "eligible": True,
            "recommended_action": "generate_plan_and_seek_care",
            "suggested_plan_type": suggested,
            "observation_days": None,
            "reason": "milk_abnormal_growth_abnormal",
            "milk_status": milk_status,
            "growth_status": growth_status,
            "message": "奶量和宝宝生长评估均异常，建议立即生成对应的追奶或减奶计划草稿，同时建议尽快就医或寻求 IBCLC/儿科评估。",
            "rule_notes": ["生成计划草稿后仍需专业评估。", "重点观察宝宝摄入、尿布、精神状态和妈妈乳房不适。"],
            "suggestions": ["生成计划草稿后仍需专业评估。", "重点观察宝宝摄入、尿布、精神状态和妈妈乳房不适。"],
        }

    if milk_status == "low":
        return {
            "eligible": True,
            "recommended_action": "generate_plan",
            "suggested_plan_type": PLAN_TYPE_INCREASE,
            "observation_days": None,
            "reason": "milk_low_growth_not_abnormal",
            "milk_status": milk_status,
            "growth_status": growth_status,
            "message": "奶量偏低，可生成追奶计划草稿，并提醒继续观察宝宝摄入和生长信号。",
            "rule_notes": ["优先保证计划可执行。", "连续记录3天后复盘。"],
            "suggestions": ["优先保证计划可执行。", "连续记录3天后复盘。"],
        }

    return {
        "eligible": True,
        "recommended_action": "generate_requested_plan",
        "suggested_plan_type": requested if requested in SUPPORTED_PLAN_TYPES else PLAN_TYPE_MAINTAIN,
        "observation_days": None,
        "reason": "default_plan_allowed",
        "milk_status": milk_status,
        "growth_status": growth_status,
        "message": "当前可根据用户目标生成计划草稿。",
        "rule_notes": [],
        "suggestions": [],
    }


def _insert_calendar_rows(conn: Any, *, user_id: str, plan_id: int, plan: dict[str, Any]) -> int:
    plan_days = max(to_int(plan.get("plan_days"), 1), 1)
    start_date = _plan_calendar_start_date()
    inserted = 0
    for day_index in range(plan_days):
        day_no = day_index + 1
        schedule_items = _schedule_items_for_day(plan, day_no)
        if not schedule_items:
            continue
        target_date = (start_date + timedelta(days=day_index)).isoformat()
        for task_id, item in enumerate(schedule_items, start=1):
            time_text = norm_text(item.get("time") or item.get("time_point"))
            if not time_text:
                continue
            start_time = _datetime_for_date(target_date, time_text)
            if not start_time:
                continue
            duration = max(to_int(item.get("duration_minutes"), 30), 1)
            end_time = _datetime_for_date(target_date, _add_minutes(time_text, duration))
            conn.execute(
                """
                INSERT INTO calendar (
                    user_id, plan_id, date, task_id, start_time, end_time,
                    content, type, source, is_milk_pump, finish, created_at, modified_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'false', CURRENT_TIMESTAMP, NULL)
                """,
                (
                    user_id,
                    plan_id,
                    target_date,
                    task_id,
                    start_time,
                    end_time,
                    norm_text(item.get("calendar_title") or item.get("title") or item.get("content") or item.get("action") or item.get("action_text"))
                    or "吸奶",
                    CALENDAR_TYPE_PUMP,
                    CALENDAR_SOURCE_SYSTEM,
                ),
            )
            inserted += 1
    return inserted


def _normalize_calendar_write_strategy(value: Any) -> str:
    token = norm_text(value)
    if token in SUPPORTED_CALENDAR_WRITE_STRATEGIES:
        return token
    return ""


def _calendar_write_delta(
    *,
    user_id: str,
    plan: dict[str, Any],
    calendar_write_strategy: str | None = None,
) -> dict[str, Any]:
    plan_days = max(to_int(plan.get("plan_days"), 1), 1)
    start_date = _plan_calendar_start_date()
    end_date = start_date + timedelta(days=plan_days)
    existing = _existing_future_plan_calendar_summary(user_id=user_id, start_date=start_date, end_date=end_date)
    draft_count = _plan_calendar_row_count(plan)
    strategy = _normalize_calendar_write_strategy(calendar_write_strategy)
    append_final = int(existing.get("total_count", 0)) + draft_count
    replace_final = draft_count
    selected_final = replace_final if strategy == CALENDAR_WRITE_STRATEGY_REPLACE_FUTURE_PLAN_TASKS else append_final
    selected_replaced = int(existing.get("total_count", 0)) if strategy == CALENDAR_WRITE_STRATEGY_REPLACE_FUTURE_PLAN_TASKS else 0
    return {
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "end_exclusive": True,
        },
        "existing_future_plan_task_count": int(existing.get("total_count", 0)),
        "existing_future_plan_task_daily": existing.get("daily", []),
        "draft_task_count": draft_count,
        "has_existing_future_plan_tasks": int(existing.get("total_count", 0)) > 0,
        "requires_calendar_write_strategy": int(existing.get("total_count", 0)) > 0 and not strategy,
        "selected_strategy": strategy or None,
        "selected_final_task_count": selected_final,
        "selected_added_task_count": draft_count,
        "selected_replaced_task_count": selected_replaced,
        "strategy_options": {
            CALENDAR_WRITE_STRATEGY_APPEND: {
                "label": "追加到现有日程",
                "final_task_count": append_final,
                "added_task_count": draft_count,
                "replaced_task_count": 0,
            },
            CALENDAR_WRITE_STRATEGY_REPLACE_FUTURE_PLAN_TASKS: {
                "label": "替换未来未完成计划任务",
                "final_task_count": replace_final,
                "added_task_count": draft_count,
                "replaced_task_count": int(existing.get("total_count", 0)),
            },
        },
    }


def _existing_future_plan_calendar_summary(*, user_id: str, start_date: date, end_date: date) -> dict[str, Any]:
    rows = fetch_all(
        """
        SELECT date, COUNT(*) AS count
        FROM calendar
        WHERE user_id = ?
          AND date >= ?
          AND date < ?
          AND plan_id IS NOT NULL
          AND source = ?
          AND type IN (?, ?)
          AND finish != 'true'
        GROUP BY date
        ORDER BY date ASC
        """,
        (
            user_id,
            start_date.isoformat(),
            end_date.isoformat(),
            CALENDAR_SOURCE_SYSTEM,
            CALENDAR_TYPE_PUMP,
            CALENDAR_TYPE_NURSING,
        ),
    )
    daily = [{"date": norm_text(row.get("date")), "count": to_int(row.get("count"), 0)} for row in rows]
    return {"total_count": sum(item["count"] for item in daily), "daily": daily}


def _delete_future_plan_calendar_rows(conn: Any, *, user_id: str, plan_days: int) -> int:
    start_date = _plan_calendar_start_date()
    end_date = start_date + timedelta(days=max(plan_days, 1))
    cursor = conn.execute(
        """
        DELETE FROM calendar
        WHERE user_id = ?
          AND date >= ?
          AND date < ?
          AND plan_id IS NOT NULL
          AND source = ?
          AND type IN (?, ?)
          AND finish != 'true'
        """,
        (
            user_id,
            start_date.isoformat(),
            end_date.isoformat(),
            CALENDAR_SOURCE_SYSTEM,
            CALENDAR_TYPE_PUMP,
            CALENDAR_TYPE_NURSING,
        ),
    )
    return int(cursor.rowcount or 0)


def _plan_calendar_start_date() -> date:
    return datetime.now().date() + timedelta(days=1)


def _plan_calendar_row_count(plan: dict[str, Any]) -> int:
    plan_days = max(to_int(plan.get("plan_days"), 1), 1)
    count = 0
    for day_index in range(plan_days):
        count += len(_schedule_items_for_day(plan, day_index + 1))
    return count


def _build_plan_schedule_items(
    *,
    plan_type: str,
    plan_context: dict[str, Any],
    pumping_count: int,
    current_frequency: int,
    current_daily_ml: float,
    target_daily_ml: float,
    desired_count: int,
    require_pp: bool,
) -> list[dict[str, Any]]:
    if plan_type == PLAN_TYPE_INCREASE:
        picked = _increase_schedule_times(
            plan_context=plan_context,
            desired_count=max(desired_count, pumping_count, 1),
            current_frequency=current_frequency,
            current_pumping_count=pumping_count,
            gap_ml=max(target_daily_ml - current_daily_ml, 0.0),
        )
    elif plan_type == PLAN_TYPE_MAINTAIN:
        picked = _maintain_schedule_times(plan_context=plan_context, desired_count=desired_count)
    elif plan_type == PLAN_TYPE_DECREASE:
        picked = _decrease_base_schedule_times(plan_context=plan_context, desired_count=max(pumping_count, desired_count, 1))
    else:
        picked = []

    if picked:
        picked = _select_spaced_times(picked, min_gap_minutes=60, limit=max(len(picked), desired_count, 1))
    elif plan_type == PLAN_TYPE_DECREASE:
        picked = ["07:00", "12:00", "18:00", "22:00"]
    elif plan_type == PLAN_TYPE_MAINTAIN:
        picked = ["06:00", "10:00", "14:00", "18:00", "22:00"]
    else:
        picked = ["06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]

    final_desired = max(desired_count, 0)
    if plan_type == PLAN_TYPE_DECREASE:
        final_desired = max(final_desired, len(picked))
    if final_desired > 0 and len(picked) < final_desired:
        picked = _fill_evenly_spaced(picked, final_desired)
    if final_desired > 0 and len(picked) > final_desired:
        picked = _trim_to_count(picked, final_desired)
    if plan_type in {PLAN_TYPE_INCREASE, PLAN_TYPE_DECREASE}:
        picked = _prefer_daytime_times(picked, desired_count=final_desired or len(picked))

    action = "双侧同时吸奶15分钟"
    duration_minutes = INCREASE_REGULAR_PUMP_MINUTES
    if plan_type == PLAN_TYPE_DECREASE:
        action = "按计划吸奶，避免突然过快减少"
        duration_minutes = 30
    if plan_type == PLAN_TYPE_MAINTAIN:
        action = "维持当前节奏吸奶"
        duration_minutes = 30
    items = [
        {"time": time, "calendar_title": "吸奶", "action": action, "duration_minutes": duration_minutes, "kind": "regular"}
        for time in picked
    ]
    if plan_type == PLAN_TYPE_INCREASE and require_pp and items:
        pp_index = _first_daytime_item_index(items)
        items[pp_index] = {
            **items[pp_index],
            "kind": "pp",
            "calendar_title": "吸奶",
            "action": "本阶段执行吸奶：双侧吸20分钟，休息10分钟，再吸10分钟，休息10分钟，再吸10分钟",
            "duration_minutes": INCREASE_PP_MINUTES,
        }
    return items


def _increase_schedule_times(
    *,
    plan_context: dict[str, Any],
    desired_count: int,
    current_frequency: int,
    current_pumping_count: int,
    gap_ml: float,
) -> list[str]:
    requirements = _derive_increase_schedule_requirements(
        gap_ml=gap_ml,
        current_frequency=current_frequency,
        current_pumping_count=current_pumping_count,
    )
    desired = max(to_int(requirements.get("desired_pumping_count"), desired_count), desired_count, 1)
    schedule_times = _unique_sorted_times(_list_from_context(plan_context, "pumping_times"))
    if not schedule_times:
        schedule_times = _unique_sorted_times(_list_from_context(plan_context, "recent_pumping_times"))
    occupied_times = _unique_sorted_times([*schedule_times, *_list_from_context(plan_context, "breastfeeding_times")])
    gap_windows = _build_circular_gap_windows(occupied_times or schedule_times)

    for min_distance in (120, 90, 60, 30):
        for window in gap_windows:
            if len(schedule_times) >= desired:
                break
            candidate = _pick_insert_time_from_window(window, occupied_times)
            if not candidate or candidate in schedule_times:
                continue
            if not _candidate_is_spaced(candidate, occupied_times, min_distance):
                continue
            schedule_times.append(candidate)
            occupied_times.append(candidate)
        if len(schedule_times) >= desired:
            break

    if len(schedule_times) < desired:
        schedule_times = _fill_evenly_spaced(schedule_times, desired)
    return _unique_sorted_times(schedule_times)[:desired]


def _maintain_schedule_times(*, plan_context: dict[str, Any], desired_count: int) -> list[str]:
    pumping_times = _unique_sorted_times(_list_from_context(plan_context, "pumping_times"))
    if pumping_times:
        return pumping_times
    recent_times = _unique_sorted_times(_list_from_context(plan_context, "recent_pumping_times"))
    if recent_times:
        return recent_times if desired_count <= 0 else _trim_or_fill_times(recent_times, desired_count)
    return []


def _decrease_base_schedule_times(*, plan_context: dict[str, Any], desired_count: int) -> list[str]:
    pumping_times = _unique_sorted_times(_list_from_context(plan_context, "pumping_times"))
    if pumping_times:
        return pumping_times
    recent_times = _unique_sorted_times(_list_from_context(plan_context, "recent_pumping_times"))
    if recent_times:
        return recent_times
    return _fill_evenly_spaced([], max(desired_count, 1))


def _evaluate_plan_window(user_id: str, *, as_of_time: str | None) -> ServiceResult:
    assessment = evaluate_milk_status(user_id=user_id, as_of_time=as_of_time, window_days=7, include_today=False)
    data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
    pumping = data.get("pumping_summary") if isinstance(data.get("pumping_summary"), dict) else {}
    if to_int(pumping.get("count"), 0) > 0:
        return assessment

    latest = fetch_one(
        """
        SELECT MAX(pump_start_time) AS latest_pump_start_time
        FROM pumping_log
        WHERE user_id = ?
        """,
        (user_id,),
    ) or {}
    latest_dt = parse_datetime(latest.get("latest_pump_start_time"))
    if latest_dt is None:
        return assessment
    fallback_as_of = datetime.combine(latest_dt.date() + timedelta(days=1), datetime.min.time())
    return evaluate_milk_status(
        user_id=user_id,
        as_of_time=fallback_as_of.strftime("%Y-%m-%d %H:%M:%S"),
        window_days=7,
        include_today=False,
    )


def _assessment_window_days(assessment_data: dict[str, Any]) -> int:
    window = assessment_data.get("window") if isinstance(assessment_data.get("window"), dict) else {}
    return to_int(window.get("window_days"), 0)


def _assessment_has_milk_basis(assessment_data: dict[str, Any]) -> bool:
    pumping = assessment_data.get("pumping_summary") if isinstance(assessment_data.get("pumping_summary"), dict) else {}
    feeding = assessment_data.get("feeding_summary") if isinstance(assessment_data.get("feeding_summary"), dict) else {}
    if to_int(pumping.get("count"), 0) > 0 or to_int(feeding.get("count"), 0) > 0:
        return True
    milk_normality = assessment_data.get("milk_normality") if isinstance(assessment_data.get("milk_normality"), dict) else {}
    days = milk_normality.get("days") if isinstance(milk_normality.get("days"), list) else []
    return any(isinstance(item, dict) and isinstance(item.get("estimated_daily_milk_ml"), (int, float)) for item in days)


def _evaluate_growth_for_plan(user_id: str, *, as_of_time: str | None) -> ServiceResult:
    try:
        return evaluate_infant_growth(user_id=user_id, infant_id=None, as_of_time=as_of_time)
    except Exception as exc:
        return ok_result(
            "infant_growth_unavailable",
            "宝宝生长评估暂不可用。",
            {"status": "insufficient_data", "error": type(exc).__name__},
        )


def _milk_status_for_eligibility(assessment_data: dict[str, Any]) -> str:
    normality = assessment_data.get("milk_normality") if isinstance(assessment_data.get("milk_normality"), dict) else {}
    status = norm_text(normality.get("overall_status") or assessment_data.get("assessment_status"))
    if status == "over_supply_alert":
        return "high"
    if status == "under_supply_alert":
        return "low"
    if status == "normal":
        return "normal"
    return "unknown"


def _growth_status_for_eligibility(growth_data: dict[str, Any]) -> str:
    status = norm_text(growth_data.get("status"))
    if status == "normal":
        return "normal"
    if status in {"slow", "fast"}:
        return "abnormal"
    return "unknown"


def _recent_pumping_times(user_id: str, *, as_of_time: str | None, limit: int) -> list[str]:
    as_of_dt = parse_datetime(as_of_time) if as_of_time else datetime.now()
    if as_of_dt is None:
        as_of_dt = datetime.now()
    rows = fetch_all(
        """
        SELECT pump_start_time
        FROM pumping_log
        WHERE user_id = ?
          AND pump_start_time < ?
        ORDER BY pump_start_time DESC
        LIMIT ?
        """,
        (user_id, as_of_dt.strftime("%Y-%m-%d %H:%M:%S"), max(limit * 2, limit)),
    )
    times = []
    for row in rows:
        parsed = parse_datetime(row.get("pump_start_time"))
        if parsed is not None:
            times.append(parsed.strftime("%H:%M"))
    return sorted(set(times))[:limit]


def _events_from_times(times: list[str], *, source: str) -> list[dict[str, Any]]:
    return [{"time": time, "source": source} for time in _unique_sorted_times(times)]


def _collect_recent_plan_context(user_id: str, *, as_of_time: str | None) -> dict[str, Any]:
    as_of_dt = parse_datetime(as_of_time) if as_of_time else datetime.now()
    if as_of_dt is None:
        as_of_dt = datetime.now()
    infant_rows = fetch_all(
        """
        SELECT infant_id, birth_date
        FROM infant_profile
        WHERE user_id = ?
        ORDER BY infant_id ASC
        """,
        (user_id,),
    )

    rhythm = build_recent_milk_rhythm_context(
        user_id=user_id,
        as_of_time=_db_time(as_of_dt),
        days=7,
        include_today=False,
    )
    selected_day = rhythm.get("selected_day") if isinstance(rhythm.get("selected_day"), dict) else {}
    summary = rhythm.get("summary") if isinstance(rhythm.get("summary"), dict) else {}
    target_day = str(selected_day.get("date") or (as_of_dt.date() - timedelta(days=1)).isoformat())
    calendar_pump_times = _unique_sorted_times(selected_day.get("planned_pumping_times") if isinstance(selected_day.get("planned_pumping_times"), list) else [])
    calendar_nursing_times = _unique_sorted_times(selected_day.get("planned_nursing_times") if isinstance(selected_day.get("planned_nursing_times"), list) else [])
    measured_pumping_times = _unique_sorted_times(selected_day.get("actual_pumping_times") if isinstance(selected_day.get("actual_pumping_times"), list) else [])
    actual_nursing_times = _unique_sorted_times(selected_day.get("actual_nursing_times") if isinstance(selected_day.get("actual_nursing_times"), list) else [])
    pumping_times = calendar_pump_times or measured_pumping_times
    breastfeeding_times = calendar_nursing_times or actual_nursing_times
    recent_pumping_times = (
        _unique_sorted_times(summary.get("typical_pumping_times") if isinstance(summary.get("typical_pumping_times"), list) else [])
        or _recent_pumping_times(user_id, as_of_time=as_of_time, limit=8)
    )
    all_event_times = _unique_sorted_times([*pumping_times, *breastfeeding_times])
    calendar_pump_events = _events_from_times(calendar_pump_times, source="calendar")
    calendar_nursing_events = _events_from_times(calendar_nursing_times, source="calendar")
    pumping_events = _events_from_times(measured_pumping_times, source="pumping")
    breastfeeding_events = _events_from_times(actual_nursing_times, source="breastfeeding")

    return {
        "target_day": target_day,
        "lookback_days": 7,
        "candidate_days": [
            {
                "target_day": str(day.get("date") or ""),
                "pumping_count": len(day.get("planned_pumping_times") or day.get("actual_pumping_times") or []),
                "breastfeeding_count": len(day.get("planned_nursing_times") or day.get("actual_nursing_times") or []),
                "completeness": day.get("completeness"),
            }
            for day in rhythm.get("days", [])
            if isinstance(day, dict) and day.get("all_times")
        ],
        "pumping_events": pumping_events,
        "calendar_pump_events": calendar_pump_events,
        "calendar_nursing_events": calendar_nursing_events,
        "breastfeeding_events": breastfeeding_events,
        "pumping_times": pumping_times,
        "measured_pumping_times": measured_pumping_times,
        "calendar_pump_times": calendar_pump_times,
        "calendar_nursing_times": calendar_nursing_times,
        "completed_calendar_pump_times": _unique_sorted_times(
            [
                str(item.get("time") or "")
                for item in selected_day.get("planned_pumping", [])
                if isinstance(item, dict) and bool(item.get("completed"))
            ]
        ),
        "completed_calendar_nursing_times": _unique_sorted_times(
            [
                str(item.get("time") or "")
                for item in selected_day.get("planned_nursing", [])
                if isinstance(item, dict) and bool(item.get("completed"))
            ]
        ),
        "breastfeeding_times": breastfeeding_times,
        "actual_nursing_times": actual_nursing_times,
        "all_event_times": all_event_times,
        "recent_pumping_times": recent_pumping_times,
        "recent_milk_rhythm": rhythm,
        "infant_age_months": _infant_age_months(infant_rows, as_of_dt.date()),
        "source": _plan_context_source(calendar_pump_times=calendar_pump_times, measured_pumping_times=measured_pumping_times, breastfeeding_times=breastfeeding_times),
    }


def _schedule_basis_from_plan_context(
    plan_context: dict[str, Any],
    *,
    pumping_summary: dict[str, Any],
    feeding_summary: dict[str, Any],
    window_days: int,
) -> dict[str, Any]:
    calendar_pump_times = _list_from_context(plan_context, "calendar_pump_times")
    measured_pumping_times = _list_from_context(plan_context, "measured_pumping_times")
    pumping_times = _list_from_context(plan_context, "pumping_times")
    breastfeeding_times = _list_from_context(plan_context, "breastfeeding_times")
    recent_pumping_times = _list_from_context(plan_context, "recent_pumping_times")
    selected_pumping_times = pumping_times or recent_pumping_times
    selected_all_times = _unique_sorted_times([*selected_pumping_times, *breastfeeding_times])
    rhythm = plan_context.get("recent_milk_rhythm") if isinstance(plan_context.get("recent_milk_rhythm"), dict) else {}
    rhythm_summary = rhythm.get("summary") if isinstance(rhythm.get("summary"), dict) else {}
    source = _plan_context_source(
        calendar_pump_times=calendar_pump_times,
        measured_pumping_times=measured_pumping_times,
        breastfeeding_times=breastfeeding_times,
    )
    if source == "default_template" and recent_pumping_times:
        source = "recent_pumping_records"

    selected_count = len(selected_all_times)
    summary_event_count = _daily_event_count(pumping_summary, window_days=window_days) + _daily_direct_breastfeeding_count(
        feeding_summary,
        window_days=window_days,
    )
    confidence = "low"
    if source == "recent_calendar_schedule":
        confidence = "high"
    elif selected_count >= 3:
        confidence = "medium"
    elif summary_event_count >= 3:
        confidence = "medium"

    rhythm_confidence = norm_text(rhythm_summary.get("confidence"))
    if rhythm_confidence in {"high", "medium", "low"} and source != "default_template":
        confidence = rhythm_confidence
    ask_daily_counts = False
    target_day = norm_text(plan_context.get("target_day"))
    used_days = [target_day] if target_day and selected_count > 0 else []
    pumping_count = len(selected_pumping_times)
    breastfeeding_count = len(breastfeeding_times)
    longest_gap_hours = _longest_gap_hours(selected_all_times)
    basis_summary = norm_text(rhythm_summary.get("basis_summary")) or _schedule_basis_summary(
        source=source,
        target_day=target_day,
        pumping_count=pumping_count,
        breastfeeding_count=breastfeeding_count,
        confidence=confidence,
    )
    return _drop_empty_dict(
        {
            "source": source,
            "confidence": confidence,
            "used_days": used_days,
            "target_day": target_day,
            "pumping_count_per_day": pumping_count,
            "breastfeeding_count_per_day": breastfeeding_count,
            "total_count_per_day": pumping_count + breastfeeding_count,
            "longest_gap_hours": longest_gap_hours,
            "ask_daily_counts": ask_daily_counts,
            "basis_summary": basis_summary,
        }
    )


def _plan_context_source(
    *,
    calendar_pump_times: list[str],
    measured_pumping_times: list[str],
    breastfeeding_times: list[str],
) -> str:
    if calendar_pump_times:
        return "recent_calendar_schedule"
    if measured_pumping_times and breastfeeding_times:
        return "recent_records"
    if measured_pumping_times:
        return "recent_pumping_records"
    if breastfeeding_times:
        return "recent_breastfeeding_records"
    return "default_template"


def _schedule_basis_summary(
    *,
    source: str,
    target_day: str,
    pumping_count: int,
    breastfeeding_count: int,
    confidence: str,
) -> str:
    if confidence == "low":
        return "最近记录不足，计划只能先按通用节奏起草。"
    date_text = f"{target_day} 的" if target_day else "最近的"
    parts = []
    if pumping_count:
        parts.append(f"吸奶 {pumping_count} 次")
    if breastfeeding_count:
        parts.append(f"亲喂 {breastfeeding_count} 次")
    detail = "、".join(parts) if parts else "喂养记录"
    if source == "recent_calendar_schedule":
        return f"计划已参考最近 7 天内 {date_text}计划提醒：{detail}。"
    return f"计划已参考最近 7 天内 {date_text}实际记录：{detail}。"


def _longest_gap_hours(times: list[str]) -> float | None:
    windows = _build_circular_gap_windows(times)
    if not windows:
        return None
    longest_min = max(to_int(item.get("duration_min"), 0) for item in windows)
    if longest_min <= 0:
        return None
    return round(longest_min / 60, 1)


def _normalize_confirmed_plan(value: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        value = parsed if isinstance(parsed, dict) else {}
    if not isinstance(value, dict):
        return {}
    if isinstance(value.get("draft"), dict):
        return dict(value["draft"])
    if isinstance(value.get("data"), dict) and isinstance(value["data"].get("draft"), dict):
        return dict(value["data"]["draft"])
    return dict(value)


def _normalize_plan_row(row: dict[str, Any], *, include_payload: bool) -> dict[str, Any]:
    result = dict(row)
    milestone_list = norm_text(result.get("milestone_list"))
    if milestone_list:
        try:
            result["milestone_list"] = json.loads(milestone_list)
        except json.JSONDecodeError:
            result["milestone_list"] = []
    if include_payload:
        payload = norm_text(result.get("plan_payload_json"))
        if payload:
            try:
                result["plan_payload"] = json.loads(payload)
            except json.JSONDecodeError:
                result["plan_payload"] = None
    else:
        result.pop("plan_payload_json", None)
    return result


def _extract_plan_from_patch(patch: dict[str, Any]) -> dict[str, Any]:
    for key in ("confirmed_plan", "plan", "draft", "plan_payload"):
        value = patch.get(key)
        if isinstance(value, dict):
            if key == "plan_payload" and isinstance(value.get("plan"), dict):
                return dict(value["plan"])
            return _normalize_confirmed_plan(value)
        if isinstance(value, str):
            normalized = _normalize_confirmed_plan(value)
            if normalized:
                return normalized
    return {}


def _resolve_target_daily_ml(
    *,
    plan_type: str,
    current_daily_ml: float,
    target_daily_ml: float | None,
    delta_ml: float | None,
) -> float | None:
    if target_daily_ml is not None:
        try:
            return round(float(target_daily_ml), 1)
        except (TypeError, ValueError):
            return None
    if delta_ml is None:
        return current_daily_ml if plan_type == PLAN_TYPE_MAINTAIN else None
    try:
        delta = float(delta_ml)
    except (TypeError, ValueError):
        return None
    if plan_type == PLAN_TYPE_DECREASE:
        return round(max(current_daily_ml - delta, 0.0), 1)
    return round(current_daily_ml + delta, 1)


def _plan_templates_for_validation(plan: dict[str, Any]) -> list[dict[str, Any]]:
    templates = plan.get("daily_schedule_templates") if isinstance(plan.get("daily_schedule_templates"), list) else []
    normalized = [template for template in templates if isinstance(template, dict)]
    if normalized:
        return normalized
    template = plan.get("daily_schedule_template") if isinstance(plan.get("daily_schedule_template"), dict) else {}
    items = template.get("items") if isinstance(template.get("items"), list) else []
    if items:
        days = max(to_int(plan.get("plan_days"), 1), 1)
        return [{"day_start": 1, "day_end": days, "phase_label": "daily_schedule_template", "items": items}]
    schedule_items = plan.get("schedule_items") if isinstance(plan.get("schedule_items"), list) else []
    if schedule_items:
        days = max(to_int(plan.get("plan_days"), 1), 1)
        return [{"day_start": 1, "day_end": days, "phase_label": "schedule_items", "items": schedule_items}]
    return []


def _validate_template_times(template: dict[str, Any], violations: list[str]) -> None:
    day_start = to_int(template.get("day_start"), 0)
    day_end = to_int(template.get("day_end"), 0)
    if day_start <= 0 or day_end < day_start:
        violations.append("时间表阶段 day_start/day_end 无效。")
    items = template.get("items") if isinstance(template.get("items"), list) else []
    if not items:
        if to_bool(template.get("guidance_only")):
            return
        violations.append(f"第 {day_start}-{day_end} 天时间表不能为空。")
        return
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            violations.append("时间表条目格式无效。")
            continue
        time_text = norm_text(item.get("time") or item.get("time_point"))
        if _minute_of_day(time_text) is None:
            violations.append(f"时间格式不正确：{time_text or '-'}，请使用 HH:MM。")
        if time_text in seen:
            violations.append(f"时间表存在重复时间：{time_text}。")
        seen.add(time_text)


def _validate_increase_templates(
    templates: list[dict[str, Any]],
    violations: list[str],
    warnings: list[str],
) -> None:
    for template in templates:
        day_start = to_int(template.get("day_start"), 0)
        items = template.get("items") if isinstance(template.get("items"), list) else []
        pp_items = [item for item in items if _is_pp_item(item)]
        if day_start >= 8 and pp_items:
            violations.append("追奶计划第 8 天后不能继续安排加强吸奶节奏。")
        if len(items) > 12:
            warnings.append("追奶计划每日任务超过 12 项，执行压力可能过高。")


def _validate_decrease_templates(
    templates: list[dict[str, Any]],
    violations: list[str],
    warnings: list[str],
) -> None:
    previous_count: int | None = None
    for template in sorted(templates, key=lambda item: to_int(item.get("day_start"), 0)):
        items = template.get("items") if isinstance(template.get("items"), list) else []
        count = len(items)
        if previous_count is not None and count > previous_count:
            violations.append("减奶计划后续阶段不应增加吸奶任务数量。")
        if previous_count is not None and previous_count - count > 1:
            warnings.append("减奶计划单阶段减少超过 1 个吸奶点，建议确认妈妈舒适度。")
        previous_count = count


def _validate_plan_boundary_constraints(
    plan: dict[str, Any],
    templates: list[dict[str, Any]],
    violations: list[str],
    warnings: list[str],
) -> None:
    plan_type = _normalize_plan_type(plan.get("plan_type"))
    rules = plan.get("plan_rules") if isinstance(plan.get("plan_rules"), dict) else {}
    current_daily_ml = _optional_float(plan.get("current_daily_ml"))
    target_daily_ml = _optional_float(plan.get("target_daily_ml"))
    current_frequency = to_int(plan.get("current_frequency"), 0)
    current_pumping_count = _current_pumping_count_from_plan(plan, rules, templates)
    counts = _template_item_counts(templates)

    if plan_type == PLAN_TYPE_INCREASE:
        if current_pumping_count is not None and counts and max(counts) <= current_pumping_count:
            violations.append("追奶计划修改后吸奶任务次数必须大于当前吸奶次数。")
        max_interval_hours = _optional_float(rules.get("max_interval_hours")) or 5.0
        for template in templates:
            if to_bool(template.get("guidance_only")):
                continue
            gap = _max_adjacent_template_gap_minutes(template)
            if gap is not None and gap > max_interval_hours * 60:
                day_start = to_int(template.get("day_start"), 0)
                day_end = to_int(template.get("day_end"), day_start)
                violations.append(f"追奶计划第 {day_start}-{day_end} 天相邻两次吸奶间隔不能超过 {max_interval_hours:g} 小时。")
        return

    if plan_type == PLAN_TYPE_DECREASE:
        if current_daily_ml is not None and target_daily_ml is not None and target_daily_ml > current_daily_ml:
            violations.append("减奶计划目标奶量不能超过当前参考日奶量。")
        if current_frequency > 0 and counts and max(counts) > current_frequency:
            violations.append("减奶计划修改后吸奶任务频次不能超过当前吸奶+亲喂总频次。")
        if current_pumping_count is not None and counts and max(counts) > current_pumping_count:
            violations.append("减奶计划修改后吸奶任务次数不能超过当前吸奶次数。")
        return

    if plan_type == PLAN_TYPE_MAINTAIN:
        baseline = current_pumping_count if current_pumping_count is not None else (counts[0] if counts else None)
        if baseline is not None:
            changed = [count for count in counts if count != baseline]
            if changed:
                violations.append("稳奶计划修改后吸奶任务次数不能改变。")
        if current_daily_ml is not None and target_daily_ml is not None and abs(target_daily_ml - current_daily_ml) > 50:
            warnings.append("稳奶计划目标奶量偏离当前参考日奶量超过 50ml，建议确认是否应改为追奶或减奶。")


def _template_item_counts(templates: list[dict[str, Any]]) -> list[int]:
    counts: list[int] = []
    for template in templates:
        if to_bool(template.get("guidance_only")):
            counts.append(0)
            continue
        items = template.get("items") if isinstance(template.get("items"), list) else []
        counts.append(len([item for item in items if isinstance(item, dict)]))
    return counts


def _current_pumping_count_from_plan(
    plan: dict[str, Any],
    rules: dict[str, Any],
    templates: list[dict[str, Any]],
) -> int | None:
    raw = rules.get("current_pumping_count")
    if raw is None:
        raw = plan.get("current_pumping_count")
    count = to_int(raw, -1)
    if count >= 0:
        return count

    plan_type = _normalize_plan_type(plan.get("plan_type"))
    desired = to_int(rules.get("desired_pumping_count"), -1)
    if plan_type == PLAN_TYPE_INCREASE:
        add_count = to_int(rules.get("add_count"), 0)
        if desired >= 0:
            return max(desired - add_count, 0)
    if plan_type == PLAN_TYPE_DECREASE:
        stage_targets = rules.get("stage_targets") if isinstance(rules.get("stage_targets"), list) else []
        first_stage = stage_targets[0] if stage_targets and isinstance(stage_targets[0], dict) else {}
        first_target = to_int(first_stage.get("target_pumping_count"), -1)
        if first_target >= 0:
            return first_target + 1
        if desired >= 0:
            return desired + 1
    if plan_type == PLAN_TYPE_MAINTAIN and desired >= 0:
        return desired

    counts = _template_item_counts(templates)
    return counts[0] if counts else None


def _max_adjacent_template_gap_minutes(template: dict[str, Any]) -> int | None:
    items = template.get("items") if isinstance(template.get("items"), list) else []
    minutes = []
    for item in items:
        if not isinstance(item, dict):
            continue
        minute = _minute_of_day(norm_text(item.get("time") or item.get("time_point")))
        if minute is not None:
            minutes.append(minute)
    minutes = sorted(set(minutes))
    if len(minutes) < 2:
        return None
    return max(minutes[index + 1] - minutes[index] for index in range(len(minutes) - 1))


def _is_pp_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    text = " ".join(
        [
            norm_text(item.get("kind")),
            norm_text(item.get("action")),
            norm_text(item.get("action_text")),
            norm_text(item.get("content")),
        ]
    ).lower()
    return "pp" in text or "power pumping" in text or "追奶" in text


def _validation_preview_lines(templates: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for template in templates:
        day_start = to_int(template.get("day_start"), 0)
        day_end = to_int(template.get("day_end"), 0)
        items = template.get("items") if isinstance(template.get("items"), list) else []
        lines.append(f"第{day_start}-{day_end}天：{len(items)}项")
    return lines


def _plan_updates_from_patch(patch: dict[str, Any], payload_plan: dict[str, Any]) -> dict[str, Any]:
    source = payload_plan or patch
    updates: dict[str, Any] = {}
    if "plan_name" in source or "plan_name" in patch:
        updates["plan_name"] = norm_text(source.get("plan_name") or patch.get("plan_name"))
    if "plan_type" in source or "plan_type" in patch:
        plan_type = _normalize_plan_type(source.get("plan_type") or patch.get("plan_type"))
        if plan_type:
            updates["plan_type"] = plan_type
    if "plan_days" in source or "plan_days" in patch:
        updates["plan_days"] = max(to_int(source.get("plan_days") or patch.get("plan_days"), 1), 1)
    if "summary" in source or "plan_summary" in patch:
        updates["plan_summary"] = norm_text(source.get("summary") or patch.get("plan_summary"))
    if "milestone_summary" in source or "milestone_summary" in patch:
        updates["milestone_summary"] = norm_text(source.get("milestone_summary") or patch.get("milestone_summary"))
    if "milestone_list" in source or "milestone_list" in patch:
        milestone_list = source.get("milestone_list") if "milestone_list" in source else patch.get("milestone_list")
        updates["milestone_list"] = json.dumps(milestone_list if isinstance(milestone_list, list) else [], ensure_ascii=False)
    if payload_plan:
        payload = {
            "ok": True,
            "plan_generated": True,
            "plan_type": _normalize_plan_type(payload_plan.get("plan_type")),
            "summary": norm_text(payload_plan.get("summary")),
            "plan": payload_plan,
            "updated_by": "milk_plan_mutate",
        }
        updates["plan_payload_json"] = json.dumps(payload, ensure_ascii=False)
    elif "plan_payload_json" in patch:
        updates["plan_payload_json"] = norm_text(patch.get("plan_payload_json"))
    return {key: value for key, value in updates.items() if value is not None}


def _schedule_items_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    template = plan.get("daily_schedule_template") if isinstance(plan.get("daily_schedule_template"), dict) else {}
    items = template.get("items") if isinstance(template.get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]


def _schedule_items_for_day(plan: dict[str, Any], day_no: int) -> list[dict[str, Any]]:
    templates = plan.get("daily_schedule_templates") if isinstance(plan.get("daily_schedule_templates"), list) else []
    for template in templates:
        if not isinstance(template, dict):
            continue
        day_start = max(to_int(template.get("day_start"), 1), 1)
        day_end = max(to_int(template.get("day_end"), day_start), day_start)
        if day_start <= day_no <= day_end:
            items = template.get("items") if isinstance(template.get("items"), list) else []
            return [item for item in items if isinstance(item, dict)]
    return _schedule_items_from_plan(plan)


def _normalize_plan_type(value: Any) -> str:
    token = norm_text(value).lower()
    if token == "maintain":
        return PLAN_TYPE_MAINTAIN
    return token


def _current_daily_ml(pumping_summary: dict[str, Any], *, window_days: int, milk_normality: dict[str, Any]) -> float:
    average = _average_daily_milk_ml(milk_normality)
    if average is not None:
        return average
    total = float(pumping_summary.get("total_ml") or 0.0)
    return round(total / max(window_days, 1), 1)


def _average_daily_milk_ml(milk_normality: dict[str, Any]) -> float | None:
    days = milk_normality.get("days") if isinstance(milk_normality.get("days"), list) else []
    values: list[float] = []
    for item in days:
        if not isinstance(item, dict):
            continue
        estimated = item.get("estimated_daily_milk_ml")
        if isinstance(estimated, (int, float)) and estimated >= 0:
            values.append(float(estimated))
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _target_daily_ml(
    *,
    plan_type: str,
    current_daily_ml: float,
    custom_target_daily_ml: float | None,
    milk_normality: dict[str, Any],
) -> float:
    if custom_target_daily_ml is not None and custom_target_daily_ml > 0:
        return round(float(custom_target_daily_ml), 1)
    latest = _latest_valid_normality_day(milk_normality)
    reference = latest.get("yield_reference") if isinstance(latest.get("yield_reference"), dict) else {}
    if plan_type == PLAN_TYPE_INCREASE:
        p15 = reference.get("p15")
        lower_bound = current_daily_ml + INCREASE_MIN_DAILY_DELTA_ML
        target = float(p15) if isinstance(p15, (int, float)) and p15 > lower_bound else lower_bound
        p85 = reference.get("p85")
        if isinstance(p85, (int, float)) and float(p85) > current_daily_ml:
            target = min(target, float(p85))
        return round(max(target, lower_bound), 1)
    if plan_type == PLAN_TYPE_DECREASE:
        p85 = reference.get("p85")
        default_delta = _default_decrease_delta_ml(current_daily_ml=current_daily_ml, p85=p85)
        return round(max(current_daily_ml - default_delta, 0.0), 1)
    return round(current_daily_ml, 1)


def _target_daily_ml_from_options(
    *,
    plan_type: str,
    current_daily_ml: float,
    custom_target_daily_ml: float | None,
    options: dict[str, Any],
) -> float | None:
    if custom_target_daily_ml is not None:
        return custom_target_daily_ml
    if plan_type != PLAN_TYPE_DECREASE:
        return None
    for key in ("decrease_delta_ml", "target_delta_ml", "delta_ml", "daily_decrease_ml"):
        raw = options.get(key)
        delta = _optional_float(raw)
        if delta is not None and delta > 0:
            return round(max(current_daily_ml - delta, 0.0), 1)
    return None


def _default_decrease_delta_ml(*, current_daily_ml: float, p85: Any = None) -> float:
    if current_daily_ml <= 0:
        return 0.0
    if isinstance(p85, (int, float)) and 0 <= float(p85) < current_daily_ml:
        return min(max(current_daily_ml - float(p85), DECREASE_MIN_DAILY_DELTA_ML), current_daily_ml)
    return min(max(DECREASE_DEFAULT_DAILY_DELTA_ML, DECREASE_MIN_DAILY_DELTA_ML), current_daily_ml)


def _requested_decrease_delta_ml(
    *,
    current_daily_ml: float,
    target_daily_ml: float | None,
    delta_ml: float | None,
) -> float | None:
    if delta_ml is not None:
        delta = _optional_float(delta_ml)
        return round(delta, 1) if delta is not None else None
    if target_daily_ml is not None:
        target = _optional_float(target_daily_ml)
        return round(current_daily_ml - target, 1) if target is not None else None
    return None


def _latest_valid_normality_day(milk_normality: dict[str, Any]) -> dict[str, Any]:
    days = milk_normality.get("days") if isinstance(milk_normality.get("days"), list) else []
    valid = [item for item in days if isinstance(item, dict) and item.get("ok") is True]
    if not valid:
        return {}
    return sorted(valid, key=lambda item: norm_text(item.get("date")))[-1]


def _daily_event_count(summary: dict[str, Any], *, window_days: int) -> int:
    count = to_int(summary.get("count"), 0)
    if count <= 0:
        return 0
    return max(int(round(count / max(window_days, 1))), 1)


def _daily_calendar_pump_task_count(summary: dict[str, Any], *, window_days: int) -> int:
    completed = to_int(summary.get("completed_pump_task_count"), 0)
    total = to_int(summary.get("pump_task_count"), 0)
    count = completed if completed > 0 else total
    if count <= 0:
        return 0
    return max(int(round(count / max(window_days, 1))), 1)


def _current_frequency(pumping_summary: dict[str, Any], feeding_summary: dict[str, Any], *, window_days: int) -> int:
    pumping_count = _daily_event_count(pumping_summary, window_days=window_days)
    direct_breastfeeding_count = _daily_direct_breastfeeding_count(feeding_summary, window_days=window_days)
    return max(pumping_count + direct_breastfeeding_count, pumping_count, 0)


def _daily_direct_breastfeeding_count(feeding_summary: dict[str, Any], *, window_days: int) -> int:
    type_counts = feeding_summary.get("type_counts") if isinstance(feeding_summary.get("type_counts"), dict) else {}
    direct_count = 0
    for feed_type, count in type_counts.items():
        if _is_direct_breastfeeding_type(feed_type):
            direct_count += to_int(count, 0)
    if direct_count <= 0:
        return 0
    return max(int(round(direct_count / max(window_days, 1))), 1)


def _plan_rules(
    *,
    plan_type: str,
    current_daily_ml: float,
    target_daily_ml: float,
    current_frequency: int,
    pumping_count: int,
    milk_normality: dict[str, Any],
    infant_age_months: Any = None,
) -> dict[str, Any]:
    milk_status = norm_text(milk_normality.get("overall_status"))
    latest_day = _latest_valid_normality_day(milk_normality)
    reference = latest_day.get("yield_reference") if isinstance(latest_day.get("yield_reference"), dict) else {}
    if plan_type == PLAN_TYPE_INCREASE:
        gap_ml = max(target_daily_ml - current_daily_ml, 0.0)
        if current_frequency >= 10:
            target_frequency = current_frequency
            require_pp = False
            needs_referral = True
        elif gap_ml <= INCREASE_SMALL_GAP_LIMIT_ML and current_frequency < INCREASE_LOW_FREQUENCY_TARGET:
            target_frequency = INCREASE_LOW_FREQUENCY_TARGET
            require_pp = False
            needs_referral = False
        elif gap_ml > INCREASE_SMALL_GAP_LIMIT_ML or INCREASE_LOW_FREQUENCY_TARGET <= current_frequency < INCREASE_HIGHER_FREQUENCY_TARGET:
            target_frequency = INCREASE_HIGHER_FREQUENCY_TARGET
            require_pp = True
            needs_referral = False
        else:
            target_frequency = max(current_frequency, INCREASE_LOW_FREQUENCY_TARGET)
            require_pp = False
            needs_referral = False
        add_count = max(target_frequency - current_frequency, 0)
        desired_pumping_count = max(pumping_count + add_count, pumping_count + 1, 1)
        add_count = max(desired_pumping_count - pumping_count, 0)
        target_frequency = max(target_frequency, current_frequency + add_count)
        return {
            "target_frequency": target_frequency,
            "add_count": add_count,
            "current_pumping_count": pumping_count,
            "desired_pumping_count": desired_pumping_count,
            "require_pp": require_pp,
            "needs_referral": needs_referral,
            "max_interval_hours": 5,
            "milk_status": milk_status,
            "reference": reference,
            "target_bounds": {
                "current_daily_ml": current_daily_ml,
                "min_target_daily_ml": round(current_daily_ml + INCREASE_MIN_DAILY_DELTA_ML, 1),
                "max_target_daily_ml": float(reference["p85"]) if isinstance(reference.get("p85"), (int, float)) else None,
            },
        }

    if plan_type == PLAN_TYPE_DECREASE:
        interval_days = _derive_decrease_interval_days(current_frequency, infant_age_months=infant_age_months)
        target_count = max(pumping_count - 1, 0) if pumping_count > 0 else 4
        max_target_daily_ml = round(max(current_daily_ml - 50.0, 0.0), 1)
        decrease_delta_ml = round(max(current_daily_ml - target_daily_ml, 0.0), 1)
        strategy = _decrease_strategy_key(current_frequency=current_frequency, infant_age_months=infant_age_months)
        return {
            "target_frequency": max(current_frequency - 1, 0),
            "current_pumping_count": pumping_count,
            "desired_pumping_count": max(target_count, 1),
            "strategy_interval_days": interval_days,
            "medical_confirmation_required": current_frequency >= 12,
            "decrease_strategy": strategy,
            "target_delta_ml": decrease_delta_ml,
            "milk_status": milk_status,
            "reference": reference,
            "target_bounds": {
                "current_daily_ml": current_daily_ml,
                "min_target_daily_ml": 0.0,
                "max_target_daily_ml": max_target_daily_ml,
                "min_decrease_delta_ml": DECREASE_MIN_DAILY_DELTA_ML,
                "max_decrease_delta_ml": current_daily_ml,
            },
            "stage_targets": _build_decrease_stage_targets(
                current_pumping_count=pumping_count,
                plan_days=_resolve_plan_days(
                    plan_type=PLAN_TYPE_DECREASE,
                    requested_days=None,
                    current_frequency=current_frequency,
                    current_pumping_count=pumping_count,
                    infant_age_months=infant_age_months,
                ),
                interval_days=interval_days,
            ),
        }

    desired = pumping_count if pumping_count > 0 else 5
    return {
        "target_frequency": current_frequency,
        "current_pumping_count": pumping_count,
        "desired_pumping_count": desired,
        "checkpoint_days": [3, 7],
        "milk_status": milk_status,
        "reference": reference,
    }


def _derive_decrease_interval_days(current_frequency: int, *, infant_age_months: Any = None) -> int:
    if current_frequency >= 12:
        return 7
    age_months = _optional_float(infant_age_months)
    if current_frequency < 3:
        return 2
    if age_months is not None and age_months >= 10 and current_frequency < 5:
        return 3
    if current_frequency < 5:
        return 7
    return 7


def _decrease_strategy_key(*, current_frequency: int, infant_age_months: Any = None) -> str:
    if current_frequency >= 12:
        return "high_frequency_medical_check"
    if current_frequency < 3:
        return "very_low_frequency_reduce_every_2_days"
    age_months = _optional_float(infant_age_months)
    if age_months is not None and age_months >= 10 and current_frequency < 5:
        return "older_infant_low_frequency_reduce_every_3_days"
    return "standard_reduce_every_7_days"


def _daily_schedule_templates(
    plan_type: str,
    days: int,
    schedule_items: list[dict[str, Any]],
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    if days <= 0:
        return []
    if plan_type == PLAN_TYPE_INCREASE:
        if bool(rules.get("require_pp")) and days > 7:
            day_1_7_items = schedule_items
            day_8_items = [_regularize_pp_item(item) for item in schedule_items]
            return [
                {"day_start": 1, "day_end": 7, "phase_label": "increase_pp_first_7_days", "items": day_1_7_items},
                {"day_start": 8, "day_end": days, "phase_label": "increase_regular_after_day_7", "items": day_8_items},
            ]
        return [{"day_start": 1, "day_end": days, "phase_label": "increase", "items": schedule_items}]

    if plan_type == PLAN_TYPE_DECREASE:
        interval = max(to_int(rules.get("strategy_interval_days"), 7), 1)
        stage_targets = rules.get("stage_targets") if isinstance(rules.get("stage_targets"), list) else []
        if stage_targets:
            return _decrease_templates_from_stage_targets(schedule_items, stage_targets)

        templates: list[dict[str, Any]] = []
        current_items = list(schedule_items)
        start = 1
        stage = 1
        while start <= days and current_items:
            end = min(start + interval - 1, days)
            templates.append(
                {
                    "day_start": start,
                    "day_end": end,
                    "phase_label": f"decrease_stage_{stage}_count_{len(current_items)}",
                    "items": current_items,
                }
            )
            if end >= days:
                break
            current_items = _remove_one_pump(current_items)
            start = end + 1
            stage += 1
        return templates or [{"day_start": 1, "day_end": days, "phase_label": "decrease", "items": schedule_items}]

    return [{"day_start": 1, "day_end": days, "phase_label": "maintain_yesterday_rhythm", "items": schedule_items}]


def _decrease_templates_from_stage_targets(
    base_schedule_items: list[dict[str, Any]],
    stage_targets: list[Any],
) -> list[dict[str, Any]]:
    current_items = _sort_schedule_items(base_schedule_items)
    templates: list[dict[str, Any]] = []
    for index, raw_stage in enumerate(stage_targets, start=1):
        if not isinstance(raw_stage, dict):
            continue
        target_count = max(to_int(raw_stage.get("target_pumping_count"), len(current_items)), 0)
        guidance_only = to_bool(raw_stage.get("guidance_only")) or target_count <= 0
        while len(current_items) > target_count:
            remove_idx = _select_reduction_index(current_items)
            if remove_idx is None:
                break
            current_items.pop(remove_idx)
        stage_items = [] if guidance_only else [dict(item) for item in current_items]
        templates.append(
            {
                "day_start": max(to_int(raw_stage.get("day_start"), 1), 1),
                "day_end": max(to_int(raw_stage.get("day_end"), 1), 1),
                "phase_label": f"decrease_stage_{to_int(raw_stage.get('stage_no'), index)}_count_{len(stage_items)}",
                "items": stage_items,
                "guidance_only": guidance_only,
                "guidance": "本阶段不安排固定吸奶任务，仅按舒适度少量移出缓解，不追求排空，并继续观察胀痛、硬块和宝宝摄入。",
            }
        )
    return templates


def _daily_targets(days: int, current_daily_ml: float, target_daily_ml: float) -> list[dict[str, Any]]:
    if days <= 0:
        return []
    if days == 1:
        return [{"day": 1, "target_daily_ml": target_daily_ml}]
    targets = []
    for day in range(1, days + 1):
        ratio = (day - 1) / max(days - 1, 1)
        targets.append({"day": day, "target_daily_ml": round(current_daily_ml + (target_daily_ml - current_daily_ml) * ratio, 1)})
    return targets


def _fill_evenly_spaced(times: list[str], desired_count: int) -> list[str]:
    desired = max(desired_count, 0)
    if desired <= 0:
        return []

    selected = _unique_sorted_times(times)
    if len(selected) >= desired:
        return selected[:desired]

    if not selected:
        selected = _evenly_spaced_daytime_times(desired)
        if len(selected) >= desired:
            return selected[:desired]

    for min_gap in (PLAN_MIN_PUMP_GAP_MINUTES, 45, 30, 0):
        while len(selected) < desired:
            candidate = _best_daytime_candidate(selected, min_gap_minutes=min_gap)
            if not candidate:
                break
            selected.append(candidate)
            selected = _unique_sorted_times(selected)
        if len(selected) >= desired:
            break

    interval = max(60, int(round(1440 / max(desired, 1))))
    offset = DAYTIME_START_MINUTE if desired >= 4 else 420
    occupied = list(selected)
    for index in range(desired * 4):
        if len(selected) >= desired:
            break
        candidate = _minute_to_hhmm(offset + index * interval)
        if candidate in selected:
            continue
        candidate_minute = _minute_of_day(candidate)
        if candidate_minute is None:
            continue
        if any(_minutes_distance(candidate_minute, _minute_of_day(item) or 0) < 60 for item in occupied):
            continue
        selected.append(candidate)
        occupied.append(candidate)

    if len(selected) < desired:
        for index in range(desired * 4):
            if len(selected) >= desired:
                break
            candidate = _minute_to_hhmm(offset + index * interval)
            if candidate not in selected:
                selected.append(candidate)

    return _unique_sorted_times(selected)[:desired]


def _prefer_daytime_times(times: list[str], *, desired_count: int) -> list[str]:
    desired = max(to_int(desired_count, 0), 0)
    selected = _unique_sorted_times(times)
    if desired <= 0:
        desired = len(selected)
    if desired <= 0:
        return []
    if len(selected) > desired:
        selected = _trim_to_count(selected, desired)
    if len(selected) < desired:
        selected = _fill_evenly_spaced(selected, desired)
    selected = _replace_excess_night_times(
        selected,
        max_night_count=MAX_NIGHT_PUMP_TASKS,
        min_gap_minutes=PLAN_MIN_PUMP_GAP_MINUTES,
    )
    if len(selected) < desired:
        selected = _fill_evenly_spaced(selected, desired)
    if len(selected) > desired:
        selected = _trim_to_count(selected, desired)
    return _unique_sorted_times(selected)[:desired]


def _replace_excess_night_times(
    times: list[str],
    *,
    max_night_count: int,
    min_gap_minutes: int,
) -> list[str]:
    selected = _unique_sorted_times(times)
    night_times = [time for time in selected if _is_night_time_text(time)]
    excess_count = max(len(night_times) - max(max_night_count, 0), 0)
    if excess_count <= 0:
        return selected

    keep_night = set(sorted(night_times, key=_night_keep_score)[: max(max_night_count, 0)])
    for time in [item for item in night_times if item not in keep_night]:
        without_time = [item for item in selected if item != time]
        replacement = ""
        for gap in (min_gap_minutes, 45, 30, 0):
            replacement = _best_daytime_candidate(without_time, min_gap_minutes=gap)
            if replacement:
                break
        if replacement:
            selected = _unique_sorted_times([*without_time, replacement])
    return selected


def _evenly_spaced_daytime_times(desired_count: int) -> list[str]:
    desired = max(desired_count, 0)
    if desired <= 0:
        return []
    if desired == 1:
        return [_minute_to_hhmm((DAYTIME_START_MINUTE + DAYTIME_END_MINUTE) // 2)]

    span = DAYTIME_END_MINUTE - DAYTIME_START_MINUTE - DAYTIME_STEP_MINUTES
    times: list[str] = []
    for index in range(desired):
        raw_minute = DAYTIME_START_MINUTE + round((span * index) / max(desired - 1, 1))
        minute = _round_to_step(raw_minute, DAYTIME_STEP_MINUTES)
        minute = min(max(minute, DAYTIME_START_MINUTE), DAYTIME_END_MINUTE - DAYTIME_STEP_MINUTES)
        times.append(_minute_to_hhmm(minute))
    return _unique_sorted_times(times)


def _best_daytime_candidate(occupied_times: list[str], *, min_gap_minutes: int) -> str:
    occupied_minutes = [
        minute
        for minute in (_minute_of_day(time) for time in _unique_sorted_times(occupied_times))
        if minute is not None
    ]
    occupied = set(_unique_sorted_times(occupied_times))
    midpoint = (DAYTIME_START_MINUTE + DAYTIME_END_MINUTE) // 2
    candidates: list[tuple[int, int, int, str]] = []
    for minute in range(DAYTIME_START_MINUTE, DAYTIME_END_MINUTE, DAYTIME_STEP_MINUTES):
        candidate = _minute_to_hhmm(minute)
        if candidate in occupied:
            continue
        nearest_gap = (
            min(_minutes_distance(minute, occupied_minute) for occupied_minute in occupied_minutes)
            if occupied_minutes
            else DAYTIME_END_MINUTE - DAYTIME_START_MINUTE
        )
        if nearest_gap < min_gap_minutes:
            continue
        center_score = -abs(minute - midpoint)
        candidates.append((nearest_gap, center_score, -minute, candidate))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][3]


def _first_daytime_item_index(items: list[dict[str, Any]]) -> int:
    for index, item in enumerate(items):
        minute = _minute_of_day(norm_text(item.get("time") or item.get("time_point")))
        if minute is not None and not _is_night_time(minute):
            return index
    return 0


def _trim_to_count(times: list[str], desired_count: int) -> list[str]:
    desired = max(desired_count, 0)
    selected = _unique_sorted_times(times)
    if desired <= 0:
        return []
    while len(selected) > desired:
        items = [{"time": time} for time in selected]
        remove_idx = _select_reduction_index(items)
        if remove_idx is None:
            remove_idx = len(selected) - 1
        selected.pop(remove_idx)
    return selected


def _regularize_pp_item(item: dict[str, Any]) -> dict[str, Any]:
    if norm_text(item.get("kind")) != "pp":
        return dict(item)
    return {
        **item,
        "kind": "regular",
        "calendar_title": "吸奶",
        "action": "双侧同时常规吸奶15分钟，并记录本次奶量",
        "duration_minutes": INCREASE_REGULAR_PUMP_MINUTES,
    }


def _remove_one_pump(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(items) <= 1:
        return list(items)
    indexed = [(idx, _minute_of_day(norm_text(item.get("time")))) for idx, item in enumerate(items)]
    indexed = [(idx, minute) for idx, minute in indexed if minute is not None]
    if not indexed:
        return items[:-1]
    remove_idx = _select_reduction_index(items)
    if remove_idx is None:
        middle = len(items) // 2
        remove_idx = min(indexed, key=lambda pair: abs(pair[0] - middle))[0]
    return [item for idx, item in enumerate(items) if idx != remove_idx]


def _select_reduction_index(items: list[dict[str, Any]]) -> int | None:
    if len(items) <= 1:
        return None
    ranked: list[tuple[int, int, int, int]] = []
    total = len(items)
    for idx, item in enumerate(items):
        current_min = _minute_of_day(norm_text(item.get("time")))
        prev_min = _minute_of_day(norm_text(items[idx - 1].get("time")))
        next_min = _minute_of_day(norm_text(items[(idx + 1) % total].get("time")))
        if current_min is None or prev_min is None or next_min is None:
            continue
        gap_prev = (current_min - prev_min) % 1440
        gap_next = (next_min - current_min) % 1440
        nearest_gap = min(gap_prev, gap_next)
        night_priority = 0 if _is_night_time(current_min) else 1
        ranked.append((night_priority, nearest_gap, current_min, idx))
    if not ranked:
        return None
    ranked.sort()
    return int(ranked[0][3])


def _is_night_time(minute_of_day: int) -> bool:
    return minute_of_day < DAYTIME_START_MINUTE or minute_of_day >= DAYTIME_END_MINUTE


def _is_night_time_text(time_text: str) -> bool:
    minute = _minute_of_day(time_text)
    return minute is not None and _is_night_time(minute)


def _night_keep_score(time_text: str) -> tuple[int, int]:
    minute = _minute_of_day(time_text)
    if minute is None:
        return (1440, 1)
    if minute >= DAYTIME_END_MINUTE:
        return (minute - DAYTIME_END_MINUTE, 0)
    return (DAYTIME_START_MINUTE - minute, 1)


def _round_to_step(minute: int, step_minutes: int) -> int:
    step = max(step_minutes, 1)
    return int(round(minute / step) * step)


def _plan_rule_notes(plan_type: str, rules: dict[str, Any]) -> list[str]:
    if plan_type == PLAN_TYPE_INCREASE:
        notes = ["优先保证可执行性，新增频次不要造成明显疲惫。", "每次吸奶后记录奶量，连续 3 天后复盘。"]
        if rules.get("breast_fullness_without_red_flags"):
            notes.insert(0, "目前主要是胀或感觉没排空，且没有发热、红肿、硬块加重等信号；计划会把单次吸奶或亲喂效果和结束标准一起考虑。")
        if rules.get("require_pp"):
            notes.insert(0, "如身体允许，可在本阶段安排一次加强吸奶；复盘后再决定是否继续。")
        if rules.get("needs_referral"):
            notes.append("当前频次已较高，如仍明显担忧奶量，建议考虑 IBCLC 支持。")
        return notes
    if plan_type == PLAN_TYPE_DECREASE:
        notes = [
            "每个阶段只减少一个吸奶点或一小段时长。",
            "每次不要追求排空；若胀满不适，只少量移出到舒适即可。",
            "减少后可持续冷敷缓解胀痛，并记录硬块、疼痛和奶量变化。",
            "若出现胀痛、硬块、发烧或明显不适，暂停减量并寻求专业帮助。",
        ]
        strategy = norm_text(rules.get("decrease_strategy"))
        if strategy == "high_frequency_medical_check":
            notes.insert(0, "当前频次较高，减奶前需先排除高泌乳素血症等病理因素。")
        if strategy == "standard_reduce_every_7_days":
            notes.append("若宝宝未满10月龄，优先减少每次吸奶时长，避免亲喂后再额外用吸奶器排空。")
        if strategy == "older_infant_low_frequency_reduce_every_3_days":
            notes.append("宝宝已满10月龄且频次较低时，可每3天减少1次，但仍以妈妈舒适度为准。")
        return notes
    return ["沿用近期可执行节奏，重点保持稳定记录。", "第3天和第7天复盘奶量、宝宝表现和妈妈舒适度。"]


def _plan_control_strategy(plan_type: str, rules: dict[str, Any]) -> dict[str, Any]:
    hard_stop = "如果发热、寒战、乳房红肿热痛扩大、疼痛明显加重，或宝宝尿布/精神/体重让人担心，先暂停自动调整，联系医生或 IBCLC。"
    if plan_type == PLAN_TYPE_INCREASE:
        if rules.get("breast_fullness_without_red_flags"):
            return {
                "why_this_way": "这版会把奶量偏低和吸完还胀一起看，先改善每次吸奶或亲喂的效果，再小步调整节奏。",
                "schedule_focus": "优先把 24 小时里的吸奶/亲喂安排得更均匀，避免很长空档，同时不把单次拖得太久。",
                "session_goal": "每次结束吸奶时看是否还有胀感，如果有的话可以多吸一会儿（1～2分钟）直到胀感减轻或消失。",
                "when_to_stop_each_time": "吸奶过程中如果有明显痛感，暂停吸奶并联系医生或IBCLC顾问，我可以帮你在线接通IBCLC顾问。",
                "how_to_adjust": "一次只改一件事，先调单次吸奶或亲喂效果和最长间隔；连续 2-3 天后再看平均变化。",
                "review_timing": "",
                "hard_stop": hard_stop,
            }
        return {
            "why_this_way": "这版先做小幅调整，重点是减少太长的间隔，不靠把少数几次吸得很久来增加负担。",
            "schedule_focus": "优先把 24 小时里的吸奶/亲喂安排得更均匀，少留很长空档。",
            "session_goal": "每次结束吸奶时看是否还有胀感，如果有的话可以多吸一会儿（1～2分钟）直到胀感减轻或消失。",
            "when_to_stop_each_time": "吸奶过程中如果有明显痛感，暂停吸奶并联系医生或IBCLC顾问，我可以帮你在线接通IBCLC顾问。",
            "how_to_adjust": "一次只改一件事，先加一次或缩短最长间隔，连续 2-3 天后看平均变化。",
            "review_timing": "",
            "hard_stop": hard_stop,
        }
    if plan_type == PLAN_TYPE_DECREASE:
        return {
            "why_this_way": "这版先减少过强刺激，让身体慢慢降下来，不突然大幅停掉。",
            "schedule_focus": "先取消不必要的额外吸奶；容易堵奶时，先少取一点或缩短单次时间，再慢慢拉长间隔或合并场次。",
            "session_goal": "每次只吸到舒服，不追求排得很空。",
            "when_to_stop_each_time": "胀得难受时少量移出到舒服就停，不继续给身体很强的增奶信号。",
            "how_to_adjust": f"每 {to_int(rules.get('strategy_interval_days'), 7)} 天作为一个小阶段；一次只减一个吸奶点或一小段时长。",
            "review_timing": "每 2-3 天看胀痛、硬块、总量、宝宝摄入和睡眠，再决定要不要进入下一步。",
            "hard_stop": hard_stop,
        }
    return {
        "why_this_way": "这版目标是稳住供需，不追求最大产量，也不突然减少。",
        "schedule_focus": "让吸奶/亲喂节奏尽量贴近宝宝真实需求，避免忽多忽少。",
        "session_goal": "每次稳定、舒服地移出即可。",
        "when_to_stop_each_time": "吸奶到奶流明显变慢、乳房舒服一些就可以；亲喂就看宝宝吞咽和满足感。",
        "how_to_adjust": "一次只改一件事；如果作息变化，先调时间，再看是否需要调次数。",
        "review_timing": "第 3 天和第 7 天复盘奶量、宝宝表现和妈妈舒适度。",
        "hard_stop": hard_stop,
    }


def _review_note(plan_type: str) -> str:
    if plan_type == PLAN_TYPE_DECREASE:
        return "每完成一个阶段后复盘胀痛、硬块、宝宝摄入和睡眠情况。"
    if plan_type == PLAN_TYPE_INCREASE:
        return "连续执行 3 天后复盘总奶量、吸奶次数和宝宝有效摄入信号。"
    return "第3天和第7天复盘执行情况。"


def _repeat_note(plan_type: str, days: int, rules: dict[str, Any]) -> str:
    if plan_type == PLAN_TYPE_INCREASE and rules.get("require_pp") and days > 7:
        return "本阶段保留加强吸奶安排，复盘后再决定是否继续或改回常规吸奶。"
    if plan_type == PLAN_TYPE_DECREASE:
        return f"每 {to_int(rules.get('strategy_interval_days'), 7)} 天作为一个阶段，确认舒适后再进入下一阶段；不排空，必要时冷敷。"
    return "按同一时间表执行，按复盘结果微调。"


def _plan_summary(plan_type: str, current_daily_ml: float, target_daily_ml: float, days: int) -> str:
    label = _plan_title(plan_type)
    return f"生成{days}天{label}：当前参考日奶量约 {current_daily_ml} ml，目标约 {target_daily_ml} ml。"


def _plan_title(plan_type: str) -> str:
    if plan_type == PLAN_TYPE_INCREASE:
        return "追奶计划"
    if plan_type == PLAN_TYPE_DECREASE:
        return "减奶计划"
    return "稳奶计划"


def _watch_items(plan_type: str) -> list[str]:
    base = ["记录每次吸奶量", "观察宝宝尿布、精神状态和进食表现", "如有疼痛、发烧或红肿加重，及时寻求专业帮助"]
    if plan_type == PLAN_TYPE_DECREASE:
        return ["不要过快减少吸奶频次", *base]
    if plan_type == PLAN_TYPE_INCREASE:
        return ["优先保证可执行，不要因计划造成明显疲惫", *base]
    return base


def _resolve_plan_days(
    *,
    plan_type: str,
    requested_days: int | None,
    current_frequency: int,
    current_pumping_count: int,
    infant_age_months: Any,
) -> int:
    if requested_days is not None:
        upper = DECREASE_MAX_PLAN_DAYS if plan_type == PLAN_TYPE_DECREASE else INCREASE_DEFAULT_PLAN_DAYS
        return min(max(to_int(requested_days, 7), 1), upper)
    if plan_type == PLAN_TYPE_INCREASE:
        return INCREASE_DEFAULT_PLAN_DAYS
    if plan_type != PLAN_TYPE_DECREASE:
        return 7

    interval = _derive_decrease_interval_days(current_frequency, infant_age_months=infant_age_months)
    if current_frequency >= 12:
        return DECREASE_MAX_PLAN_DAYS
    if current_frequency < 3:
        return min(max(interval * max(current_pumping_count, 1), interval), DECREASE_MAX_PLAN_DAYS)
    if current_pumping_count >= 4:
        return DECREASE_MAX_PLAN_DAYS
    if current_pumping_count >= 1:
        return min(max(current_pumping_count * interval, interval), DECREASE_MAX_PLAN_DAYS)
    return min(max(interval, 1), DECREASE_MAX_PLAN_DAYS)


def _build_decrease_stage_targets(
    *,
    current_pumping_count: int,
    plan_days: int,
    interval_days: int,
) -> list[dict[str, Any]]:
    stage_span = max(interval_days, 1)
    total_days = max(plan_days, 1)
    targets: list[dict[str, Any]] = []
    day_start = 1
    stage_no = 1
    while day_start <= total_days:
        day_end = min(day_start + stage_span - 1, total_days)
        target_count = max(current_pumping_count - stage_no, 0)
        targets.append(
            {
                "stage_no": stage_no,
                "day_start": day_start,
                "day_end": day_end,
                "target_pumping_count": target_count,
                "guidance_only": target_count == 0,
            }
        )
        stage_no += 1
        day_start = day_end + 1
    return targets


def _checkpoint_days(days: int, rules: dict[str, Any]) -> list[int]:
    raw = rules.get("checkpoint_days")
    if isinstance(raw, list):
        points = [to_int(item, 0) for item in raw]
    else:
        interval = to_int(rules.get("strategy_interval_days"), 0)
        points = list(range(interval, days + 1, interval)) if interval > 0 else [3, 7]
    points = [day for day in points if 0 < day <= days]
    if days > 0 and days not in points:
        points.append(days)
    return sorted(set(points))


def _public_plan_generation_context(plan_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": plan_context.get("source"),
        "target_day": plan_context.get("target_day"),
        "schedule_basis": plan_context.get("schedule_basis") if isinstance(plan_context.get("schedule_basis"), dict) else {},
        "pumping_times": _list_from_context(plan_context, "pumping_times"),
        "calendar_pump_times": _list_from_context(plan_context, "calendar_pump_times"),
        "completed_calendar_pump_times": _list_from_context(plan_context, "completed_calendar_pump_times"),
        "measured_pumping_times": _list_from_context(plan_context, "measured_pumping_times"),
        "breastfeeding_times": _list_from_context(plan_context, "breastfeeding_times"),
        "recent_pumping_times": _list_from_context(plan_context, "recent_pumping_times"),
        "recent_milk_rhythm": _public_recent_milk_rhythm_context(plan_context.get("recent_milk_rhythm")),
        "infant_age_months": plan_context.get("infant_age_months"),
    }


def _public_recent_milk_rhythm_context(value: Any) -> dict[str, Any]:
    rhythm = value if isinstance(value, dict) else {}
    summary = rhythm.get("summary") if isinstance(rhythm.get("summary"), dict) else {}
    selected_day = rhythm.get("selected_day") if isinstance(rhythm.get("selected_day"), dict) else {}
    days = rhythm.get("days") if isinstance(rhythm.get("days"), list) else []
    return _drop_empty_dict(
        {
            "summary": _drop_empty_dict(
                {
                    "basis_date": summary.get("basis_date"),
                    "confidence": summary.get("confidence"),
                    "usable_for_schedule": summary.get("usable_for_schedule"),
                    "ask_daily_counts": summary.get("ask_daily_counts"),
                    "ask_missing_records": summary.get("ask_missing_records"),
                    "basis_summary": summary.get("basis_summary"),
                    "average_pumping_count_per_day": summary.get("average_pumping_count_per_day"),
                    "average_nursing_count_per_day": summary.get("average_nursing_count_per_day"),
                    "typical_pumping_times": summary.get("typical_pumping_times"),
                    "typical_nursing_times": summary.get("typical_nursing_times"),
                    "longest_gap_hours": summary.get("longest_gap_hours"),
                }
            ),
            "selected_day": _public_recent_milk_day(selected_day),
            "days": [_public_recent_milk_day(day) for day in days if isinstance(day, dict)],
        }
    )


def _public_recent_milk_day(day: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty_dict(
        {
            "date": day.get("date"),
            "completeness": day.get("completeness"),
            "planned_pumping_times": day.get("planned_pumping_times"),
            "planned_nursing_times": day.get("planned_nursing_times"),
            "actual_pumping": _public_recent_events(day.get("actual_pumping"), include_amount=True, include_duration=True),
            "actual_nursing_times": day.get("actual_nursing_times"),
            "breastmilk_bottle": _public_recent_events(day.get("breastmilk_bottle"), include_amount=True),
            "formula_bottle": _public_recent_events(day.get("formula_bottle"), include_amount=True),
            "all_times": day.get("all_times"),
        }
    )


def _public_recent_events(value: Any, *, include_amount: bool = False, include_duration: bool = False) -> list[dict[str, Any]]:
    events = value if isinstance(value, list) else []
    compact = []
    for item in events:
        if not isinstance(item, dict):
            continue
        event = {"time": item.get("time")}
        if include_amount:
            event["milk_ml"] = item.get("milk_ml")
        if include_duration:
            event["duration_minutes"] = item.get("duration_minutes")
        compact.append(_drop_empty_dict(event))
    return compact


def _plan_observation_context(
    *,
    pumping_summary: dict[str, Any],
    feeding_summary: dict[str, Any],
    calendar_task_summary: dict[str, Any],
    plan_context: dict[str, Any],
    window_days: int,
) -> dict[str, Any]:
    calendar_pump_tasks_per_day = max(
        _daily_calendar_pump_task_count(calendar_task_summary, window_days=window_days),
        len(_list_from_context(plan_context, "calendar_pump_times")),
    )
    return {
        "window_days": max(window_days, 1),
        "schedule_basis": plan_context.get("schedule_basis") if isinstance(plan_context.get("schedule_basis"), dict) else {},
        "calendar_pump_tasks_per_day": calendar_pump_tasks_per_day,
        "completed_calendar_pump_task_count": to_int(calendar_task_summary.get("completed_pump_task_count"), 0),
        "recorded_pumping_logs_per_day": _daily_event_count(pumping_summary, window_days=window_days),
        "recorded_pumping_log_count": to_int(pumping_summary.get("count"), 0),
        "feeding_log_count": to_int(feeding_summary.get("count"), 0),
        "records_and_backfills_per_day": round(
            (to_int(pumping_summary.get("count"), 0) + to_int(feeding_summary.get("count"), 0))
            / max(window_days, 1),
            1,
        ),
    }


def _derive_increase_schedule_requirements(
    *,
    gap_ml: float,
    current_frequency: int,
    current_pumping_count: int,
) -> dict[str, Any]:
    if current_frequency >= 10:
        return {
            "target_frequency": current_frequency,
            "add_count": 0,
            "desired_pumping_count": max(current_pumping_count, 0),
            "require_pp": False,
            "needs_referral": True,
        }
    if gap_ml <= INCREASE_SMALL_GAP_LIMIT_ML and current_frequency < INCREASE_LOW_FREQUENCY_TARGET:
        target_frequency = INCREASE_LOW_FREQUENCY_TARGET
        require_pp = False
    elif gap_ml > INCREASE_SMALL_GAP_LIMIT_ML or INCREASE_LOW_FREQUENCY_TARGET <= current_frequency < INCREASE_HIGHER_FREQUENCY_TARGET:
        target_frequency = INCREASE_HIGHER_FREQUENCY_TARGET
        require_pp = True
    else:
        target_frequency = max(current_frequency, INCREASE_LOW_FREQUENCY_TARGET)
        require_pp = False
    add_count = max(target_frequency - current_frequency, 0)
    desired_pumping_count = max(current_pumping_count + add_count, current_pumping_count + 1, 1)
    add_count = max(desired_pumping_count - current_pumping_count, 0)
    target_frequency = max(target_frequency, current_frequency + add_count)
    return {
        "target_frequency": target_frequency,
        "add_count": add_count,
        "desired_pumping_count": desired_pumping_count,
        "require_pp": require_pp,
        "needs_referral": False,
    }


def _parse_options(options: dict[str, Any] | str | None) -> dict[str, Any]:
    if options is None:
        return {}
    if isinstance(options, dict):
        return dict(options)
    try:
        parsed = json.loads(options)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _maternal_symptoms_from_options(options: dict[str, Any]) -> dict[str, Any]:
    value = options.get("maternal_symptoms")
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _has_breast_fullness_without_red_flags(maternal_symptoms: dict[str, Any]) -> bool:
    if not _has_breast_fullness_signal(maternal_symptoms):
        return False
    return not any(
        to_bool(maternal_symptoms.get(key))
        for key in ("fever", "chills", "breast_redness", "lump_or_hard_area", "worsening_pain")
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


def _prepared_data_from_options(options: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = options.get(key)
        if not isinstance(value, dict):
            continue
        data = value.get("data") if isinstance(value.get("data"), dict) else value
        if isinstance(data, dict) and data:
            return dict(data)
    return {}


def _merge_times(primary: list[str], fallback: list[str]) -> list[str]:
    return sorted(set([*primary, *fallback]))


def _list_from_context(context: dict[str, Any], key: str) -> list[str]:
    value = context.get(key)
    if not isinstance(value, list):
        return []
    return [norm_text(item) for item in value if norm_text(item)]


def _drop_empty_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in ("", None, [], {})
    }


def _trim_or_fill_times(times: list[str], desired_count: int) -> list[str]:
    desired = max(to_int(desired_count, 0), 0)
    if desired <= 0:
        return _unique_sorted_times(times)
    selected = _unique_sorted_times(times)
    if len(selected) > desired:
        return _trim_to_count(selected, desired)
    if len(selected) < desired:
        return _fill_evenly_spaced(selected, desired)
    return selected


def _build_circular_gap_windows(event_times: list[str]) -> list[dict[str, Any]]:
    times = _unique_sorted_times(event_times)
    if not times:
        return [{"start": "00:00", "end": "24:00", "duration_min": 1440, "suggested_time": "12:00"}]
    minutes = [_minute_of_day(item) or 0 for item in times]
    windows: list[dict[str, Any]] = []
    for index, start_minute in enumerate(minutes):
        end_minute = minutes[(index + 1) % len(minutes)]
        if index == len(minutes) - 1:
            end_minute += 1440
        gap = max(end_minute - start_minute, 0)
        windows.append(
            {
                "start": _minute_to_hhmm(start_minute),
                "end": _minute_to_hhmm(end_minute),
                "duration_min": gap,
                "suggested_time": _minute_to_hhmm(start_minute + gap // 2),
            }
        )
    return sorted(windows, key=lambda item: to_int(item.get("duration_min"), 0), reverse=True)


def _pick_insert_time_from_window(window: dict[str, Any], occupied_times: list[str]) -> str | None:
    start_minute = _minute_of_day(norm_text(window.get("start")))
    end_minute = _minute_of_day(norm_text(window.get("end")))
    duration = to_int(window.get("duration_min"), 0)
    if start_minute is None or duration <= 1:
        return None
    if end_minute is None:
        end_minute = start_minute + duration
    if end_minute <= start_minute:
        end_minute += 1440
    target = start_minute + duration // 2
    occupied = set(_unique_sorted_times(occupied_times))
    offsets = [0]
    for step in range(15, max(duration, 16), 15):
        offsets.extend([-step, step])
    for offset in offsets:
        candidate_minute = target + offset
        if candidate_minute <= start_minute or candidate_minute >= end_minute:
            continue
        candidate = _minute_to_hhmm(candidate_minute)
        if candidate not in occupied:
            return candidate
    return None


def _candidate_is_spaced(candidate: str, occupied_times: list[str], min_distance_min: int) -> bool:
    candidate_minute = _minute_of_day(candidate)
    if candidate_minute is None:
        return False
    for occupied in _unique_sorted_times(occupied_times):
        occupied_minute = _minute_of_day(occupied)
        if occupied_minute is None:
            continue
        if _minutes_distance(candidate_minute, occupied_minute) < min_distance_min:
            return False
    return True


def _select_spaced_times(times: list[str], *, min_gap_minutes: int, limit: int) -> list[str]:
    selected: list[str] = []
    for time in _unique_sorted_times(times):
        minute = _minute_of_day(time)
        if minute is None:
            continue
        if any(_minutes_distance(minute, existing) < min_gap_minutes for existing in [_minute_of_day(item) or 0 for item in selected]):
            continue
        selected.append(time)
        if len(selected) >= limit:
            break
    return selected or _unique_sorted_times(times)[:limit]


def _unique_sorted_times(times: list[str]) -> list[str]:
    valid = []
    for time in times:
        minute = _minute_of_day(norm_text(time))
        if minute is not None:
            valid.append((minute, f"{minute // 60:02d}:{minute % 60:02d}"))
    return [item[1] for item in sorted(set(valid))]


def _sort_schedule_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in items:
        time_text = _hhmm_from_value(item.get("time") or item.get("time_point"))
        if not time_text:
            continue
        normalized.append({**item, "time": time_text})
    return sorted(normalized, key=lambda item: _minute_of_day(norm_text(item.get("time"))) or 0)


def _dedupe_events_by_time(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for event in events:
        time_text = _hhmm_from_value(event.get("time"))
        if not time_text:
            continue
        payload = dict(event)
        payload["time"] = time_text
        if time_text not in merged:
            merged[time_text] = payload
            continue
        merged[time_text]["milk_ml"] = round(_to_float(merged[time_text].get("milk_ml")) + _to_float(payload.get("milk_ml")), 1)
    return [merged[key] for key in _unique_sorted_times(list(merged.keys()))]


def _minutes_distance(a: int, b: int) -> int:
    diff = abs((a - b) % 1440)
    return min(diff, 1440 - diff)


def _minute_to_hhmm(minute: int) -> str:
    normalized = minute % 1440
    return f"{normalized // 60:02d}:{normalized % 60:02d}"


def _minute_of_day(time_text: str) -> int | None:
    parsed = parse_datetime(time_text)
    if parsed is None:
        return None
    return parsed.hour * 60 + parsed.minute


def _hhmm_from_value(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def _date_from_value(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    return parsed.date().isoformat()


def _calendar_task_finished(value: Any) -> bool:
    token = norm_text(value).lower()
    return token in {"true", "1", "yes", "done", "completed", "已完成", "完成"}


def _datetime_for_date(target_date: str, time_text: str) -> str:
    parsed = parse_datetime(time_text)
    if parsed is None:
        return ""
    return f"{target_date} {parsed.hour:02d}:{parsed.minute:02d}:00"


def _add_minutes(time_text: str, minutes: int) -> str:
    parsed = parse_datetime(time_text)
    if parsed is None:
        return time_text
    return (parsed + timedelta(minutes=minutes)).strftime("%H:%M")


def _db_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_direct_breastfeeding_type(value: Any) -> bool:
    token = norm_text(value).lower()
    return token in {"direct", "breastfeeding", "breast", "母乳亲喂"} or "亲喂" in token


def _infant_age_months(infants: list[dict[str, Any]], as_of_date: date) -> float | None:
    birth_dates = []
    for infant in infants:
        parsed = parse_datetime(infant.get("birth_date"))
        if parsed is not None:
            birth_dates.append(parsed.date())
    if not birth_dates:
        return None
    age_days = max((as_of_date - min(birth_dates)).days, 0)
    return round(age_days / 30.4375, 2)
