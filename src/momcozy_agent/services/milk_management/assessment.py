from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from math import floor
from typing import Any

from .db import fetch_all, fetch_one, get_knowledge_root
from .feeding import estimate_breastfeeding_milk, get_yesterday_feeding_snapshot
from .schemas import ServiceResult, error_result, norm_text, ok_result, parse_datetime, to_int


def evaluate_milk_status(
    *,
    user_id: str,
    as_of_time: str | None = None,
    window_days: int = 1,
    include_today: bool = False,
) -> ServiceResult:
    """Evaluate recent milk-management status from local SQLite data."""

    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法评估奶量。")

    as_of_dt = parse_datetime(as_of_time) if as_of_time else datetime.now()
    if as_of_dt is None:
        return error_result("invalid_as_of_time", "as_of_time 无效。")

    days = max(to_int(window_days, 1), 1)
    end_dt = as_of_dt if include_today else datetime.combine(as_of_dt.date(), datetime.min.time())
    start_dt = end_dt - timedelta(days=days)

    profile = fetch_one(
        """
        SELECT user_id, user_nickname, delivery_date, updated_at, created_at
        FROM user_profile
        WHERE user_id = ?
        """,
        (uid,),
    ) or {}
    infants = fetch_all(
        """
        SELECT infant_id, user_id, user_nickname, infant_name, sex, birth_date, updated_at, created_at
        FROM infant_profile
        WHERE user_id = ?
        ORDER BY infant_id ASC
        """,
        (uid,),
    )
    pumping_logs = fetch_all(
        """
        SELECT pumping_id, user_id, pump_start_time, pump_end_time, pump_milk_volum,
               pump_type, pump_milk_duration, created_at
        FROM pumping_log
        WHERE user_id = ?
          AND pump_start_time >= ?
          AND pump_start_time < ?
        ORDER BY pump_start_time ASC
        """,
        (uid, _db_time(start_dt), _db_time(end_dt)),
    )
    feeding_logs = fetch_all(
        """
        SELECT feeding_id, user_id, infant_id, feed_time, feed_milk_volum, feed_type, created_at
        FROM feeding_log
        WHERE user_id = ?
          AND feed_time >= ?
          AND feed_time < ?
        ORDER BY feed_time ASC
        """,
        (uid, _db_time(start_dt), _db_time(end_dt)),
    )

    pumping_summary = _summarize_pumping(pumping_logs)
    feeding_summary = _summarize_feeding(feeding_logs)
    yesterday_feeding_snapshot = (
        get_yesterday_feeding_snapshot(user_id=uid, as_of_time=_db_time(as_of_dt))
        if days == 1 and not include_today
        else None
    )
    missing_data = _missing_data(profile, infants, pumping_logs, feeding_logs)
    quick_24h_intake = _quick_24h_intake_requirements(
        pumping_summary=pumping_summary,
        feeding_summary=feeding_summary,
        yesterday_feeding_snapshot=yesterday_feeding_snapshot,
    )
    normality = _evaluate_milk_normality(
        profile=profile,
        pumping_logs=pumping_logs,
        feeding_logs=feeding_logs,
        as_of_dt=as_of_dt,
        window_days=days,
        include_today=include_today,
    )

    status = "ready"
    if "delivery_date" in missing_data or ("pumping_logs" in missing_data and "feeding_logs" in missing_data):
        status = "insufficient_data"
    elif normality.get("overall_status") in {"under_supply_alert", "over_supply_alert", "normal"}:
        status = norm_text(normality.get("overall_status"))

    summary = _summary_text(status, pumping_summary, feeding_summary, missing_data, normality)
    return ok_result(
        "milk_status_evaluated",
        summary,
        {
            "user_id": uid,
            "as_of_time": _db_time(as_of_dt),
            "window": {
                "start_at": _db_time(start_dt),
                "end_at": _db_time(end_dt),
                "window_days": days,
                "include_today": bool(include_today),
            },
            "profile": profile,
            "infants": infants,
            "pumping_summary": pumping_summary,
            "feeding_summary": feeding_summary,
            "yesterday_feeding_snapshot": yesterday_feeding_snapshot,
            "quick_24h_intake": quick_24h_intake,
            "milk_normality": normality,
            "missing_data": missing_data,
            "assessment_status": status,
        },
    )


