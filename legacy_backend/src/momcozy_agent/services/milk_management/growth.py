from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .db import fetch_all, get_knowledge_root
from .schemas import ServiceResult, error_result, norm_text, ok_result, parse_datetime, to_int

STATUS_NORMAL = "normal"
STATUS_SLOW = "slow"
STATUS_FAST = "fast"
STATUS_UNKNOWN = "insufficient_data"
STALE_DAYS_THRESHOLD = 31

# Trajectory thresholds (in percentile points).
TRAJECTORY_DROP_FLAG = 15.0  # crossing roughly one major reference band
TRAJECTORY_DROP_SIGNIFICANT = 25.0  # crossing ~two reference bands


def evaluate_infant_growth(
    *,
    user_id: str,
    infant_id: int | None = None,
    as_of_time: str | None = None,
) -> ServiceResult:
    """Evaluate infant growth with WHO percentile reference data."""

    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法执行生长发育评估。")

    as_of_dt = _naive_datetime(parse_datetime(as_of_time)) if as_of_time else datetime.now()
    if as_of_dt is None:
        return error_result("invalid_as_of_time", "as_of_time 无效。")

    profiles = _infant_profiles(uid, infant_id=infant_id)
    if not profiles:
        return error_result(
            "infant_profile_not_found",
            "未查询到可评估的宝宝档案。",
            data={"user_id": uid, "infants": []},
        )

    growth_index = _build_growth_index(str(_growth_reference_path()))
    infant_results = []
    for profile in profiles:
        result = _evaluate_one_infant(uid, profile, as_of_dt, growth_index)
        infant_results.append(result)

    infant_results.sort(key=_result_sort_key, reverse=True)
    selected = infant_results[0] if infant_results else {}
    selected_status = norm_text(selected.get("status")) or STATUS_UNKNOWN
    selected_name = norm_text(selected.get("infant_name")) or "宝宝"
    summary = norm_text(selected.get("summary")) or "暂无可评估结果。"
    trajectory = selected.get("weight_trajectory") if isinstance(selected.get("weight_trajectory"), dict) else None
    trajectory_status = norm_text((trajectory or {}).get("status"))
    return ok_result(
        "infant_growth_evaluated",
        f"{selected_name}：{summary}",
        {
            "user_id": uid,
            "as_of_time": _db_time(as_of_dt),
            "status": selected_status,
            "trajectory_status": trajectory_status or None,
            "selected_infant_id": selected.get("infant_id"),
            "infants": infant_results,
        },
    )


def _infant_profiles(user_id: str, *, infant_id: int | None) -> list[dict[str, Any]]:
    params: list[Any] = [user_id]
    clauses = ["user_id = ?"]
    if infant_id is not None:
        clauses.append("infant_id = ?")
        params.append(int(infant_id))
    return fetch_all(
        f"""
        SELECT infant_id, user_id, user_nickname, infant_name, sex, birth_date, updated_at, created_at
        FROM infant_profile
        WHERE {" AND ".join(clauses)}
        ORDER BY infant_id ASC
        """,
        params,
    )


