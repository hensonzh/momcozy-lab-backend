from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from .db import fetch_all, fetch_one, get_knowledge_root
from .schemas import parse_datetime


def estimate_breastfeeding_milk(*, user_id: str, as_of_time: Any = None) -> float | None:
    """Estimate direct breastfeeding milk from the user's bottle-feeding history."""

    uid = str(user_id or "").strip()
    if not uid:
        return None

    as_of_dt = parse_datetime(as_of_time) if as_of_time else datetime.now()
    if as_of_dt is None:
        as_of_dt = datetime.now()
    window_start = as_of_dt - timedelta(hours=24)

    recent_bottle_ml = _bottle_feed_volumes(
        user_id=uid,
        start_at=_db_time(window_start),
        end_at=_db_time(as_of_dt),
        limit=None,
    )
    if recent_bottle_ml:
        return round(sum(recent_bottle_ml) / len(recent_bottle_ml), 1)

    latest_bottle_ml = _bottle_feed_volumes(
        user_id=uid,
        start_at=None,
        end_at=_db_time(as_of_dt),
        limit=1,
    )
    if latest_bottle_ml:
        return round(latest_bottle_ml[0], 1)
    return None


def get_yesterday_feeding_snapshot(
    *,
    user_id: str,
    as_of_time: str | None = None,
) -> dict[str, Any]:
    """Return a previous-full-day feeding snapshot for quick plan intake."""

    uid = str(user_id or "").strip()
    if not uid:
        return _empty_snapshot(ok=False, summary="缺少 user_id。")

    as_of_dt = parse_datetime(as_of_time) if as_of_time else datetime.now()
    if as_of_dt is None:
        as_of_dt = datetime.now()
    day_end = datetime.combine(as_of_dt.date(), datetime.min.time())
    day_start = day_end - timedelta(days=1)

    infant = _load_infant(uid, None)
    infant_id = _to_int(infant.get("infant_id"))
    params: list[Any] = [uid, _db_time(day_start), _db_time(day_end)]
    infant_clause = ""
    if infant_id > 0:
        infant_clause = "AND infant_id = ?"
        params.append(infant_id)

    rows = fetch_all(
        f"""
        SELECT feeding_id, user_id, infant_id, feed_time, feed_milk_volum, feed_type, feeding_title, feed_action, created_at
        FROM feeding_log
        WHERE user_id = ?
          AND feed_time >= ?
          AND feed_time < ?
          {infant_clause}
        ORDER BY feed_time ASC
        """,
        params,
    )

    breastfeeding_count = 0
    bottle_count = 0
    bottle_formula_count = 0
    bottle_breast_count = 0
    bottle_unknown_count = 0
    bottle_ml_missing_count = 0
    bottle_total_ml = 0.0
    bottle_formula_ml = 0.0
    bottle_breast_ml = 0.0
    bottle_unknown_ml = 0.0

    for row in rows:
        feed_type = row.get("feed_type")
        milk_ml = _optional_float(row.get("feed_milk_volum"))
        if _is_breastfeeding(feed_type):
            breastfeeding_count += 1
            continue
        if not _is_any_bottle(feed_type):
            continue

        bottle_count += 1
        if milk_ml is None:
            bottle_ml_missing_count += 1
        else:
            bottle_total_ml += milk_ml

        if _is_formula_bottle(feed_type):
            bottle_formula_count += 1
            if milk_ml is not None:
                bottle_formula_ml += milk_ml
        elif _is_breastmilk_bottle(feed_type):
            bottle_breast_count += 1
            if milk_ml is not None:
                bottle_breast_ml += milk_ml
        else:
            bottle_unknown_count += 1
            if milk_ml is not None:
                bottle_unknown_ml += milk_ml

    missing_fields = []
    follow_up_questions = []
    if not rows:
        missing_fields.extend(["yesterday_feeding_count", "yesterday_breastfeeding_count", "yesterday_bottle_volume_ml"])
        follow_up_questions.append("昨天一共喂了几次？其中亲喂几次、瓶喂几次？")
        follow_up_questions.append("昨天瓶喂或补奶总量大约多少 ml？")
    else:
        if breastfeeding_count == 0:
            missing_fields.append("yesterday_breastfeeding_count")
            follow_up_questions.append("昨天有亲喂吗？如果有，大约亲喂几次？")
        if bottle_count > 0 and bottle_ml_missing_count > 0:
            missing_fields.append("yesterday_bottle_volume_ml")
            follow_up_questions.append("昨天部分瓶喂记录没有奶量，请补充每次或总瓶喂奶量。")

    if not rows:
        summary = "前一天无喂养记录，需要补充昨日喂养次数、亲喂次数和瓶喂/补奶量。"
    elif bottle_ml_missing_count > 0:
        summary = "前一天有喂养记录，但部分瓶喂记录缺少奶量。"
    else:
        summary = "前一天喂养记录可用于 24h 快速评估。"

    return {
        "ok": True,
        "user_id": uid,
        "as_of_time": _db_time(as_of_dt),
        "window": {
            "start_at": _db_time(day_start),
            "end_at": _db_time(day_end),
            "hours": 24,
            "scope": "previous_full_day",
        },
        "infant_id": infant_id if infant_id > 0 else None,
        "records": rows,
        "counts": {
            "total_records": len(rows),
            "breastfeeding_count": breastfeeding_count,
            "bottle_count": bottle_count,
            "bottle_formula_count": bottle_formula_count,
            "bottle_breast_count": bottle_breast_count,
            "bottle_unknown_count": bottle_unknown_count,
            "bottle_ml_missing_count": bottle_ml_missing_count,
        },
        "volumes": {
            "bottle_total_ml": round(bottle_total_ml, 1),
            "bottle_formula_ml": round(bottle_formula_ml, 1),
            "bottle_breast_ml": round(bottle_breast_ml, 1),
            "bottle_unknown_ml": round(bottle_unknown_ml, 1),
        },
        "flags": {
            "has_feeding_records": bool(rows),
            "has_bottle_feeding": bottle_count > 0,
            "has_formula_feeding": bottle_formula_count > 0,
            "has_breastfeeding": breastfeeding_count > 0,
            "has_missing_bottle_ml": bottle_ml_missing_count > 0,
        },
        "missing_fields": missing_fields,
        "follow_up_questions": follow_up_questions,
        "data_complete_hint": "missing_required_24h_feeding_data" if missing_fields else "ok",
        "summary": summary,
    }


