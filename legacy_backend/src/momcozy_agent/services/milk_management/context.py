from __future__ import annotations

import json
from typing import Any

from .db import fetch_all, fetch_one
from .schemas import ServiceResult, error_result, norm_text, ok_result


def get_milk_context(*, user_id: str) -> ServiceResult:
    """Read shared milk-management context for tools and API handlers."""

    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法读取奶量管理上下文。")

    user_profile = fetch_one(
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
    latest_plan = fetch_one(
        """
        SELECT plan_id, user_id, plan_name, plan_type, plan_days, plan_summary,
               milestone_summary, milestone_list, plan_payload_json, created_at, updated_at
        FROM milk_plan
        WHERE user_id = ?
        ORDER BY plan_id DESC
        LIMIT 1
        """,
        (uid,),
    )
    today_counts = fetch_one(
        """
        SELECT COUNT(*) AS total_count,
               SUM(CASE WHEN finish IN (1, '1', 'true') THEN 1 ELSE 0 END) AS completed_count
        FROM calendar
        WHERE user_id = ?
          AND date = DATE('now', 'localtime')
        """,
        (uid,),
    ) or {}

    setup_missing = []
    if not user_profile:
        setup_missing.append("user_profile")
    elif not norm_text(user_profile.get("delivery_date")):
        setup_missing.append("delivery_date")
    if not infants:
        setup_missing.append("infant_profile")

    return ok_result(
        "milk_context_loaded",
        "已读取奶量管理上下文。",
        {
            "user_id": uid,
            "user_profile": user_profile,
            "infants": infants,
            "latest_plan": _normalize_plan_row(latest_plan),
            "today_calendar_summary": {
                "total_count": int(today_counts.get("total_count") or 0),
                "completed_count": int(today_counts.get("completed_count") or 0),
            },
            "setup": {
                "ready": len(setup_missing) == 0,
                "missing": setup_missing,
            },
        },
    )


def _normalize_plan_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    payload = norm_text(result.get("plan_payload_json"))
    if payload:
        try:
            result["plan_payload"] = json.loads(payload)
        except json.JSONDecodeError:
            result["plan_payload"] = None
    return result