def _summarize_pumping(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_ml = 0.0
    times: list[str] = []
    durations: list[int] = []
    for row in rows:
        total_ml += _float(row.get("pump_milk_volum"))
        time_text = _time_text(row.get("pump_start_time"))
        if time_text:
            times.append(time_text)
        duration = to_int(row.get("pump_milk_duration"), 0)
        if duration > 0:
            durations.append(duration)
    return {
        "count": len(rows),
        "total_ml": round(total_ml, 1),
        "times": times,
        "average_ml": round(total_ml / len(rows), 1) if rows else 0.0,
        "average_duration_minutes": round(sum(durations) / len(durations), 1) if durations else None,
    }


def _summarize_feeding(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_ml = 0.0
    type_counts: dict[str, int] = {}
    times: list[str] = []
    for row in rows:
        total_ml += _float(row.get("feed_milk_volum"))
        feed_type = norm_text(row.get("feed_type")) or "unknown"
        type_counts[feed_type] = type_counts.get(feed_type, 0) + 1
        time_text = _time_text(row.get("feed_time"))
        if time_text:
            times.append(time_text)
    return {
        "count": len(rows),
        "total_bottle_ml": round(total_ml, 1),
        "type_counts": type_counts,
        "times": times,
        "has_formula": any("奶粉" in key or "formula" in key.lower() for key in type_counts),
        "has_breastfeeding": any("亲喂" in key or "breast" in key.lower() for key in type_counts),
    }


def _evaluate_milk_normality(
    profile: dict[str, Any],
    pumping_logs: list[dict[str, Any]],
    feeding_logs: list[dict[str, Any]],
    as_of_dt: datetime,
    window_days: int,
    include_today: bool,
) -> dict[str, Any]:
    delivery = _date_from_value(profile.get("delivery_date"))
    if delivery is None:
        return {
            "ok": False,
            "overall_status": "insufficient_data",
            "summary": "missing delivery_date",
            "days": [],
        }

    end_day = as_of_dt.date() if include_today else as_of_dt.date() - timedelta(days=1)
    start_day = end_day - timedelta(days=max(window_days, 1) - 1)
    aggregate = _aggregate_days(pumping_logs, feeding_logs, start_day=start_day, end_day=end_day)
    # Per-session breastfeeding milk estimate is derived from the user's
    # bottle-feed history (same logic used by `pump_info()` / the trend chart).
    # When unavailable, fall back to 0 — equivalent to "pump only" supply.
    breastfeeding_per_session_ml = (
        estimate_breastfeeding_milk(
            user_id=norm_text(profile.get("user_id")),
            as_of_time=as_of_dt,
        )
        or 0.0
    )
    try:
        yield_points = _build_yield_points(str(_yield_reference_path()))
        freq_points = _build_frequency_points(str(_frequency_reference_path()))
    except Exception as exc:
        return {
            "ok": False,
            "overall_status": "insufficient_data",
            "summary": "missing milk reference data",
            "days": [],
            "missing_reference_data": type(exc).__name__,
        }

    per_day = []
    day_ptr = start_day
    while day_ptr <= end_day:
        key = day_ptr.isoformat()
        slot = aggregate.get(
            key,
            {
                "pumping_ml_total": 0.0,
                "pumping_count": 0,
                "breastfeeding_count": 0,
                "feeding_count_total": 0,
            },
        )
        postpartum_day = (day_ptr - delivery).days + 1
        if postpartum_day <= 0:
            per_day.append(
                {
                    "date": key,
                    "postpartum_day": postpartum_day,
                    "pumping_ml_total": round(float(slot["pumping_ml_total"]), 1),
                    "pumping_count": int(slot["pumping_count"]),
                    "breastfeeding_count": int(slot["breastfeeding_count"]),
                    "feeding_count_total": int(slot["feeding_count_total"]),
                    "frequency_reference": None,
                    "yield_reference": None,
                    "ok": False,
                    "status": "error",
                    "normal": None,
                    "rule_hit": "pre_delivery_day",
                    "estimated_frequency": None,
                    "estimated_daily_milk_ml": None,
                }
            )
            day_ptr += timedelta(days=1)
            continue

        frequency_reference = _interp_frequency_reference(postpartum_day, freq_points)
        yield_reference = _interp_yield_range(postpartum_day, yield_points) if postpartum_day <= 360 else None
        evaluated = _evaluate_day(
            day_postpartum=postpartum_day,
            pumping_ml_total=float(slot["pumping_ml_total"]),
            pumping_count=int(slot["pumping_count"]),
            breastfeeding_count=int(slot["breastfeeding_count"]),
            breastfeeding_per_session_ml=breastfeeding_per_session_ml,
            frequency_reference=frequency_reference,
            yield_reference=yield_reference,
        )
        per_day.append(
            {
                "date": key,
                "postpartum_day": postpartum_day,
                "pumping_ml_total": round(float(slot["pumping_ml_total"]), 1),
                "pumping_count": int(slot["pumping_count"]),
                "breastfeeding_count": int(slot["breastfeeding_count"]),
                "feeding_count_total": int(slot["feeding_count_total"]),
                "frequency_reference": frequency_reference,
                "yield_reference": yield_reference,
                **evaluated,
            }
        )
        day_ptr += timedelta(days=1)

    valid_days = [item for item in per_day if item.get("ok") is True]
    normal_days = [item for item in valid_days if item.get("normal") is True]
    abnormal_days = [item for item in valid_days if item.get("normal") is False]
    low_days = [item for item in abnormal_days if norm_text(item.get("status")) == "low"]
    high_days = [item for item in abnormal_days if norm_text(item.get("status")) == "high"]
    error_days = [item for item in per_day if item.get("ok") is not True]

    overall_status, summary = _overall_normality(valid_days, abnormal_days, low_days, high_days)
    return {
        "ok": True,
        "include_today": bool(include_today),
        "window_days": max(window_days, 1),
        "range": {
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
        },
        "delivery_date": delivery.isoformat(),
        "overall_status": overall_status,
        "summary": summary,
        "stats": {
            "total_days": len(per_day),
            "valid_days": len(valid_days),
            "normal_days": len(normal_days),
            "abnormal_days": len(abnormal_days),
            "error_days": len(error_days),
        },
        "days": per_day,
    }


def _aggregate_days(
    pumping_logs: list[dict[str, Any]],
    feeding_logs: list[dict[str, Any]],
    *,
    start_day: date,
    end_day: date,
) -> dict[str, dict[str, Any]]:
    aggregate: dict[str, dict[str, Any]] = {}
    for row in pumping_logs:
        try:
            pump_type = int(row.get("pump_type") if row.get("pump_type") is not None else 0)
        except (TypeError, ValueError):
            pump_type = 0
        if pump_type == 2:
            continue
        day = _date_from_value(row.get("pump_start_time"))
        if day is None or day < start_day or day > end_day:
            continue
        slot = _day_slot(aggregate, day)
        slot["pumping_ml_total"] += _float(row.get("pump_milk_volum"))
        slot["pumping_count"] += 1

    for row in feeding_logs:
        day = _date_from_value(row.get("feed_time"))
        if day is None or day < start_day or day > end_day:
            continue
        slot = _day_slot(aggregate, day)
        slot["feeding_count_total"] += 1
        if _is_breastfeeding_type(row.get("feed_type")):
            slot["breastfeeding_count"] += 1
    return aggregate


def _day_slot(aggregate: dict[str, dict[str, Any]], day: date) -> dict[str, Any]:
    return aggregate.setdefault(
        day.isoformat(),
        {
            "pumping_ml_total": 0.0,
            "pumping_count": 0,
            "breastfeeding_count": 0,
            "feeding_count_total": 0,
        },
    )


def _evaluate_day(
    *,
    day_postpartum: int,
    pumping_ml_total: float,
    pumping_count: int,
    breastfeeding_count: int,
    breastfeeding_per_session_ml: float,
    frequency_reference: dict[str, int],
    yield_reference: dict[str, float] | None,
) -> dict[str, Any]:
    if pumping_count <= 0:
        # No pump entries today; still surface direct-breastfeeding estimate
        # so callers don't see a vanished session as zero supply.
        estimated_breastfeeding_only = round(
            float(breastfeeding_count) * float(breastfeeding_per_session_ml), 1
        )
        return {
            "ok": False,
            "status": "insufficient_data",
            "normal": None,
            "rule_hit": "invalid_pumping_count_zero",
            "message": "当天没有可用于估算的吸奶记录。",
            "estimated_frequency": None,
            "estimated_daily_milk_ml": estimated_breastfeeding_only or None,
            "estimated_breastfeeding_ml": estimated_breastfeeding_only,
            "pumping_ml_total": 0.0,
            "breastfeeding_per_session_ml": round(float(breastfeeding_per_session_ml), 1),
        }

    # estimated_frequency is reported as the typical daily feeding frequency
    # for context; it is no longer used to derive estimated_daily_milk_ml,
    # because the old "pump_avg × total_frequency" formula systematically
    # over-counted combo-feeding moms (per-pump volume ≠ per-breastfeed
    # transfer) and routinely tripped a false over-supply alert.
    total_times = int(pumping_count) + int(breastfeeding_count)
    if breastfeeding_count <= 0:
        estimated_frequency = int(pumping_count)
        frequency_rule = "no_breastfeeding_use_pumping_count"
    elif total_times <= int(frequency_reference.get("p25", 0)):
        estimated_frequency = total_times
        frequency_rule = "within_or_below_p25_use_total_times"
    elif total_times > int(frequency_reference.get("p75", 0)):
        estimated_frequency = int(frequency_reference.get("p50", total_times))
        frequency_rule = "above_p75_use_p50"
    else:
        estimated_frequency = total_times
        frequency_rule = "between_p25_p75_use_total_times"

    # New formula: measured pump volume plus an estimate of direct
    # breastfeeding transfer (count × per-session estimate from bottle history,
    # mirroring how `pump_info()` builds `total_milk_estimate`).
    estimated_breastfeeding_ml = round(
        float(breastfeeding_count) * float(breastfeeding_per_session_ml), 1
    )
    estimated_daily_milk_ml = round(
        float(pumping_ml_total) + max(estimated_breastfeeding_ml, 0.0), 1
    )
    base = {
        "estimated_frequency": estimated_frequency,
        "estimated_daily_milk_ml": estimated_daily_milk_ml,
        "estimated_breastfeeding_ml": estimated_breastfeeding_ml,
        "pumping_ml_total": round(float(pumping_ml_total), 1),
        "breastfeeding_per_session_ml": round(float(breastfeeding_per_session_ml), 1),
        "frequency_rule": frequency_rule,
    }

    if estimated_daily_milk_ml > 1000:
        return {**base, "ok": True, "status": "high", "normal": False, "rule_hit": "special_over_supply_gt_1000", "message": "估算日奶量 > 1000ml。"}
    if day_postpartum == 4 and estimated_daily_milk_ml < 140:
        return {**base, "ok": True, "status": "low", "normal": False, "rule_hit": "special_day4_lt_140", "message": "产后第4天估算日奶量 < 140ml。"}
    if day_postpartum == 14 and estimated_daily_milk_ml < 500:
        return {**base, "ok": True, "status": "low", "normal": False, "rule_hit": "special_day14_lt_500", "message": "产后第14天估算日奶量 < 500ml。"}
    if 30 <= day_postpartum <= 180 and estimated_daily_milk_ml < 600:
        return {**base, "ok": True, "status": "low", "normal": False, "rule_hit": "special_day30_180_lt_600", "message": "产后30-180天估算日奶量 < 600ml。"}

    if day_postpartum <= 360:
        if not isinstance(yield_reference, dict):
            return {**base, "ok": False, "status": "error", "normal": None, "rule_hit": "yield_reference_missing", "message": "缺少奶量百分位参考。"}
        p15 = float(yield_reference.get("p15") or 0.0)
        p85 = float(yield_reference.get("p85") or 0.0)
        if estimated_daily_milk_ml < p15:
            return {**base, "ok": True, "status": "low", "normal": False, "rule_hit": "percentile_below_p15", "message": "低于 P15。"}
        if estimated_daily_milk_ml > p85:
            return {**base, "ok": True, "status": "high", "normal": False, "rule_hit": "percentile_above_p85", "message": "高于 P85。"}
        return {**base, "ok": True, "status": "normal", "normal": True, "rule_hit": "percentile_p15_to_p85", "message": "处于 P15-P85。"}

    if estimated_daily_milk_ml > 400:
        return {**base, "ok": True, "status": "normal", "normal": True, "rule_hit": "gt_360_day_ml_gt_400", "message": "产后超过360天且估算日奶量 > 400ml。"}
    return {**base, "ok": True, "status": "low", "normal": False, "rule_hit": "gt_360_day_ml_lte_400", "message": "产后超过360天且估算日奶量 <= 400ml。"}


def _overall_normality(
    valid_days: list[dict[str, Any]],
    abnormal_days: list[dict[str, Any]],
    low_days: list[dict[str, Any]],
    high_days: list[dict[str, Any]],
) -> tuple[str, str]:
    hit_codes = {norm_text(item.get("rule_hit")) for item in abnormal_days}
    if "special_over_supply_gt_1000" in hit_codes:
        return "over_supply_alert", "存在估算日奶量 > 1000ml 的高奶量提醒。"
    if {"special_day4_lt_140", "special_day14_lt_500", "special_day30_180_lt_600"} & hit_codes:
        return "under_supply_alert", "关键阶段奶量低于参考阈值。"
    if not valid_days:
        return "insufficient_data", "评估窗口内没有足够的有效日级数据。"
    if not abnormal_days:
        return "normal", "评估窗口内有效日期均处于参考范围。"
    if high_days and not low_days:
        return "over_supply_alert", "评估窗口内存在高于参考范围的日期。"
    if low_days and not high_days:
        return "under_supply_alert", "评估窗口内存在低于参考范围的日期。"
    latest_abnormal = sorted(abnormal_days, key=lambda item: norm_text(item.get("date")))[-1]
    if norm_text(latest_abnormal.get("status")) == "high":
        return "over_supply_alert", "奶量有波动，最近异常日期偏高。"
    return "under_supply_alert", "奶量有波动，最近异常日期偏低。"


def _missing_data(
    profile: dict[str, Any],
    infants: list[dict[str, Any]],
    pumping_logs: list[dict[str, Any]],
    feeding_logs: list[dict[str, Any]],
) -> list[str]:
    missing: list[str] = []
    if not profile:
        missing.append("user_profile")
    if not norm_text(profile.get("delivery_date")):
        missing.append("delivery_date")
    if not infants:
        missing.append("infant_profile")
    if not pumping_logs:
        missing.append("pumping_logs")
    if not feeding_logs:
        missing.append("feeding_logs")
    return missing


def _quick_24h_intake_requirements(
    *,
    pumping_summary: dict[str, Any],
    feeding_summary: dict[str, Any],
    yesterday_feeding_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(yesterday_feeding_snapshot, dict):
        return {
            "ready": True,
            "missing_fields": [],
            "follow_up_questions": [],
            "summary": "Not a 24h quick assessment.",
        }

    missing_fields = []
    follow_up_questions = []
    if to_int(pumping_summary.get("count"), 0) <= 0:
        missing_fields.extend(["yesterday_pumping_count", "yesterday_pumping_total_ml"])
        follow_up_questions.append("昨天吸奶几次？总吸奶量大约多少 ml？如果记得每次奶量，也请一起补充。")
    elif float(pumping_summary.get("total_ml") or 0.0) <= 0:
        missing_fields.append("yesterday_pumping_total_ml")
        follow_up_questions.append("昨天吸奶记录缺少奶量，总吸奶量大约多少 ml？")

    snapshot_missing = yesterday_feeding_snapshot.get("missing_fields")
    if isinstance(snapshot_missing, list):
        for field in snapshot_missing:
            token = norm_text(field)
            if token and token not in missing_fields:
                missing_fields.append(token)
    snapshot_questions = yesterday_feeding_snapshot.get("follow_up_questions")
    if isinstance(snapshot_questions, list):
        for question in snapshot_questions:
            token = norm_text(question)
            if token and token not in follow_up_questions:
                follow_up_questions.append(token)

    if to_int(feeding_summary.get("count"), 0) <= 0 and "yesterday_feeding_count" not in missing_fields:
        missing_fields.append("yesterday_feeding_count")
        follow_up_questions.append("昨天喂奶总次数大约是多少？")

    ready = len(missing_fields) == 0
    return {
        "ready": ready,
        "missing_fields": missing_fields,
        "follow_up_questions": follow_up_questions,
        "summary": (
            "24h 快速评估资料已足够。"
            if ready
            else "24h 快速评估缺少关键资料，请先补充昨日吸奶次数、吸奶奶量、喂奶次数或亲喂次数。"
        ),
    }


def _summary_text(
    status: str,
    pumping: dict[str, Any],
    feeding: dict[str, Any],
    missing: list[str],
    normality: dict[str, Any],
) -> str:
    if status == "insufficient_data":
        return "当前奶量评估数据不足，需要补充关键资料或近 24 小时记录。"
    normality_summary = norm_text(normality.get("summary"))
    return (
        f"窗口内共有 {pumping['count']} 次吸奶，记录奶量约 {pumping['total_ml']} ml；"
        f"喂养记录 {feeding['count']} 次。"
        + (f" {normality_summary}" if normality_summary else "")
        + (f" 缺失信息：{', '.join(missing)}。" if missing else "")
    )


def _yield_reference_path() -> Any:
    return get_knowledge_root() / "infant_milk_volum_info" / "milk_yield_percentiles_0_360.json"


def get_yield_reference_range(day_postpartum: int) -> dict[str, float] | None:
    """Return P15/P85 daily milk reference from the milk knowledge base."""

    if day_postpartum <= 0 or day_postpartum > 360:
        return None
    try:
        return _interp_yield_range(int(day_postpartum), _build_yield_points(str(_yield_reference_path())))
    except Exception:
        return None


def _frequency_reference_path() -> Any:
    return get_knowledge_root() / "infant_milk_volum_info" / "milk_pump_normal_times.json"


@lru_cache(maxsize=4)
def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"invalid json object: {path}")
    return data


@lru_cache(maxsize=4)
def _build_yield_points(path: str) -> list[tuple[int, float, float]]:
    records = _load_json(path).get("records", [])
    points = []
    for row in records if isinstance(records, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            points.append((int(row.get("day_postpartum")), float(row.get("p15")), float(row.get("p85"))))
        except (TypeError, ValueError):
            continue
    points.sort(key=lambda item: item[0])
    if not points:
        raise ValueError("milk_yield_percentiles json has no valid points")
    return points


@lru_cache(maxsize=4)
def _build_frequency_points(path: str) -> list[tuple[int, float, float, float]]:
    records = _load_json(path).get("records", [])
    points = []
    for row in records if isinstance(records, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            points.append((int(row.get("month_index")), float(row.get("p25")), float(row.get("p50")), float(row.get("p75"))))
        except (TypeError, ValueError):
            continue
    points.sort(key=lambda item: item[0])
    if not points:
        raise ValueError("milk_pump_normal_times json has no valid points")
    return points


def _interp_yield_range(day_postpartum: int, points: list[tuple[int, float, float]]) -> dict[str, float]:
    if day_postpartum <= points[0][0]:
        return {"p15": round(points[0][1], 1), "p85": round(points[0][2], 1)}
    if day_postpartum >= points[-1][0]:
        return {"p15": round(points[-1][1], 1), "p85": round(points[-1][2], 1)}
    for idx in range(len(points) - 1):
        d0, p15_0, p85_0 = points[idx]
        d1, p15_1, p85_1 = points[idx + 1]
        if d0 <= day_postpartum <= d1:
            return {
                "p15": round(_linear_interp(day_postpartum, d0, p15_0, d1, p15_1), 1),
                "p85": round(_linear_interp(day_postpartum, d0, p85_0, d1, p85_1), 1),
            }
    return {"p15": round(points[-1][1], 1), "p85": round(points[-1][2], 1)}


def _interp_frequency_reference(day_postpartum: int, points: list[tuple[int, float, float, float]]) -> dict[str, int]:
    month_value = max(0.0, float(day_postpartum) / 30.0)
    if month_value <= float(points[0][0]):
        point = points[0]
        return {"p25": _round_half_up(point[1]), "p50": _round_half_up(point[2]), "p75": _round_half_up(point[3])}
    if month_value >= float(points[-1][0]):
        point = points[-1]
        return {"p25": _round_half_up(point[1]), "p50": _round_half_up(point[2]), "p75": _round_half_up(point[3])}
    for idx in range(len(points) - 1):
        m0, p25_0, p50_0, p75_0 = points[idx]
        m1, p25_1, p50_1, p75_1 = points[idx + 1]
        if float(m0) <= month_value <= float(m1):
            return {
                "p25": max(1, _round_half_up(_linear_interp(month_value, m0, p25_0, m1, p25_1))),
                "p50": max(1, _round_half_up(_linear_interp(month_value, m0, p50_0, m1, p50_1))),
                "p75": max(1, _round_half_up(_linear_interp(month_value, m0, p75_0, m1, p75_1))),
            }
    point = points[-1]
    return {"p25": _round_half_up(point[1]), "p50": _round_half_up(point[2]), "p75": _round_half_up(point[3])}


def _linear_interp(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    if x1 == x0:
        return y1
    return y0 + (x - x0) / (x1 - x0) * (y1 - y0)


def _round_half_up(value: float) -> int:
    if value >= 0:
        return int(floor(value + 0.5))
    return int(-floor(abs(value) + 0.5))


def _is_breastfeeding_type(value: Any) -> bool:
    token = norm_text(value).lower()
    return "亲喂" in token or "breast" in token


def _date_from_value(value: Any) -> date | None:
    parsed = parse_datetime(value)
    return parsed.date() if parsed is not None else None


def _db_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _time_text(value: Any) -> str:
    parsed = parse_datetime(value)
    return parsed.strftime("%H:%M") if parsed else ""


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
