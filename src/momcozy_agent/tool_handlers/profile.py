from __future__ import annotations

from typing import Any

from ..services import data_store
from ..types import RuntimeInputs


def get_profile(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    profile = _runtime_profile(inputs)
    return {
        "tool_name": "profile_get",
        "user_profile": profile,
        "baby_profile": inputs.get("baby_profile", {}),
        "service_state": inputs.get("service_state", {}),
        "status": "read_from_profile_store",
    }


def update_profile(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    user_id = _runtime_user_id(inputs)
    if not user_id:
        return {
            "tool_name": "profile_update",
            "status": "missing_user_id",
            "ok": False,
            "summary": "缺少 user_id，无法保存基础资料。",
        }

    display_name = _optional_text(args.get("display_name"))
    age = _optional_age(args.get("age"))
    onboarding_skipped = args.get("onboarding_skipped")
    if onboarding_skipped is not True and onboarding_skipped is not False:
        onboarding_skipped = None

    if not display_name and age is None and onboarding_skipped is None:
        return {
            "tool_name": "profile_update",
            "status": "no_profile_changes",
            "ok": True,
            "summary": "没有需要更新的基础资料。",
            "user_profile": _runtime_profile(inputs),
        }

    profile = data_store.update_user_profile_memory(
        user_id=user_id,
        display_name=display_name,
        age=age,
        onboarding_skipped=onboarding_skipped,
    )
    if profile is None:
        return {
            "tool_name": "profile_update",
            "status": "profile_update_failed",
            "ok": False,
            "summary": "基础资料保存失败。",
        }
    return {
        "tool_name": "profile_update",
        "status": "profile_updated",
        "ok": True,
        "summary": "基础资料已更新。",
        "profile_onboarding_complete": bool(profile.get("profile_onboarding_complete")),
        "profile_onboarding_skipped": bool(profile.get("profile_onboarding_skipped")),
    }


def _runtime_profile(inputs: RuntimeInputs) -> dict[str, Any]:
    user_id = _runtime_user_id(inputs)
    persisted = data_store.get_user_profile(user_id) if user_id else {}
    provided = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    merged: dict[str, Any] = {}
    for source in (persisted, provided):
        for key, value in source.items():
            if value in (None, "") and key in merged:
                continue
            merged[key] = value
    if user_id:
        merged["user_id"] = user_id
    return _public_profile(merged)


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    display_name = _optional_text(profile.get("display_name") or profile.get("user_nickname"))
    age = _optional_age(profile.get("age"))
    skipped_at = _optional_text(profile.get("profile_onboarding_skipped_at"))
    completed_at = _optional_text(profile.get("profile_onboarding_completed_at"))
    return {
        "user_id": _optional_text(profile.get("user_id")),
        "display_name": display_name,
        "age": age,
        "profile_onboarding_complete": bool(display_name and age is not None),
        "profile_onboarding_skipped": bool(skipped_at),
        "profile_onboarding_skipped_at": skipped_at,
        "profile_onboarding_completed_at": completed_at,
        "birth_prep_due_date_or_week": _optional_text(profile.get("birth_prep_due_date_or_week")),
        "birth_prep_ivf": _optional_text(profile.get("birth_prep_ivf")),
        "birth_prep_fetus_count": _optional_text(profile.get("birth_prep_fetus_count")),
        "birth_prep_city_or_country": _optional_text(profile.get("birth_prep_city_or_country")),
        "birth_prep_birth_hospital": _optional_text(profile.get("birth_prep_birth_hospital")),
        "birth_prep_birth_path": _optional_text(profile.get("birth_prep_birth_path")),
        "birth_prep_first_birth": _optional_text(profile.get("birth_prep_first_birth")),
        "birth_prep_feeding_intention": _optional_text(profile.get("birth_prep_feeding_intention")),
        "birth_prep_return_to_work_timing": _optional_text(profile.get("birth_prep_return_to_work_timing")),
        "birth_prep_support_person": _optional_text(profile.get("birth_prep_support_person")),
        "birth_prep_pregnancy_history_or_notes": _optional_text(profile.get("birth_prep_pregnancy_history_or_notes")),
        "birth_prep_top_worries": _optional_text(profile.get("birth_prep_top_worries")),
        "current_care_stage": _current_care_stage(profile.get("current_care_stage")),
        "current_care_stage_source": _optional_text(profile.get("current_care_stage_source")),
        "language": _optional_text(profile.get("language")),
    }


def _runtime_user_id(inputs: RuntimeInputs) -> str:
    profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    return str(inputs.get("user_id") or profile.get("user_id") or "").strip()


def _optional_text(value: Any) -> str:
    return str(value or "").strip()


def _optional_age(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        age = int(value)
    except Exception:
        return None
    if age < 0 or age > 120:
        return None
    return age


def _current_care_stage(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in {"pregnancy", "postpartum"} else ""