def assess_feeding_demand_reference(
    *,
    user_id: str,
    infant_id: int | None = None,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    uid = str(user_id or "").strip()
    if not uid:
        return _empty_reference(False, "missing_user_id")

    profile = fetch_one("SELECT user_id, delivery_date FROM user_profile WHERE user_id = ?", (uid,)) or {}
    infant = _load_infant(uid, infant_id)
    if infant_id is not None and not infant:
        return _empty_reference(False, "infant_not_found")

    reference_date = _parse_date(profile.get("delivery_date")) or _parse_date(infant.get("birth_date") if infant else None)
    if reference_date is None:
        return _empty_reference(False, "missing_reference_date")

    current_date = _parse_date(as_of_date) or datetime.now().date()
    day_postpartum = max(1, (current_date - reference_date).days + 1)
    if day_postpartum > 360:
        return _empty_reference(False, "reference_out_of_range", postpartum_day=day_postpartum)

    try:
        reference = _interp_yield_reference(day_postpartum, _build_yield_points(str(_yield_reference_path())))
    except Exception:
        return _empty_reference(False, "yield_reference_missing", postpartum_day=day_postpartum)

    return {
        "ok": True,
        "rule_hit": "yield_percentile_reference",
        "postpartum_day": day_postpartum,
        "p25_value": int(round(reference["p25"])),
        "p50_value": int(round(reference["p50"])),
        "p75_value": int(round(reference["p75"])),
    }


def _empty_snapshot(*, ok: bool, summary: str) -> dict[str, Any]:
    return {
        "ok": ok,
        "summary": summary,
        "counts": {},
        "volumes": {},
        "flags": {},
        "missing_fields": [],
        "follow_up_questions": [],
        "records": [],
    }


def _bottle_feed_volumes(
    *,
    user_id: str,
    start_at: str | None,
    end_at: str,
    limit: int | None,
) -> list[float]:
    clauses = ["user_id = ?", "feed_time <= ?"]
    params: list[Any] = [user_id, end_at]
    if start_at:
        clauses.append("feed_time >= ?")
        params.append(start_at)
    rows = fetch_all(
        f"""
        SELECT feed_milk_volum, feed_type
        FROM feeding_log
        WHERE {' AND '.join(clauses)}
        ORDER BY feed_time DESC
        """,
        params,
    )
    volumes: list[float] = []
    for row in rows:
        if not _is_any_bottle(row.get("feed_type")):
            continue
        milk_ml = _optional_float(row.get("feed_milk_volum"))
        if milk_ml is not None and milk_ml > 0:
            volumes.append(milk_ml)
            if limit is not None and int(limit) > 0 and len(volumes) >= int(limit):
                break
    return volumes


def _is_breastfeeding(feed_type: Any) -> bool:
    token = str(feed_type or "").strip().lower()
    return token in {"direct", "breastfeeding", "breast", "母乳亲喂"} or "亲喂" in token


def _is_formula_bottle(feed_type: Any) -> bool:
    token = str(feed_type or "").strip().lower()
    return token in {"formula", "奶粉"} or "formula" in token or "奶粉" in token or "配方" in token


def _is_breastmilk_bottle(feed_type: Any) -> bool:
    token = str(feed_type or "").strip().lower()
    return token in {"bottle_breastmilk", "breastmilk_bottle"} or ("母乳" in token and ("瓶" in token or "bottle" in token))


def _is_any_bottle(feed_type: Any) -> bool:
    token = str(feed_type or "").strip().lower()
    return _is_formula_bottle(token) or _is_breastmilk_bottle(token) or "bottle" in token or "瓶" in token


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _db_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _load_infant(user_id: str, infant_id: int | None) -> dict[str, Any]:
    if infant_id is not None:
        return fetch_one(
            "SELECT infant_id, user_id, birth_date FROM infant_profile WHERE user_id = ? AND infant_id = ?",
            (user_id, int(infant_id)),
        ) or {}
    return fetch_one(
        "SELECT infant_id, user_id, birth_date FROM infant_profile WHERE user_id = ? ORDER BY infant_id LIMIT 1",
        (user_id,),
    ) or {}


def _empty_reference(ok: bool, rule_hit: str, postpartum_day: int | None = None) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "rule_hit": rule_hit,
        "postpartum_day": postpartum_day,
        "p25_value": 0,
        "p50_value": 0,
        "p75_value": 0,
    }