def _growth_logs(user_id: str, infant_id: int, as_of_dt: datetime) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT growth_id, user_id, infant_id, height_cm, weight_kg,
               height_measured_at, weight_measured_at, head_cm, head_measured_at, created_at
        FROM infant_growth_log
        WHERE user_id = ?
          AND infant_id = ?
          AND (
                height_measured_at <= ?
             OR weight_measured_at <= ?
             OR created_at <= ?
          )
        ORDER BY COALESCE(weight_measured_at, height_measured_at, created_at) DESC
        """,
        (user_id, infant_id, _db_time(as_of_dt), _db_time(as_of_dt), _db_time(as_of_dt)),
    )


def _evaluate_one_infant(
    user_id: str,
    profile: dict[str, Any],
    as_of_dt: datetime,
    growth_index: dict[tuple[str, str], list[tuple[float, dict[str, Any]]]],
) -> dict[str, Any]:
    current_infant_id = int(profile.get("infant_id") or 0)
    infant_name = norm_text(profile.get("infant_name")) or f"宝宝{current_infant_id}"
    logs = _growth_logs(user_id, current_infant_id, as_of_dt)
    if not logs:
        return {
            "infant_id": current_infant_id,
            "infant_name": infant_name,
            "status": STATUS_UNKNOWN,
            "summary": "暂无生长发育记录，无法评估。",
            "latest_growth": None,
        }

    height_cm, height_dt = _select_latest_metric(logs, "height_cm", "height_measured_at", as_of_dt)
    weight_kg, weight_dt = _select_latest_metric(logs, "weight_kg", "weight_measured_at", as_of_dt)
    if height_cm is None and weight_kg is None:
        return {
            "infant_id": current_infant_id,
            "infant_name": infant_name,
            "status": STATUS_UNKNOWN,
            "summary": "暂无可用身高/体重记录，无法评估。",
            "latest_growth": None,
        }

    ref_dt = max([dt for dt in [height_dt, weight_dt] if dt is not None], default=as_of_dt)
    sex = _normalize_sex(profile.get("sex"))
    height_detail = _build_metric_detail(
        indicator="lhfa",
        value=height_cm,
        sex=sex,
        age_months=_age_months_at(profile.get("birth_date"), height_dt or ref_dt),
        growth_index=growth_index,
    )
    weight_detail = _build_metric_detail(
        indicator="wfa",
        value=weight_kg,
        sex=sex,
        age_months=_age_months_at(profile.get("birth_date"), weight_dt or ref_dt),
        growth_index=growth_index,
    )
    bmi_value = round(weight_kg / ((height_cm / 100.0) ** 2), 2) if height_cm and weight_kg else None
    bmi_detail = _build_metric_detail(
        indicator="bmi",
        value=bmi_value,
        sex=sex,
        age_months=_age_months_at(profile.get("birth_date"), ref_dt),
        growth_index=growth_index,
    )
    weight_history = _compute_weight_history(
        logs=logs,
        profile=profile,
        sex=sex,
        growth_index=growth_index,
        as_of_dt=as_of_dt,
    )
    weight_trajectory = _summarize_weight_trajectory(weight_history)

    final_status, used_bmi_tiebreaker = _final_growth_status(
        norm_text(height_detail.get("status")),
        norm_text(weight_detail.get("status")),
        norm_text(bmi_detail.get("status")),
    )
    # Even when the latest absolute weight still sits in the WHO normal band
    # (P3-P95), a clear percentile drop across the recent 4-8 weeks is a
    # textbook pediatric red flag. Surface it on the top-level status and
    # summary so consumers (agent, charts) don't miss it.
    trajectory_status = norm_text(weight_trajectory.get("status"))
    if trajectory_status in {"drop", "significant_drop"} and final_status != STATUS_SLOW:
        final_status = STATUS_SLOW

    summary = _growth_summary(final_status, trajectory=weight_trajectory)
    height_days = _days_since(height_dt, as_of_dt)
    weight_days = _days_since(weight_dt, as_of_dt)
    return {
        "infant_id": current_infant_id,
        "infant_name": infant_name,
        "status": final_status,
        "summary": summary,
        "sex": sex,
        "birth_date": norm_text(profile.get("birth_date")),
        "age_months": _age_months_at(profile.get("birth_date"), ref_dt),
        "latest_growth": {
            "height_cm": height_cm,
            "weight_kg": weight_kg,
            "height_measured_at": _db_time(height_dt) if height_dt is not None else "",
            "weight_measured_at": _db_time(weight_dt) if weight_dt is not None else "",
            "reference_time": _db_time(ref_dt),
            "height_days_since": height_days,
            "weight_days_since": weight_days,
            "stale_threshold_days": STALE_DAYS_THRESHOLD,
            "height_stale": height_days is None or int(height_days) > STALE_DAYS_THRESHOLD,
            "weight_stale": weight_days is None or int(weight_days) > STALE_DAYS_THRESHOLD,
        },
        "indicators": {
            "height_lhfa": height_detail,
            "weight_wfa": weight_detail,
            "bmi": bmi_detail,
        },
        "weight_history": weight_history,
        "weight_trajectory": weight_trajectory,
        "used_bmi_tiebreaker": used_bmi_tiebreaker,
    }


def _growth_reference_path() -> Path:
    return get_knowledge_root() / "infant_growth_info" / "merged_growth_reference_full.json"


@lru_cache(maxsize=4)
def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("growth reference json must be object")
    return data


@lru_cache(maxsize=4)
def _build_growth_index(path: str) -> dict[tuple[str, str], list[tuple[float, dict[str, Any]]]]:
    records = _load_json(path).get("records", [])
    index: dict[tuple[str, str], list[tuple[float, dict[str, Any]]]] = {}
    for item in records if isinstance(records, list) else []:
        if not isinstance(item, dict):
            continue
        indicator = norm_text(item.get("indicator")).lower()
        if indicator not in {"wfa", "lhfa", "bmi"}:
            continue
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        try:
            month = float(values.get("Month"))
        except (TypeError, ValueError):
            continue
        index.setdefault((indicator, _normalize_sex(item.get("sex"))), []).append((month, item))
    for key in index:
        index[key] = sorted(index[key], key=lambda pair: pair[0])
    return index


def _build_metric_detail(
    *,
    indicator: str,
    value: float | None,
    sex: str,
    age_months: float | None,
    growth_index: dict[tuple[str, str], list[tuple[float, dict[str, Any]]]],
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "indicator": indicator,
        "value": value,
        "status": "unknown",
        "band": "unknown",
        "estimated_percentile": None,
        "reference_month": None,
        "ref_lower": None,
        "ref_upper": None,
    }
    if value is None or age_months is None:
        return detail
    ref_row = _nearest_reference(growth_index, indicator, sex, age_months)
    values = ref_row.get("values") if isinstance(ref_row, dict) and isinstance(ref_row.get("values"), dict) else {}
    band = _estimate_percentile(float(value), _collect_percentile_points(values))
    percentile = band.get("estimated_percentile")
    detail.update(
        {
            "status": _percentile_status(percentile if isinstance(percentile, (int, float)) else None),
            "band": band.get("band"),
            "estimated_percentile": percentile,
            "reference_month": values.get("Month"),
            "ref_lower": band.get("ref_lower"),
            "ref_upper": band.get("ref_upper"),
        }
    )
    return detail


def _collect_percentile_points(values: dict[str, Any]) -> list[tuple[float, float]]:
    points = []
    for key, value in values.items():
        if not isinstance(key, str) or not key.startswith("P"):
            continue
        try:
            percentile = float(key[1:])
            metric_value = float(value)
        except (TypeError, ValueError):
            continue
        if percentile > 100:
            percentile = percentile / 10.0
        points.append((percentile, metric_value))
    return sorted(points, key=lambda pair: pair[0])


def _estimate_percentile(measured: float, points: list[tuple[float, float]]) -> dict[str, Any]:
    if not points:
        return {"band": "unknown", "estimated_percentile": None, "ref_lower": None, "ref_upper": None}
    first_p, first_v = points[0]
    last_p, last_v = points[-1]
    if measured <= first_v:
        return {"band": f"<P{int(first_p)}", "estimated_percentile": round(first_p, 1), "ref_lower": None, "ref_upper": first_v}
    if measured >= last_v:
        return {"band": f">P{int(last_p)}", "estimated_percentile": round(last_p, 1), "ref_lower": last_v, "ref_upper": None}
    for idx in range(len(points) - 1):
        low_p, low_v = points[idx]
        high_p, high_v = points[idx + 1]
        if low_v <= measured <= high_v:
            ratio = 1.0 if abs(high_v - low_v) < 1e-9 else (measured - low_v) / (high_v - low_v)
            percentile = low_p + ratio * (high_p - low_p)
            return {
                "band": f"P{int(low_p)}-P{int(high_p)}",
                "estimated_percentile": round(percentile, 1),
                "ref_lower": low_v,
                "ref_upper": high_v,
            }
    return {"band": "unknown", "estimated_percentile": None, "ref_lower": None, "ref_upper": None}


def _nearest_reference(
    growth_index: dict[tuple[str, str], list[tuple[float, dict[str, Any]]]],
    indicator: str,
    sex: str,
    age_months: float,
) -> dict[str, Any] | None:
    rows = growth_index.get((indicator, sex), [])
    if not rows:
        return None
    return min(rows, key=lambda pair: abs(pair[0] - age_months))[1]


def _select_latest_metric(
    rows: list[dict[str, Any]],
    value_key: str,
    time_key: str,
    as_of_time: datetime,
) -> tuple[float | None, datetime | None]:
    latest_value = None
    latest_dt = None
    for row in rows:
        value = _safe_float(row.get(value_key))
        metric_dt = _naive_datetime(parse_datetime(row.get(time_key)) or parse_datetime(row.get("created_at")))
        if value is None or metric_dt is None or metric_dt > as_of_time:
            continue
        if latest_dt is None or metric_dt > latest_dt:
            latest_value = value
            latest_dt = metric_dt
    return latest_value, latest_dt


def _final_growth_status(height_status: str, weight_status: str, bmi_status: str) -> tuple[str, bool]:
    has_slow = STATUS_SLOW in {height_status, weight_status}
    has_fast = STATUS_FAST in {height_status, weight_status}
    has_normal = STATUS_NORMAL in {height_status, weight_status}
    if has_slow and has_fast:
        if bmi_status in {STATUS_SLOW, STATUS_FAST, STATUS_NORMAL}:
            return bmi_status, True
        return STATUS_UNKNOWN, True
    if has_slow and not has_fast:
        return STATUS_SLOW, False
    if has_fast and not has_slow:
        return STATUS_FAST, False
    if has_normal:
        return STATUS_NORMAL, False
    return STATUS_UNKNOWN, False


def _percentile_status(percentile: float | None) -> str:
    if percentile is None:
        return "unknown"
    if percentile < 3:
        return STATUS_SLOW
    if percentile > 95:
        return STATUS_FAST
    return STATUS_NORMAL


def _growth_summary(status: str, *, trajectory: dict[str, Any] | None = None) -> str:
    trajectory_status = norm_text((trajectory or {}).get("status"))
    drop = norm_text((trajectory or {}).get("percentile_drop_text"))
    if trajectory_status == "significant_drop":
        return (
            f"体重曲线在最近 {(trajectory or {}).get('window_weeks', '?')} 周内出现明显下沉"
            f"（{drop}），属于生长红旗，建议尽快与儿科沟通；当前最新测量仍在 WHO 标准带内时也要重视趋势。"
        )
    if trajectory_status == "drop":
        return (
            f"体重曲线在最近 {(trajectory or {}).get('window_weeks', '?')} 周内有明显的百分位下降"
            f"（{drop}），目前虽未跌出标准范围，但建议在下次产检与儿科再确认一次。"
        )
    if status == STATUS_NORMAL:
        return "身高/体重处于正常区间（3-95百分位），并且最近几周百分位变化稳定。"
    if status == STATUS_SLOW:
        return "当前评估倾向生长缓慢（低于3百分位或最近百分位明显下沉）。"
    if status == STATUS_FAST:
        return "当前评估倾向生长过快（高于95百分位）。"
    return "当前数据不足，无法给出稳定结论。"


def _compute_weight_history(
    *,
    logs: list[dict[str, Any]],
    profile: dict[str, Any],
    sex: str,
    growth_index: dict[tuple[str, str], list[tuple[float, dict[str, Any]]]],
    as_of_dt: datetime,
) -> list[dict[str, Any]]:
    """Return chronological weight measurements with WHO weight-for-age
    percentile estimates, so callers can detect trajectory shifts that the
    single-latest snapshot cannot reveal."""

    birth_date = profile.get("birth_date")
    history: list[dict[str, Any]] = []
    for row in logs:
        weight = _safe_float(row.get("weight_kg"))
        measured_dt = _naive_datetime(
            parse_datetime(row.get("weight_measured_at")) or parse_datetime(row.get("created_at"))
        )
        if weight is None or measured_dt is None or measured_dt > as_of_dt:
            continue
        age_months = _age_months_at(birth_date, measured_dt)
        detail = _build_metric_detail(
            indicator="wfa",
            value=weight,
            sex=sex,
            age_months=age_months,
            growth_index=growth_index,
        )
        history.append(
            {
                "measured_at": _db_time(measured_dt),
                "weight_kg": weight,
                "age_months": age_months,
                "estimated_percentile": detail.get("estimated_percentile"),
                "band": detail.get("band"),
                "ref_lower": detail.get("ref_lower"),
                "ref_upper": detail.get("ref_upper"),
            }
        )
    history.sort(key=lambda item: norm_text(item.get("measured_at")))
    return history


def _summarize_weight_trajectory(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Detect a downward percentile shift over the most recent ~6-8 weeks.

    `status` values: `stable`, `drop`, `significant_drop`, `rise`,
    `insufficient_data`. The drop classification mirrors a clinically
    common red flag — crossing one (P15-point shift) or two
    (P25-point shift) WHO reference bands downward over a few weeks.
    """

    points = [
        item
        for item in history
        if isinstance(item.get("estimated_percentile"), (int, float))
    ]
    if len(points) < 2:
        return {
            "status": "insufficient_data",
            "current_percentile": points[-1].get("estimated_percentile") if points else None,
            "baseline_percentile": None,
            "percentile_change": None,
            "percentile_drop_text": None,
            "window_weeks": None,
            "compared_at": None,
            "current_at": points[-1].get("measured_at") if points else None,
        }

    latest = points[-1]
    current_percentile = float(latest["estimated_percentile"])
    current_dt = _naive_datetime(parse_datetime(latest.get("measured_at")))

    # Prefer baseline that is at least ~4 weeks old; fall back to oldest.
    baseline = points[0]
    if current_dt is not None:
        candidate = None
        for item in points[:-1]:
            measured_dt = _naive_datetime(parse_datetime(item.get("measured_at")))
            if measured_dt is None:
                continue
            weeks = (current_dt - measured_dt).days / 7.0
            if weeks >= 4.0:
                candidate = item
        if candidate is not None:
            baseline = candidate

    baseline_percentile = float(baseline.get("estimated_percentile") or 0.0)
    baseline_dt = _naive_datetime(parse_datetime(baseline.get("measured_at")))
    weeks_span: float | None = None
    if current_dt is not None and baseline_dt is not None:
        weeks_span = round(max((current_dt - baseline_dt).days / 7.0, 0.0), 1)

    change = round(current_percentile - baseline_percentile, 1)
    if change <= -TRAJECTORY_DROP_SIGNIFICANT:
        status = "significant_drop"
    elif change <= -TRAJECTORY_DROP_FLAG:
        status = "drop"
    elif change >= TRAJECTORY_DROP_FLAG:
        status = "rise"
    else:
        status = "stable"

    drop_text = None
    if status in {"drop", "significant_drop"}:
        drop_text = (
            f"P{baseline_percentile:.0f} → P{current_percentile:.0f}"
        )

    return {
        "status": status,
        "current_percentile": current_percentile,
        "baseline_percentile": baseline_percentile,
        "percentile_change": change,
        "percentile_drop_text": drop_text,
        "window_weeks": weeks_span,
        "compared_at": baseline.get("measured_at"),
        "current_at": latest.get("measured_at"),
    }


def _normalize_sex(sex: Any) -> str:
    token = norm_text(sex).lower()
    if token in {"girls", "girl", "female", "f", "女", "女宝", "女孩"}:
        return "girls"
    return "boys"


def _age_months_at(birth_date: Any, ref_dt: datetime) -> float | None:
    birth_dt = _naive_datetime(parse_datetime(birth_date))
    if birth_dt is None:
        return None
    days = max((ref_dt.date() - birth_dt.date()).days, 0)
    return round(days / 30.4375, 2)


def _days_since(ref_dt: datetime | None, as_of_dt: datetime) -> int | None:
    if ref_dt is None:
        return None
    return max((as_of_dt.date() - ref_dt.date()).days, 0)


def _result_sort_key(item: dict[str, Any]) -> float:
    latest = item.get("latest_growth") if isinstance(item.get("latest_growth"), dict) else {}
    ref_dt = _naive_datetime(parse_datetime(latest.get("reference_time")))
    return ref_dt.timestamp() if ref_dt is not None else 0.0


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _db_time(value: datetime) -> str:
    return _naive_datetime(value).strftime("%Y-%m-%d %H:%M:%S")


def _naive_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)