def _yield_reference_path() -> Path:
    return get_knowledge_root() / "infant_milk_volum_info" / "milk_yield_percentiles_0_360.json"


@lru_cache(maxsize=4)
def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=4)
def _build_yield_points(path: str) -> list[dict[str, float]]:
    records = _load_json(path).get("records", [])
    points: list[dict[str, float]] = []
    for row in records if isinstance(records, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            points.append(
                {
                    "day_postpartum": float(row["day_postpartum"]),
                    "p25": float(row["p25"]),
                    "p50": float(row["p50"]),
                    "p75": float(row["p75"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    points.sort(key=lambda item: item["day_postpartum"])
    if not points:
        raise ValueError("milk_yield_percentiles json has no valid p25/p50/p75 points")
    return points


def _interp_yield_reference(day_postpartum: int, points: list[dict[str, float]]) -> dict[str, float]:
    day = float(day_postpartum)
    if day <= points[0]["day_postpartum"]:
        return _pick_reference(points[0])
    if day >= points[-1]["day_postpartum"]:
        return _pick_reference(points[-1])
    for idx in range(len(points) - 1):
        left = points[idx]
        right = points[idx + 1]
        d0 = left["day_postpartum"]
        d1 = right["day_postpartum"]
        if d0 <= day <= d1:
            return {
                "p25": round(_linear_interp(day, d0, left["p25"], d1, right["p25"]), 1),
                "p50": round(_linear_interp(day, d0, left["p50"], d1, right["p50"]), 1),
                "p75": round(_linear_interp(day, d0, left["p75"], d1, right["p75"]), 1),
            }
    return _pick_reference(points[-1])


def _pick_reference(point: dict[str, float]) -> dict[str, float]:
    return {"p25": round(point["p25"], 1), "p50": round(point["p50"], 1), "p75": round(point["p75"], 1)}


def _linear_interp(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    if x1 == x0:
        return y1
    return y0 + (x - x0) / (x1 - x0) * (y1 - y0)


def _parse_date(value: Any) -> date | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token).date()
    except Exception:
        return None
