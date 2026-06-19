from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from .services import data_store
from .types import RuntimeInputs

DEFAULT_LOCALE = "en-US"
DEFAULT_TIMEZONE = "America/Los_Angeles"


@dataclass
class ContextState:
    environment_sent: bool = False
    loaded_references: list[str] = field(default_factory=list)
    loaded_tools: list[str] = field(default_factory=list)
    client_events: list[str] = field(default_factory=list)
    available_tool_images: list[dict[str, str]] = field(default_factory=list)
    last_displayed_tool_image: dict[str, str] | None = None
    active_device_module: str = ""
    active_service_domain: str = ""
    shown_step_image_urls: list[str] = field(default_factory=list)
    profile_slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    birth_prep_slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_assistant_message: str = ""
    slot_turn_index: int = 0
    birth_journey_intake: dict[str, Any] = field(default_factory=dict)
    milk_management_state: dict[str, Any] = field(default_factory=dict)


PROFILE_SLOT_FIELDS = (
    "display_name",
    "age",
)
HOSPITAL_BAG_SLOT_FIELDS = (
    "age",
    "due_date_or_week",
    "ivf",
    "first_birth",
    "fetus_count",
    "city_or_country",
    "birth_hospital",
    "pregnancy_history_or_notes",
    "birth_path",
    "feeding_intention",
    "return_to_work_timing",
    "support_person",
    "top_worries",
)
_HOSPITAL_BAG_SLOT_KEY = "hospital_bag"
_MAX_SLOT_TEXT_LENGTH = 160
_SLOT_RECORD_MARKER_KEYS = frozenset({"value", "status", "source"})
_SLOT_STATUS_CONFIRMED = "confirmed"
_SLOT_SOURCE_USER_TEXT = "user_text"
_MAX_LOADED_TOOL_CONTEXT_ITEMS = 12
_SERVICE_DOMAIN_ALIASES = {
    "birth-prep": "birth_prep",
    "birth_prep": "birth_prep",
    "birthprep": "birth_prep",
    "pregnancy": "birth_prep",
    "pregnancy_plan": "birth_prep",
    "hospital_bag": "birth_prep",
    "labor_communication": "birth_prep",
    "milk-management": "milk_management",
    "milk_management": "milk_management",
    "milkmanagement": "milk_management",
    "postpartum": "milk_management",
    "lactation": "milk_management",
    "device-guidance": "device_guidance",
    "device_guidance": "device_guidance",
    "deviceguidance": "device_guidance",
    "emotion-support": "emotion_support",
    "emotion_support": "emotion_support",
    "emotionsupport": "emotion_support",
}
_BIRTH_PREP_DOMAIN = "birth_prep"
_MILK_MANAGEMENT_DOMAIN = "milk_management"
_LOADED_TOOL_GUIDANCE = {
    "birth_journey_intake_manage": (
        "birth_journey_intake_manage 已在当前会话中加载/使用过；孕期计划信息采集继续用它推进状态机，"
        "不要重复调用 tool_search 查找 birth_prep 工具，除非用户切换服务或本轮无法直接调用该工具。"
    ),
    "birth_journey_plan_card_create": (
        "birth_journey_plan_card_create 已在当前会话中加载/使用过；只有 birth_journey_intake_manage 返回 ready_to_generate "
        "或用户明确处理已有孕期计划时才调用，不要重复 tool_search 查找 birth_prep 工具。"
    ),
    "hospital_bag_form_create": (
        "hospital_bag_form_create 已在当前会话中加载/使用过；待产包信息表场景优先复用该工具，不要重复 tool_search 查找 birth_prep 工具。"
    ),
    "hospital_bag_card_create": (
        "hospital_bag_card_create 已在当前会话中加载/使用过；已有确认表单数据时优先复用该工具生成清单，不要重复 tool_search 查找 birth_prep 工具。"
    ),
    "labor_communication_card_create": (
        "labor_communication_card_create 已在当前会话中加载/使用过；分娩沟通单场景优先复用该工具，不要重复 tool_search 查找 birth_prep 工具。"
    ),
    "device_manual_search": (
        "device_manual_search 已在当前会话中加载/使用过；同一设备说明或 FAQ 场景优先复用已读内容和该工具，不要重复搜索同一资料。"
    ),
}
_LOADED_TOOL_SERVICE_DOMAINS = {
    "birth_plan_form_create": _BIRTH_PREP_DOMAIN,
    "labor_communication_card_create": _BIRTH_PREP_DOMAIN,
    "birth_journey_intake_manage": _BIRTH_PREP_DOMAIN,
    "birth_journey_plan_card_create": _BIRTH_PREP_DOMAIN,
    "birth_journey_plan_delete": _BIRTH_PREP_DOMAIN,
    "birth_journey_plan_todo_update": _BIRTH_PREP_DOMAIN,
    "hospital_bag_form_create": _BIRTH_PREP_DOMAIN,
    "hospital_bag_card_create": _BIRTH_PREP_DOMAIN,
    "hospital_bag_cart_update": _BIRTH_PREP_DOMAIN,
    "hospital_bag_pump_recommend": _BIRTH_PREP_DOMAIN,
    "pregnancy_diary_manage": _BIRTH_PREP_DOMAIN,
    "device_manual_search": "device_guidance",
    "support_ticket_draft_create": "device_guidance",
    "milk_snapshot_get": _MILK_MANAGEMENT_DOMAIN,
    "milk_status_query": _MILK_MANAGEMENT_DOMAIN,
    "milk_analysis_intake_manage": _MILK_MANAGEMENT_DOMAIN,
    "milk_analysis_evaluate": _MILK_MANAGEMENT_DOMAIN,
    "milk_plan_preview_create": _MILK_MANAGEMENT_DOMAIN,
    "infant_growth_evaluate": _MILK_MANAGEMENT_DOMAIN,
    "infant_growth_mutate": _MILK_MANAGEMENT_DOMAIN,
    "milk_records_query": _MILK_MANAGEMENT_DOMAIN,
    "milk_record_mutate": _MILK_MANAGEMENT_DOMAIN,
    "milk_plan_query": _MILK_MANAGEMENT_DOMAIN,
    "milk_plan_mutate": _MILK_MANAGEMENT_DOMAIN,
    "milk_calendar_query": _MILK_MANAGEMENT_DOMAIN,
    "milk_calendar_change_preview": _MILK_MANAGEMENT_DOMAIN,
    "milk_calendar_reschedule_preview": _MILK_MANAGEMENT_DOMAIN,
    "milk_calendar_mutate": _MILK_MANAGEMENT_DOMAIN,
    "milk_task_complete": _MILK_MANAGEMENT_DOMAIN,
}
_MILK_STATE_INVALIDATING_WRITE_TOOLS = {
    "milk_record_mutate",
    "milk_plan_mutate",
    "milk_calendar_mutate",
    "milk_task_complete",
    "infant_growth_mutate",
}


def build_request_context(
    inputs: RuntimeInputs,
    state: ContextState | None = None,
    loaded_skill_ids: list[str] | None = None,
) -> str:
    include_environment = state is None or not state.environment_sent
    service_domain = active_service_domain(inputs, state)
    lines = ["request_context:"]

    if include_environment:
        lines.append(f"locale: {inputs.get('locale') or DEFAULT_LOCALE}")
        lines.append(f"timezone: {inputs.get('timezone') or DEFAULT_TIMEZONE}")
        lines.extend(_format_user_profile_context(inputs))
        if state is not None:
            state.environment_sent = True

    lines.append(f"message_sent_at: {_message_sent_at(inputs)}")
    if _should_inject_birth_prep_domain_context(service_domain):
        lines.extend(_format_birth_prep_profile_context(inputs))
    if loaded_skill_ids:
        lines.append("loaded_skill_context:")
        for skill_id in _unique_strings(loaded_skill_ids):
            lines.append(
                f"- {skill_id}/SKILL.md 已在当前会话中读取过；连续同一服务任务优先复用，不要重复调用 load_skill，除非用户切换服务或需要新的未读资料。"
            )
    if state is not None:
        lines.extend(_format_loaded_tool_context(state, service_domain))
    if state is not None and state.loaded_references:
        lines.append("loaded_reference_context:")
        for reference in state.loaded_references:
            lines.append(f"- {reference}")
    if state is not None and state.client_events:
        lines.append("client_event_context:")
        for event in state.client_events[-5:]:
            lines.append(f"- {event}")
    if state is not None:
        if _should_inject_birth_prep_domain_context(service_domain):
            lines.extend(_format_birth_prep_context(state))
        lines.extend(_format_profile_slot_context(state))
        if _should_inject_milk_management_context(service_domain):
            lines.extend(_format_milk_management_context(state))
        lines.extend(_format_device_image_context(state))
    lines.extend(
        _format_active_care_plan_context(
            inputs,
            include_birth_journey_todos=_should_inject_birth_prep_domain_context(service_domain),
        )
    )
    lines.extend(_format_pregnancy_diary_context(inputs))
    hospital_bag_cart = _format_hospital_bag_cart_context(inputs.get("hospital_bag_cart"))
    if hospital_bag_cart:
        lines.extend(hospital_bag_cart)
    return "\n".join(line for line in lines if line)


def capture_birth_prep_user_message(inputs: RuntimeInputs, state: ContextState) -> None:
    """Legacy compatibility hook.

    Session slots are now extracted by the async sidecar in server.py. This
    synchronous hook intentionally does not parse natural language or write
    persistent profile memory.
    """
    return


def record_birth_prep_assistant_message(state: ContextState, message: str) -> None:
    state.last_assistant_message = _clip_slot_text(message, max_length=1200)


def active_service_domain(inputs: RuntimeInputs | None, state: ContextState | None = None) -> str:
    if isinstance(inputs, dict):
        for key in ("service_domain", "active_service_domain", "current_service"):
            domain = normalize_service_domain(inputs.get(key))
            if domain:
                return domain
    if state is not None:
        return normalize_service_domain(state.active_service_domain)
    return ""


def set_active_service_domain(state: ContextState, domain: Any) -> None:
    normalized = normalize_service_domain(domain)
    if normalized:
        state.active_service_domain = normalized


def record_loaded_tool(state: ContextState, tool_name: Any) -> None:
    name = str(tool_name or "").strip()
    if not name:
        return
    if name not in state.loaded_tools:
        state.loaded_tools.append(name)
    state.loaded_tools = state.loaded_tools[-_MAX_LOADED_TOOL_CONTEXT_ITEMS:]


def normalize_service_domain(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace(" ", "_").replace("-", "_").lower()
    return _SERVICE_DOMAIN_ALIASES.get(normalized, _SERVICE_DOMAIN_ALIASES.get(text.lower(), ""))


def _should_inject_birth_prep_domain_context(service_domain: str) -> bool:
    return service_domain in {"", _BIRTH_PREP_DOMAIN}


def _should_inject_milk_management_context(service_domain: str) -> bool:
    return service_domain in {"", _MILK_MANAGEMENT_DOMAIN}


def _format_loaded_tool_context(state: ContextState, service_domain: str) -> list[str]:
    tool_names = [
        tool_name
        for tool_name in _unique_strings(state.loaded_tools)[-_MAX_LOADED_TOOL_CONTEXT_ITEMS:]
        if _should_inject_loaded_tool_context(tool_name, service_domain)
    ]
    if not tool_names:
        return []
    lines = ["loaded_tool_context:"]
    for tool_name in tool_names:
        guidance = _LOADED_TOOL_GUIDANCE.get(
            tool_name,
            f"{tool_name} 已在当前会话中加载/使用过；连续同一服务任务优先复用，避免重复调用 tool_search 查找同一工具，除非用户切换服务或本轮无法直接调用。",
        )
        lines.append(f"- {guidance}")
    return lines


def _should_inject_loaded_tool_context(tool_name: str, service_domain: str) -> bool:
    if not service_domain:
        return True
    tool_domain = _LOADED_TOOL_SERVICE_DOMAINS.get(tool_name)
    return not tool_domain or tool_domain == service_domain


def merge_hospital_bag_slots(
    state: ContextState,
    values: dict[str, Any],
    *,
    source: str = "tool",
    status: str = _SLOT_STATUS_CONFIRMED,
    turn_id: int | None = None,
) -> None:
    _merge_hospital_bag_slot_values(state, values, source=source, status=status, turn_id=turn_id)


def merge_extracted_birth_prep_slots(
    state: ContextState,
    candidates: list[dict[str, Any]] | dict[str, Any] | None,
    *,
    turn_id: int | None = None,
    run_id: str = "",
    updated_at: str = "",
    extractor_version: str = "",
) -> dict[str, Any]:
    if not candidates:
        return {}
    items: list[dict[str, Any]]
    if isinstance(candidates, dict):
        raw_items = candidates.get("slots") if isinstance(candidates.get("slots"), list) else [candidates]
        items = [item for item in raw_items if isinstance(item, dict)]
    elif isinstance(candidates, list):
        items = [item for item in candidates if isinstance(item, dict)]
    else:
        return {}

    accepted: dict[str, Any] = {}
    slot_metadata: dict[str, dict[str, Any]] = {}
    accepted_profile: dict[str, Any] = {}
    profile_slot_metadata: dict[str, dict[str, Any]] = {}
    for item in items:
        raw_field_id = item.get("field_id") or item.get("field")
        metadata = {
            "evidence": _clip_slot_text(item.get("evidence"), max_length=120),
            "confidence": _normalized_confidence(item.get("confidence")),
            "updated_at": str(updated_at or "").strip(),
            "run_id": str(run_id or "").strip(),
            "extractor_version": str(extractor_version or "").strip(),
        }
        profile_field_id = _valid_profile_slot_field(raw_field_id)
        if profile_field_id:
            normalized_profile = _normalize_profile_slot_value(profile_field_id, item.get("value"))
            if _slot_value_has_content(normalized_profile):
                accepted_profile[profile_field_id] = normalized_profile
                profile_slot_metadata[profile_field_id] = metadata
                if profile_field_id == "age":
                    accepted["age"] = normalized_profile
                    slot_metadata["age"] = metadata
            continue

        field_id = _valid_hospital_bag_slot_field(raw_field_id)
        if not field_id:
            continue
        normalized = _normalize_hospital_bag_slot_value(field_id, item.get("value"))
        if not _slot_value_has_content(normalized):
            continue
        accepted[field_id] = normalized
        slot_metadata[field_id] = metadata

    _merge_profile_slot_values(
        state,
        accepted_profile,
        source=_SLOT_SOURCE_USER_TEXT,
        status=_SLOT_STATUS_CONFIRMED,
        turn_id=turn_id,
        metadata_by_field=profile_slot_metadata,
    )
    _merge_hospital_bag_slot_values(
        state,
        accepted,
        source=_SLOT_SOURCE_USER_TEXT,
        status=_SLOT_STATUS_CONFIRMED,
        turn_id=turn_id,
        metadata_by_field=slot_metadata,
    )
    return {**accepted, **accepted_profile}


def next_slot_extraction_turn(state: ContextState) -> int:
    state.slot_turn_index += 1
    return state.slot_turn_index


def hospital_bag_slots(state: ContextState | None) -> dict[str, Any]:
    if state is None:
        return {}
    slots = state.birth_prep_slots.get(_HOSPITAL_BAG_SLOT_KEY)
    if not isinstance(slots, dict):
        return {}
    return {
        field_id: _slot_record_value(slots[field_id])
        for field_id in HOSPITAL_BAG_SLOT_FIELDS
        if _slot_value_has_content(slots.get(field_id))
    }


def profile_slots(state: ContextState | None) -> dict[str, Any]:
    if state is None or not isinstance(state.profile_slots, dict):
        return {}
    return {
        field_id: _slot_record_value(state.profile_slots[field_id])
        for field_id in PROFILE_SLOT_FIELDS
        if _slot_value_has_content(state.profile_slots.get(field_id))
    }


def birth_journey_intake_state(state: ContextState | None) -> dict[str, Any]:
    if state is None or not isinstance(state.birth_journey_intake, dict):
        return {}
    return dict(state.birth_journey_intake)


def merge_birth_journey_intake_state(state: ContextState, values: dict[str, Any]) -> None:
    if not isinstance(values, dict):
        return
    state.birth_journey_intake = dict(values)


def _hospital_bag_slots(state: ContextState) -> dict[str, Any]:
    slots = state.birth_prep_slots.get(_HOSPITAL_BAG_SLOT_KEY)
    if not isinstance(slots, dict):
        slots = {}
        state.birth_prep_slots[_HOSPITAL_BAG_SLOT_KEY] = slots
    return slots


def _format_profile_slot_context(state: ContextState) -> list[str]:
    profile_values = [
        f"{field_id}={_display_slot_value(state.profile_slots[field_id])}"
        for field_id in PROFILE_SLOT_FIELDS
        if _slot_value_has_content(state.profile_slots.get(field_id))
    ]
    if not profile_values:
        return []
    return [
        "profile_slot_context:",
        "- profile_known_fields: " + "; ".join(profile_values),
        "- profile_slot_status: listed fields are confirmed session slots from user text; use them as the latest session-level user profile context, but keep persistent profile writes on the profile_update path.",
    ]


def _format_birth_prep_context(state: ContextState) -> list[str]:
    slots = state.birth_prep_slots.get(_HOSPITAL_BAG_SLOT_KEY)
    hospital_bag_values: list[str] = []
    if isinstance(slots, dict):
        hospital_bag_values = [
            f"{field_id}={_display_slot_value(slots[field_id])}"
            for field_id in HOSPITAL_BAG_SLOT_FIELDS
            if _slot_value_has_content(slots.get(field_id))
        ]

    intake = state.birth_journey_intake if isinstance(state.birth_journey_intake, dict) else {}
    intake_step = str(intake.get("next_step") or "").strip()
    intake_groups = intake.get("completed_groups") if isinstance(intake.get("completed_groups"), list) else []
    if not hospital_bag_values and not intake_step:
        return []
    lines = ["birth_prep_context:"]
    if hospital_bag_values:
        lines.append("- birth_prep_known_fields: " + "; ".join(hospital_bag_values))
        lines.append("- birth_prep_slot_status: listed fields are confirmed session slots.")
        lines.append("- 这些字段来自用户文本、表单或工具结果；创建产前表单或待产包表单时复用这些字段作为默认值，让用户在表单里确认或修改，不要重复追问。")
    if intake_step:
        lines.append(f"- birth_journey_intake_next_step: {intake_step}")
        if intake_groups:
            lines.append("- birth_journey_intake_completed_groups: " + ", ".join(str(group) for group in intake_groups))
        lines.append("- 孕期计划信息采集优先调用 birth_journey_intake_manage 继续推进，不要自己凭记忆判断流程。")
    return lines


def record_milk_management_tool_state(state: ContextState, tool_name: str, result: dict[str, Any]) -> None:
    set_active_service_domain(state, _MILK_MANAGEMENT_DOMAIN)
    if tool_name == "milk_analysis_intake_manage":
        _record_milk_analysis_intake_state(state, result)
        return
    if tool_name == "milk_analysis_evaluate":
        _record_milk_analysis_evaluation_state(state, result)
        return
    if tool_name == "milk_plan_preview_create":
        _record_milk_plan_preview_create_state(state, result)
        return
    if tool_name == "milk_assessment_evaluate":
        _record_milk_assessment_state(state, result)
        return
    if tool_name == "milk_plan_preview":
        _record_milk_plan_preview_state(state, result)
        return
    if tool_name in _MILK_STATE_INVALIDATING_WRITE_TOOLS and _milk_tool_succeeded(result):
        _invalidate_milk_management_state_after_write(state)


def _milk_tool_succeeded(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict) or result.get("ok") is not True:
        return False
    tool_result = result.get("result")
    if isinstance(tool_result, dict) and tool_result.get("ok") is False:
        return False
    return True


def _invalidate_milk_management_state_after_write(state: ContextState) -> None:
    state.milk_management_state.pop("analysis_intake", None)
    state.milk_management_state.pop("last_assessment", None)
    state.milk_management_state.pop("pending_plan_after_assessment", None)
    state.milk_management_state.pop("last_plan_preview", None)


def _record_milk_analysis_intake_state(state: ContextState, result: dict[str, Any]) -> None:
    tool_result = result.get("result")
    if not isinstance(tool_result, dict):
        return
    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    intake_state = data.get("intake_state") if isinstance(data.get("intake_state"), dict) else {}
    if not intake_state:
        return
    state.milk_management_state["analysis_intake"] = intake_state
    for key in ("last_assessment", "pending_plan_after_assessment", "last_plan_preview"):
        state.milk_management_state.pop(key, None)


def _record_milk_analysis_evaluation_state(state: ContextState, result: dict[str, Any]) -> None:
    tool_result = result.get("result")
    if not isinstance(tool_result, dict):
        return
    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    intake_state = data.get("intake_state") if isinstance(data.get("intake_state"), dict) else {}
    existing = state.milk_management_state.get("analysis_intake") if isinstance(state.milk_management_state.get("analysis_intake"), dict) else {}
    merged = {**existing, **intake_state}
    assessment = data.get("assessment_result") if isinstance(data.get("assessment_result"), dict) else {}
    if assessment:
        merged["assessment_result"] = assessment
    if data.get("analysis_context"):
        merged["analysis_context"] = data["analysis_context"]
    if merged:
        merged["stage"] = "analysis_ready"
        state.milk_management_state["analysis_intake"] = merged


def _record_milk_plan_preview_create_state(state: ContextState, result: dict[str, Any]) -> None:
    tool_result = result.get("result")
    if not isinstance(tool_result, dict):
        return
    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    intake_state = data.get("intake_state") if isinstance(data.get("intake_state"), dict) else {}
    existing = state.milk_management_state.get("analysis_intake") if isinstance(state.milk_management_state.get("analysis_intake"), dict) else {}
    merged = {**existing, **intake_state}
    assessment = data.get("assessment_result") if isinstance(data.get("assessment_result"), dict) else {}
    if assessment:
        merged["assessment_result"] = assessment
    if data.get("analysis_context"):
        merged["analysis_context"] = data["analysis_context"]
    plan_preview = data.get("plan_preview") if isinstance(data.get("plan_preview"), dict) else {}
    if not plan_preview:
        draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
        if draft and str(tool_result.get("status") or "").strip() == "plan_preview_ready":
            plan_preview = {
                "status": tool_result.get("status"),
                "draft": draft,
                "calendar_delta": data.get("calendar_delta") if isinstance(data.get("calendar_delta"), dict) else {},
                "idempotency_key": _milk_plan_preview_idempotency_key(draft),
            }
    if plan_preview:
        merged["plan_preview"] = plan_preview
        merged["stage"] = "plan_preview"
    if merged:
        state.milk_management_state["analysis_intake"] = merged
    _record_milk_plan_preview_state(state, result)


def _record_milk_assessment_state(state: ContextState, result: dict[str, Any]) -> None:
    tool_result = result.get("result")
    if not isinstance(tool_result, dict) or tool_result.get("ok") is not True:
        return
    data = tool_result.get("data")
    if not isinstance(data, dict):
        return
    clinical = data.get("clinical_assessment") if isinstance(data.get("clinical_assessment"), dict) else {}
    flow_decision = data.get("milk_flow_decision") if isinstance(data.get("milk_flow_decision"), dict) else {}
    state.milk_management_state["last_assessment"] = {
        "as_of_time": data.get("as_of_time"),
        "assessment_status": data.get("assessment_status"),
        "assessment_data": data,
        "clinical_assessment": clinical,
        "growth_assessment": clinical.get("growth_assessment") if isinstance(clinical.get("growth_assessment"), dict) else {},
        "flow_decision": flow_decision,
    }
    normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    status = str(normality.get("overall_status") or data.get("assessment_status") or "").strip()
    plan_gate = clinical.get("plan_gate") if isinstance(clinical.get("plan_gate"), dict) else {}
    if status != "under_supply_alert" or plan_gate.get("allowed") is not True:
        state.milk_management_state.pop("pending_plan_after_assessment", None)
        return
    allowed_types = plan_gate.get("allowed_plan_types") if isinstance(plan_gate.get("allowed_plan_types"), list) else []
    if allowed_types and "increase_milk" not in {str(item) for item in allowed_types}:
        state.milk_management_state.pop("pending_plan_after_assessment", None)
        return
    rhythm = data.get("recent_milk_rhythm") if isinstance(data.get("recent_milk_rhythm"), dict) else {}
    rhythm_summary = rhythm.get("summary") if isinstance(rhythm.get("summary"), dict) else {}
    state.milk_management_state["pending_plan_after_assessment"] = {
        "plan_type": "increase_milk",
        "as_of_time": data.get("as_of_time"),
        "basis_date": rhythm_summary.get("basis_date"),
        "typical_pumping_times": rhythm_summary.get("typical_pumping_times"),
        "typical_nursing_times": rhythm_summary.get("typical_nursing_times"),
        "usable_for_schedule": rhythm_summary.get("usable_for_schedule"),
    }


def _record_milk_plan_preview_state(state: ContextState, result: dict[str, Any]) -> None:
    tool_result = result.get("result")
    if not isinstance(tool_result, dict):
        return
    data = tool_result.get("data")
    data = data if isinstance(data, dict) else {}
    flow_decision = data.get("milk_flow_decision") if isinstance(data.get("milk_flow_decision"), dict) else {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    calendar_delta = data.get("calendar_delta") if isinstance(data.get("calendar_delta"), dict) else {}
    clinical = data.get("clinical_assessment") if isinstance(data.get("clinical_assessment"), dict) else {}
    if flow_decision or (draft and str(tool_result.get("status") or "").strip() == "plan_preview_ready"):
        state.milk_management_state["last_plan_preview"] = {
            "status": tool_result.get("status"),
            "flow_decision": flow_decision,
            "clinical_assessment": clinical,
            "draft": draft,
            "calendar_delta": calendar_delta,
            "idempotency_key": _milk_plan_preview_idempotency_key(draft),
        }
        if tool_result.get("ok") is False:
            return
    state.milk_management_state.pop("pending_plan_after_assessment", None)
    if tool_result.get("ok") is True and not draft:
        state.milk_management_state.pop("last_plan_preview", None)


def _format_milk_management_context(state: ContextState) -> list[str]:
    analysis_intake = state.milk_management_state.get("analysis_intake")
    if isinstance(analysis_intake, dict):
        lines = ["milk_management_context:"]
        lines.extend(_format_milk_analysis_intake_context(analysis_intake))
        return lines if len(lines) > 1 else []
    pending = state.milk_management_state.get("pending_plan_after_assessment")
    last_assessment = state.milk_management_state.get("last_assessment")
    last_plan_preview = state.milk_management_state.get("last_plan_preview")
    if not isinstance(pending, dict) and not isinstance(last_assessment, dict) and not isinstance(last_plan_preview, dict):
        return []
    lines = ["milk_management_context:"]
    if isinstance(last_assessment, dict):
        lines.extend(_format_last_milk_assessment_context(last_assessment))
    if isinstance(last_plan_preview, dict):
        lines.extend(_format_last_milk_plan_preview_context(last_plan_preview))
    if isinstance(pending, dict):
        lines.extend(_format_pending_milk_plan_context(pending))
    return lines if len(lines) > 1 else []


def _format_milk_analysis_intake_context(intake: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    stage = str(intake.get("stage") or "").strip()
    if stage:
        lines.append(f"- milk_analysis_intake_stage: {stage}")
    checklist = intake.get("checklist") if isinstance(intake.get("checklist"), list) else []
    completed = [str(item.get("id")) for item in checklist if isinstance(item, dict) and item.get("status") == "collected"]
    missing = [str(item.get("id")) for item in checklist if isinstance(item, dict) and item.get("status") != "collected"]
    if completed:
        lines.append("- milk_analysis_intake_completed_fields: " + ", ".join(completed))
    if missing:
        lines.append("- milk_analysis_intake_missing_fields: " + ", ".join(missing))
    current_field = str(intake.get("current_field") or "").strip()
    if current_field:
        lines.append(f"- milk_analysis_intake_current_field: {current_field}")
    next_question = str(intake.get("next_question") or "").strip()
    if next_question:
        lines.append(f"- milk_analysis_intake_next_question: {next_question}")
    plan_type = str(intake.get("plan_type") or "").strip()
    if plan_type:
        lines.append(f"- milk_analysis_intake_plan_type: {plan_type}")
    if intake.get("target_daily_ml") is not None:
        lines.append(f"- milk_analysis_intake_target_daily_ml: {intake.get('target_daily_ml')}")
    if intake.get("delta_ml") is not None:
        lines.append(f"- milk_analysis_intake_delta_ml: {intake.get('delta_ml')}")
    analysis_context = intake.get("analysis_context") if isinstance(intake.get("analysis_context"), dict) else {}
    lines.extend(_format_milk_analysis_context_records(analysis_context))

    plan_preview = intake.get("plan_preview") if isinstance(intake.get("plan_preview"), dict) else {}
    if str(plan_preview.get("status") or "").strip() == "plan_preview_ready":
        lines.append("- milk_plan_preview_ready_for_save: true")
        key = str(plan_preview.get("idempotency_key") or "").strip()
        if key:
            lines.append(f"- milk_plan_preview_idempotency_key: {key}")
        strategy = _last_plan_preview_calendar_strategy(plan_preview)
        if strategy:
            lines.append(f"- milk_plan_preview_calendar_write_strategy: {strategy}")
        lines.append("- 如果用户本轮明确确认保存、同步、写入或按这版执行，调用 milk_plan_mutate 创建计划；不要只用文字说已经保存。")
    elif missing:
        lines.append("- milk_analysis_required_tool: milk_analysis_intake_manage")
        lines.append("- 用户本轮若是在回答上一轮奶量分析追问，必须调用 milk_analysis_intake_manage 继续采集；不要直接分析或生成计划。")
    elif stage == "ready_to_evaluate":
        lines.append("- milk_analysis_required_tool: milk_analysis_evaluate")
        lines.append("- 信息采集已经完成；下一步必须直接调用 milk_analysis_evaluate，不要先向用户输出分析结论或计划建议。")
    elif stage == "analysis_ready":
        lines.append("- milk_analysis_result_available: true")
        assessment = intake.get("assessment_result") if isinstance(intake.get("assessment_result"), dict) else {}
        assessment_data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
        current_daily_ml = _milk_context_current_daily_ml(assessment_data)
        if current_daily_ml is not None:
            lines.append(f"- milk_analysis_current_daily_ml: {_milk_context_ml_text(current_daily_ml)}")
        lines.append("- 如果用户本轮表达继续制定计划、接受建议、给出每天多/少多少 ml 或目标奶量，调用 milk_plan_preview_create；不要询问工具已读取的 7 天记录或近期节奏。")
    elif stage == "plan_preview":
        lines.append("- 奶量计划预览已生成但尚未保存；保存前必须等待用户明确确认。")
    return lines


def _format_milk_analysis_context_records(context: dict[str, Any]) -> list[str]:
    records = context.get("records_snapshot") if isinstance(context.get("records_snapshot"), dict) else {}
    source = context.get("source_records") if isinstance(context.get("source_records"), dict) else {}
    counts = records.get("record_counts") if isinstance(records.get("record_counts"), dict) else {}
    daily_rollups = source.get("daily_rollups") if isinstance(source.get("daily_rollups"), list) else records.get("daily_rollups")
    raw_records = source.get("raw_records") if isinstance(source.get("raw_records"), dict) else records.get("raw_records")
    lines: list[str] = []
    if records:
        lines.append(f"- milk_analysis_records_status: {records.get('status')}")
        if records.get("valid_days") is not None:
            lines.append(f"- milk_analysis_records_valid_days: {records.get('valid_days')}")
    count_parts = []
    for key in ("pumping", "feeding", "calendar"):
        value = counts.get(key) if isinstance(counts, dict) else None
        if isinstance(value, (int, float)):
            count_parts.append(f"{key}={int(value)}")
    if count_parts:
        lines.append("- milk_analysis_source_record_counts: " + "; ".join(count_parts))
    if isinstance(daily_rollups, list) and daily_rollups:
        lines.append(f"- milk_analysis_source_daily_rollups_available: {len(daily_rollups)} days")
    if isinstance(raw_records, dict) and raw_records:
        lines.append("- milk_analysis_source_raw_records_available: true")
    if lines:
        lines.append("- milk_analysis_records_policy: 过去 7 天原始记录和日级汇总已在 analysis_context 中；不要向用户索要这些明细。")
    return lines


def _format_last_milk_assessment_context(last_assessment: dict[str, Any]) -> list[str]:
    flow_decision = last_assessment.get("flow_decision") if isinstance(last_assessment.get("flow_decision"), dict) else {}
    if not flow_decision:
        return []
    assessment_data = last_assessment.get("assessment_data") if isinstance(last_assessment.get("assessment_data"), dict) else {}
    lines: list[str] = []
    stage = str(flow_decision.get("stage") or "").strip()
    if stage:
        lines.append(f"- last_assessment_flow_stage: {stage}")
    current_daily_ml = _milk_context_current_daily_ml(assessment_data)
    if current_daily_ml is not None:
        lines.append(f"- last_assessment_current_daily_ml: {_milk_context_ml_text(current_daily_ml)}")
    lines.extend(_format_milk_source_record_context(assessment_data))
    missing = [str(item).strip() for item in flow_decision.get("missing_user_inputs", []) if str(item).strip()]
    if missing:
        lines.append("- last_assessment_missing_fields: " + ", ".join(missing))
        current_field = missing[0]
        lines.append(f"- last_assessment_current_missing_field: {current_field}")
        lines.append(f"- last_assessment_current_missing_question: {_milk_context_field_label(current_field)}")
    plan_decision = flow_decision.get("plan_decision") if isinstance(flow_decision.get("plan_decision"), dict) else {}
    next_tool = str(plan_decision.get("next_tool") or "").strip()
    if next_tool:
        lines.append(f"- last_assessment_next_tool: {next_tool}")
    if plan_decision.get("can_start_plan") is not None:
        lines.append(f"- last_assessment_can_start_plan: {_bool_context_value(plan_decision.get('can_start_plan'))}")
    plan_type = str(plan_decision.get("recommended_plan_type") or "").strip()
    if plan_type:
        lines.append(f"- last_assessment_recommended_plan_type: {plan_type}")
    reason = str(plan_decision.get("reason_for_user") or "").strip()
    if reason:
        lines.append(f"- last_assessment_plan_reason: {reason}")
    if missing and next_tool:
        lines.append(
            "- 如果用户本轮是在补充上述缺失信息，调用 milk_analysis_intake_manage 继续奶量分析信息采集，"
            "把用户补充的信息作为 user_update，并尽量保留已确认过的宝宝/妈妈信息；不要直接给调整建议，也不要自行结束流程。"
        )
        lines.append(
            "- 如果用户本轮是简短肯定、否定或状态描述，优先理解为对 last_assessment_current_missing_question 的回答；"
            "调用 milk_analysis_intake_manage 时带上已确认过的宝宝/妈妈信息，不要重复追问同一项。"
        )
    elif plan_decision.get("can_start_plan") is True and next_tool:
        lines.append(
            "- 如果用户本轮表达愿意继续或进入计划，调用 milk_plan_preview_create 承接计划预览流程；"
            "不要把工具可读取的近期吸奶、亲喂或日程节奏作为前置追问。"
        )
        lines.extend(_format_milk_plan_action_contract(next_tool=next_tool, plan_type=plan_type))
    return lines


def _format_milk_source_record_context(assessment_data: dict[str, Any]) -> list[str]:
    source = assessment_data.get("source_record_context") if isinstance(assessment_data.get("source_record_context"), dict) else {}
    counts = source.get("record_counts") if isinstance(source.get("record_counts"), dict) else {}
    daily_rollups = source.get("daily_rollups") if isinstance(source.get("daily_rollups"), list) else []
    if not counts and not daily_rollups:
        return []
    count_parts = []
    for key in ("pumping", "feeding", "calendar"):
        value = counts.get(key)
        if isinstance(value, (int, float)):
            count_parts.append(f"{key}={int(value)}")
    if daily_rollups:
        count_parts.append(f"daily_rollups={len(daily_rollups)}")
    lines = ["- last_assessment_source_records_available: true"]
    if count_parts:
        lines.append("- last_assessment_source_records: " + "; ".join(count_parts))
    lines.append("- last_assessment_source_records_policy: 原始记录已在上一轮工具结果/会话状态里，计划工具会复用；不要向用户索要过去 7 天明细。")
    return lines


def _milk_context_current_daily_ml(assessment_data: dict[str, Any]) -> float | None:
    normality = assessment_data.get("milk_normality") if isinstance(assessment_data.get("milk_normality"), dict) else {}
    days = normality.get("days") if isinstance(normality.get("days"), list) else []
    values: list[float] = []
    for day in days:
        if not isinstance(day, dict):
            continue
        if day.get("ok") is False:
            continue
        value = _milk_context_float(day.get("estimated_daily_milk_ml"))
        if value is not None and value > 0:
            values.append(value)
    if values:
        return round(sum(values) / len(values), 1)

    pumping = assessment_data.get("pumping_summary") if isinstance(assessment_data.get("pumping_summary"), dict) else {}
    total = _milk_context_float(pumping.get("total_ml"))
    window = assessment_data.get("window") if isinstance(assessment_data.get("window"), dict) else {}
    window_days = _milk_context_float(window.get("window_days"))
    if total is not None and total > 0 and window_days is not None and window_days > 0:
        return round(total / window_days, 1)
    return None


def _milk_context_float(value: Any) -> float | None:
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


def _milk_context_ml_text(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def _format_milk_plan_action_contract(*, next_tool: str, plan_type: str) -> list[str]:
    if next_tool not in {"milk_plan_preview", "milk_plan_preview_create"}:
        return []
    lines = [
        "- milk_plan_action_contract: must_call_preview_before_reply_when_user_accepts_or_provides_target",
        "- milk_plan_required_tool: milk_plan_preview_create",
        "- milk_plan_context_source: reuse cached milk analysis_context and assessment_result; milk_plan_preview_create 会生成计划草稿但不会保存。",
        "- milk_plan_forbidden_pretool_questions: recent_milk_records, seven_day_daily_breakdown, pumping_frequency, breastfeeding_frequency, calendar_rhythm, target_reconfirmation_when_delta_or_target_provided",
        "- 用户已给出继续、确认、每天多/少多少 ml、做到多少 ml 或生成计划时，先调用 milk_plan_preview_create，再根据工具结果回复。",
    ]
    if plan_type:
        lines.insert(2, f"- milk_plan_required_plan_type: {plan_type}")
    return lines


def _milk_context_field_label(field_id: str) -> str:
    labels = {
        "infant_signals": "宝宝近 24 小时尿布、精神和吃奶表现",
        "maternal_symptoms": "妈妈有没有发热、乳房红肿、硬块或疼痛加重",
        "infant_wet_diapers": "宝宝近 24 小时尿量/尿布情况",
        "infant_state_or_feeding_satisfaction": "宝宝精神状态和吃奶后表现",
        "infant_growth_signal": "宝宝近期体重增长情况",
        "maternal_red_flags": "妈妈有没有发热、寒战、红肿、硬块或疼痛加重",
        "maternal_breast_comfort": "吸奶或亲喂后乳房舒适度",
    }
    return labels.get(field_id, field_id)


def _format_last_milk_plan_preview_context(last_plan_preview: dict[str, Any]) -> list[str]:
    flow_decision = last_plan_preview.get("flow_decision") if isinstance(last_plan_preview.get("flow_decision"), dict) else {}
    draft = last_plan_preview.get("draft") if isinstance(last_plan_preview.get("draft"), dict) else {}
    if not flow_decision and not draft:
        return []
    lines: list[str] = []
    status = str(last_plan_preview.get("status") or "").strip()
    if status:
        lines.append(f"- last_plan_preview_status: {status}")
    missing = [str(item).strip() for item in flow_decision.get("missing_user_inputs", []) if str(item).strip()]
    if missing:
        lines.append("- last_plan_preview_missing_fields: " + ", ".join(missing))
    plan_decision = flow_decision.get("plan_decision") if isinstance(flow_decision.get("plan_decision"), dict) else {}
    next_tool = str(plan_decision.get("next_tool") or "").strip()
    if next_tool:
        lines.append(f"- last_plan_preview_next_tool: {next_tool}")
    reason = str(plan_decision.get("reason_for_user") or "").strip()
    if reason:
        lines.append(f"- last_plan_preview_reason: {reason}")
    if missing and next_tool:
        lines.append(
            "- 如果用户本轮是在补充上一轮奶量计划预览缺失信息，调用 milk_analysis_intake_manage 继续补齐信息，"
            "把用户补充的信息作为 user_update，并尽量保留已确认过的宝宝/妈妈信息；不要直接给调整建议，也不要自行结束流程。"
        )
    elif status == "plan_preview_ready" and draft:
        strategy = _last_plan_preview_calendar_strategy(last_plan_preview)
        lines.append("- last_plan_preview_ready_for_save: true")
        lines.append(
            "- 如果用户本轮明确确认同步、保存、写入日历或按这版计划执行，调用 milk_plan_mutate 创建计划；"
            "不要只用文字说已经同步。"
        )
        if strategy:
            lines.append(f"- last_plan_preview_calendar_write_strategy: {strategy}")
        key = str(last_plan_preview.get("idempotency_key") or "").strip()
        if key:
            lines.append(f"- last_plan_preview_idempotency_key: {key}")
    return lines


def _milk_plan_preview_idempotency_key(draft: dict[str, Any]) -> str:
    if not draft:
        return ""
    payload = json.dumps(draft, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"milk-plan-preview-{digest}"


def _last_plan_preview_calendar_strategy(last_plan_preview: dict[str, Any]) -> str:
    calendar_delta = last_plan_preview.get("calendar_delta") if isinstance(last_plan_preview.get("calendar_delta"), dict) else {}
    required = bool(calendar_delta.get("calendar_write_strategy_required") or calendar_delta.get("requires_calendar_write_strategy"))
    if required:
        return ""
    recommended = str(calendar_delta.get("recommended_calendar_write_strategy") or "").strip()
    return recommended


def _format_pending_milk_plan_context(pending: dict[str, Any]) -> list[str]:
    plan_type = str(pending.get("plan_type") or "").strip()
    if not plan_type:
        return []
    lines = [
        f"- last_assessment_suggested_plan_type: {plan_type}",
        "- 如果用户本轮表达接受上一轮计划建议、确认继续或希望进入下一步，就进入奶量计划预览流程；不要把已由工具可读取的近期吸奶、亲喂或日程节奏再次作为前置追问。",
        "- 奶量计划预览会自动读取最近 7 天吸奶、亲喂和日程记录来排时间；只有工具返回仍缺少宝宝或妈妈状态时，才继续追问对应缺失信息。",
    ]
    lines.extend(_format_milk_plan_action_contract(next_tool="milk_plan_preview", plan_type=plan_type))
    basis_date = str(pending.get("basis_date") or "").strip()
    if basis_date:
        lines.append(f"- recent_milk_rhythm_basis_date: {basis_date}")
    pumping_times = pending.get("typical_pumping_times") if isinstance(pending.get("typical_pumping_times"), list) else []
    nursing_times = pending.get("typical_nursing_times") if isinstance(pending.get("typical_nursing_times"), list) else []
    pumping_text = "、".join(str(item) for item in pumping_times if str(item).strip())
    nursing_text = "、".join(str(item) for item in nursing_times if str(item).strip())
    if pumping_text:
        lines.append(f"- recent_typical_pumping_times: {pumping_text}")
    if nursing_text:
        lines.append(f"- recent_typical_nursing_times: {nursing_text}")
    return lines


def _bool_context_value(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _format_active_care_plan_context(inputs: RuntimeInputs, *, include_birth_journey_todos: bool = False) -> list[str]:
    user_id = _runtime_user_id(inputs)
    if not user_id:
        return []
    try:
        active_plans = data_store.list_care_plan_artifacts(user_id=user_id, status="active")
    except Exception:
        return []
    birth_journey_plan = next(
        (plan for plan in active_plans if isinstance(plan, dict) and plan.get("plan_type") == "birth_journey"),
        None,
    )
    if not isinstance(birth_journey_plan, dict):
        return []
    payload = birth_journey_plan.get("payload") if isinstance(birth_journey_plan.get("payload"), dict) else {}
    phases = payload.get("phases") if isinstance(payload.get("phases"), list) else []
    current_phase = next(
        (phase for phase in phases if isinstance(phase, dict) and phase.get("status") == "current"),
        None,
    )
    current_phase_title = _trim_context_value(current_phase.get("title") if isinstance(current_phase, dict) else "")
    summary = _trim_context_value(birth_journey_plan.get("summary"), 120)
    updated_at = _trim_context_value(birth_journey_plan.get("updated_at"), 40)
    plan_id = str(birth_journey_plan.get("plan_id") or "").strip()
    detail_parts = [
        f"plan_id={plan_id}" if plan_id else "",
        f"current_phase={current_phase_title}" if current_phase_title else "",
        f"summary={summary}" if summary else "",
        f"updated_at={updated_at}" if updated_at else "",
    ]
    lines = [
        "active_care_plan_context:",
        "- birth_journey_plan: 已存在 active 孕期计划；" + "；".join(part for part in detail_parts if part),
        "- 只要该计划未被删除，就把它视为用户已有计划；用户要求生成/制定孕期计划时，不要再次调用 birth_journey_plan_card_create 创建新计划，先说明已有计划并继续查看或推进。",
    ]
    if include_birth_journey_todos:
        lines.extend(_format_birth_journey_next_7_todo_context(payload))
    return lines


def _format_birth_journey_next_7_todo_context(payload: dict[str, Any]) -> list[str]:
    layers = payload.get("planning_layers") if isinstance(payload.get("planning_layers"), dict) else {}
    next_7 = layers.get("next_7_days") if isinstance(layers.get("next_7_days"), dict) else {}
    items = next_7.get("items") if isinstance(next_7.get("items"), list) else []
    todo_lines: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        title = _trim_context_value(item.get("title"), 36)
        if not title:
            continue
        item_id = _trim_context_value(item.get("id"), 20) or f"next7_{index + 1:02d}"
        state = "done" if _context_completed_bool(item.get("completed")) else "todo"
        todo_lines.append(f"  {index + 1}. [{state}] {item_id} {title}")
    if not todo_lines:
        return []
    return [
        "- next_7_days_todos: 用户说已完成/取消完成这些事项时，调用 birth_journey_plan_todo_update；优先传 item_id，也可传编号。",
        *todo_lines,
    ]


def _context_completed_bool(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "done", "completed", "完成", "已完成"}
    return bool(value) if isinstance(value, int) else False


def _format_pregnancy_diary_context(inputs: RuntimeInputs) -> list[str]:
    user_id = _runtime_user_id(inputs)
    if not user_id:
        return []
    try:
        entries = data_store.list_pregnancy_diary_entries(user_id=user_id, limit=7)
    except Exception:
        return []
    detail_parts = [f"recent_entries={len(entries)}"]
    if entries:
        latest = entries[0]
        recent_dates = [_trim_context_value(entry.get("entry_date"), 20) for entry in entries]
        detail_parts.extend(
            [
                f"recent_dates={','.join(date for date in recent_dates if date)}",
                f"latest_date={_trim_context_value(latest.get('entry_date'), 20)}",
                f"latest_has_content={bool(str(latest.get('content') or '').strip())}",
            ]
        )
    return [
        "pregnancy_diary_context:",
        "- " + "；".join(part for part in detail_parts if part),
        "- 用户明确要查看、整理、写入、更新或删除孕期日记时，使用 pregnancy_diary_manage。",
        "- 用户具体讲述今天/近期的孕期生活、身体感受、情绪、产检、胎动、睡眠、饮食、用药/补剂、已尝试措施或想问医生的问题时，可以主动写入孕期日记；写入不必另行追问确认，用户主动说出的事实可视为可记录内容。",
        "- 读取具体日期必须调用 read/list，不要仅凭摘要臆造完整记录；如果当天已有日记，先 read 再结合新补充 update。",
        "- 写入或更新孕期日记只记录用户明确表达的日记内容；纯科普、泛泛咨询、模型建议、安抚、风险判断、医疗提醒或观察计划不要写入；用户说不用记录时不要记录；删除仍必须 confirmed=true。",
    ]


def _format_user_profile_context(inputs: RuntimeInputs) -> list[str]:
    profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    if not profile:
        return []
    display_name = _trim_context_value(profile.get("display_name") or profile.get("user_nickname"), 40)
    age = _profile_age_text(profile.get("age"))
    skipped = bool(profile.get("profile_onboarding_skipped")) or bool(str(profile.get("profile_onboarding_skipped_at") or "").strip())
    missing: list[str] = []
    if not display_name:
        missing.append("display_name")
    if not age:
        missing.append("age")
    details = [
        f"display_name={display_name}" if display_name else "",
        f"age={age}" if age else "",
        "onboarding=skipped" if skipped else "",
        f"missing={','.join(missing)}" if missing else "onboarding=complete",
    ]
    lines = ["user_profile_context:", "- " + "；".join(part for part in details if part)]
    if skipped:
        lines.append("- 用户已选择暂时跳过基础资料收集；不要因为缺少名字或年龄而在新会话里主动反复追问。")
    return lines


def _format_birth_prep_profile_context(inputs: RuntimeInputs) -> list[str]:
    profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    if not profile:
        return []
    fields = (
        ("age", profile.get("age")),
        ("due_date_or_week", profile.get("birth_prep_due_date_or_week")),
        ("ivf", profile.get("birth_prep_ivf")),
        ("fetus_count", profile.get("birth_prep_fetus_count")),
        ("city_or_country", profile.get("birth_prep_city_or_country")),
        ("birth_hospital", profile.get("birth_prep_birth_hospital")),
        ("birth_path", profile.get("birth_prep_birth_path")),
        ("first_birth", profile.get("birth_prep_first_birth")),
        ("feeding_intention", profile.get("birth_prep_feeding_intention")),
        ("return_to_work_timing", profile.get("birth_prep_return_to_work_timing")),
        ("support_person", profile.get("birth_prep_support_person")),
        ("pregnancy_history_or_notes", profile.get("birth_prep_pregnancy_history_or_notes")),
        ("top_worries", profile.get("birth_prep_top_worries")),
    )
    details = [
        f"{field_id}={value_text}"
        for field_id, raw_value in fields
        if (value_text := _trim_context_value(raw_value, 80))
    ]
    context_line = "；".join(part for part in details if part)
    if not context_line:
        return []
    return [
        "birth_prep_profile_context:",
        f"- {context_line}",
        "- 这些是孕期计划、待产包和分娩沟通单共享的已确认信息；相关服务优先复用，创建表单时作为默认值，不要重复询问同一个已知字段。",
    ]


def _runtime_user_id(inputs: RuntimeInputs) -> str:
    user_profile = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    return str(inputs.get("user_id") or user_profile.get("user_id") or "").strip()


def _profile_age_text(value: Any) -> str:
    try:
        age = int(value)
    except Exception:
        return ""
    if age < 0 or age > 120:
        return ""
    return str(age)


def _trim_context_value(value: Any, max_length: int = _MAX_SLOT_TEXT_LENGTH) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _valid_hospital_bag_slot_field(value: Any) -> str:
    field_id = str(value or "").strip()
    return field_id if field_id in HOSPITAL_BAG_SLOT_FIELDS else ""


def _valid_profile_slot_field(value: Any) -> str:
    field_id = str(value or "").strip()
    return field_id if field_id in PROFILE_SLOT_FIELDS else ""


def _merge_profile_slot_values(
    state: ContextState,
    values: dict[str, Any],
    *,
    source: str,
    status: str,
    turn_id: int | None = None,
    metadata_by_field: dict[str, dict[str, Any]] | None = None,
) -> None:
    if not isinstance(values, dict):
        return
    effective_turn_id = turn_id if turn_id is not None else state.slot_turn_index
    for field_id in PROFILE_SLOT_FIELDS:
        if field_id not in values:
            continue
        value = _normalize_profile_slot_value(field_id, values.get(field_id))
        if not _slot_value_has_content(value):
            continue
        existing = state.profile_slots.get(field_id)
        if effective_turn_id is not None and _slot_record_turn_id(existing) > effective_turn_id:
            continue
        metadata = dict((metadata_by_field or {}).get(field_id) or {})
        state.profile_slots[field_id] = _slot_record(
            value,
            status=status,
            source=source,
            turn_id=effective_turn_id if effective_turn_id > 0 else None,
            **metadata,
        )


def _merge_hospital_bag_slot_values(
    state: ContextState,
    values: dict[str, Any],
    *,
    source: str,
    status: str,
    turn_id: int | None = None,
    metadata_by_field: dict[str, dict[str, Any]] | None = None,
) -> None:
    if not isinstance(values, dict):
        return
    slots = _hospital_bag_slots(state)
    effective_turn_id = turn_id if turn_id is not None else state.slot_turn_index
    for field_id in HOSPITAL_BAG_SLOT_FIELDS:
        if field_id not in values:
            continue
        value = _normalize_hospital_bag_slot_value(field_id, values.get(field_id))
        if not _slot_value_has_content(value):
            continue
        existing = slots.get(field_id)
        if effective_turn_id is not None and _slot_record_turn_id(existing) > effective_turn_id:
            continue
        metadata = dict((metadata_by_field or {}).get(field_id) or {})
        slots[field_id] = _slot_record(
            value,
            status=status,
            source=source,
            turn_id=effective_turn_id if effective_turn_id > 0 else None,
            **metadata,
        )


def _slot_record(value: Any, *, status: str, source: str, turn_id: int | None = None, **metadata: Any) -> dict[str, Any]:
    record = {
        "value": value,
        "status": status or _SLOT_STATUS_CONFIRMED,
        "source": source or "unknown",
    }
    if turn_id is not None:
        record["turn_id"] = turn_id
    for key, meta_value in metadata.items():
        if _slot_value_has_content(meta_value):
            record[key] = meta_value
    return record


def _is_slot_record(value: Any) -> bool:
    return isinstance(value, dict) and bool(_SLOT_RECORD_MARKER_KEYS.intersection(value.keys())) and "value" in value


def _slot_record_value(value: Any) -> Any:
    if _is_slot_record(value):
        return value.get("value")
    return value


def _slot_record_turn_id(value: Any) -> int:
    if not _is_slot_record(value):
        return 0
    try:
        return int(value.get("turn_id") or 0)
    except Exception:
        return 0


def _normalized_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except Exception:
        return None
    if confidence < 0:
        return 0.0
    if confidence > 1:
        return 1.0
    return confidence


def _normalize_hospital_bag_slot_value(field_id: str, value: Any) -> Any:
    if _is_slot_record(value):
        value = value.get("value")
    if field_id == "age":
        return _normalize_age(value)
    if field_id == "due_date_or_week":
        return _normalize_due_or_week(value)
    if field_id in {"ivf", "first_birth"}:
        return _normalize_yes_no(value)
    if field_id == "fetus_count":
        return _normalize_enum(value, {"单胎", "双胎", "多胎", "三胎及以上", "不确定", "还不确定"})
    if field_id == "city_or_country":
        return _normalize_short_text(value, max_length=30, reject_sentence=True)
    if field_id == "birth_hospital":
        return _normalize_short_text(value, max_length=40, reject_sentence=True)
    if field_id == "birth_path":
        return _normalize_enum(value, {"顺产", "自然分娩", "剖宫产", "剖腹产", "计划剖宫产", "还不确定", "不确定"})
    if field_id == "feeding_intention":
        return _normalize_enum(value, {"亲喂母乳", "母乳喂养", "混合喂养", "配方奶", "纯泵", "还不确定", "不确定"})
    if field_id in {"pregnancy_history_or_notes", "top_worries"}:
        return _normalize_text_list(value, max_items=6 if field_id == "pregnancy_history_or_notes" else 3)
    if field_id in {"return_to_work_timing", "support_person"}:
        return _normalize_short_text(value, max_length=80, reject_sentence=False)
    return _normalize_short_text(value, max_length=_MAX_SLOT_TEXT_LENGTH, reject_sentence=False)


def _normalize_profile_slot_value(field_id: str, value: Any) -> Any:
    if _is_slot_record(value):
        value = value.get("value")
    if field_id == "display_name":
        return _normalize_short_text(value, max_length=40, reject_sentence=True)
    if field_id == "age":
        return _normalize_age(value)
    return ""


def _normalize_age(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        age = int(value)
    except Exception:
        text = str(value or "").strip()
        if not text.isdigit():
            return None
        age = int(text)
    if 12 <= age <= 60:
        return age
    return None


def _normalize_due_or_week(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        try:
            week = int(value)
        except Exception:
            return ""
        if week == value:
            return f"孕{week}周" if 1 <= week <= 42 else ""
        return ""
    text = _normalize_short_text(value, max_length=40, reject_sentence=True)
    if not text:
        return ""
    compact = text.replace(" ", "")
    if compact.isdigit():
        week = int(compact)
        return f"孕{week}周" if 1 <= week <= 42 else ""
    return compact


def _normalize_yes_no(value: Any) -> str:
    text = _normalize_short_text(value, max_length=12, reject_sentence=True)
    if not text:
        return ""
    normalized = text.strip().lower()
    yes_values = {"是", "对", "是的", "yes", "true", "ivf", "试管", "有"}
    no_values = {"否", "不是", "不是的", "no", "false", "自然怀孕", "自然受孕", "没有", "无"}
    unknown_values = {"不确定", "还不确定", "不知道", "暂不确定"}
    if normalized in yes_values:
        return "是"
    if normalized in no_values:
        return "否"
    if normalized in unknown_values:
        return "不确定"
    return text if text in {"是", "否", "不确定"} else ""


def _normalize_enum(value: Any, allowed: set[str]) -> str:
    text = _normalize_short_text(value, max_length=30, reject_sentence=True)
    if not text:
        return ""
    alias = {
        "自然分娩": "顺产",
        "剖腹产": "剖宫产",
        "计划剖宫产": "剖宫产",
        "母乳喂养": "亲喂母乳",
        "不确定": "还不确定",
    }.get(text, text)
    return alias if alias in allowed else text if text in allowed else ""


def _normalize_text_list(value: Any, *, max_items: int) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = [value]
    items: list[str] = []
    for item in raw_items:
        text = _normalize_short_text(item, max_length=80, reject_sentence=False)
        if text and text not in items:
            items.append(text)
    return items[:max_items]


def _normalize_short_text(value: Any, *, max_length: int, reject_sentence: bool) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "\n" in text or "\r" in text:
        return ""
    if reject_sentence and any(mark in text for mark in ("。", "？", "?", "！", "!", "；", ";")):
        return ""
    if len(text) > max_length:
        return ""
    return text


def _clip_slot_text(value: Any, max_length: int = _MAX_SLOT_TEXT_LENGTH) -> str:
    text = str(value or "").strip()
    return text[:max_length]


def _display_slot_value(value: Any) -> str:
    value = _slot_record_value(value)
    if isinstance(value, list):
        return "、".join(_clip_slot_text(item) for item in value if _clip_slot_text(item))
    return _clip_slot_text(value)


def _slot_value_has_content(value: Any) -> bool:
    value = _slot_record_value(value)
    if isinstance(value, list):
        return any(_slot_value_has_content(item) for item in value)
    if isinstance(value, dict):
        return any(_slot_value_has_content(item) for item in value.values())
    return bool(_clip_slot_text(value))


def _message_sent_at(inputs: RuntimeInputs) -> str:
    value = inputs.get("message_sent_at") or inputs.get("current_date") or ""
    return str(value)


def _unique_strings(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in unique:
            unique.append(text)
    return unique


def _format_device_image_context(state: ContextState) -> list[str]:
    if not state.last_displayed_tool_image and not state.shown_step_image_urls:
        return []

    lines = ["device_image_context:"]
    image = state.last_displayed_tool_image or {}
    if image:
        parts = []
        for key in ("alt", "module", "url"):
            value = str(image.get(key) or "").strip()
            if value:
                parts.append(f"{key}={value}")
        if parts:
            lines.append(f"- last_displayed_tool_image: {'; '.join(parts)}")
        lines.append("- 用户问“图中 / 上图 / 编号 / 标号”时，默认只参考 last_displayed_tool_image；不要从其他历史图片里猜。")
    if state.active_device_module:
        lines.append(f"- active_device_module: {state.active_device_module}")
    if state.shown_step_image_urls:
        lines.append("- shown_step_image_urls: " + ", ".join(state.shown_step_image_urls[-8:]))
        lines.append("- 同一视觉步骤已展示过图片时，后续轮次优先说“对照上图”，不要重复输出同一张 Markdown 图片。")
    return lines


def _format_hospital_bag_cart_context(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    groups = value.get("groups")
    if not isinstance(groups, list):
        return []

    lines = ["current_hospital_bag_cart:"]
    totals = value.get("totals")
    if isinstance(totals, dict):
        total = totals.get("total")
        item_count = totals.get("itemCount") or totals.get("item_count")
        total_text = f"total={total}" if total is not None else ""
        count_text = f"item_count={item_count}" if item_count is not None else ""
        summary = "; ".join(part for part in (total_text, count_text) if part)
        if summary:
            lines.append(f"- {summary}")

    item_lines: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_title = str(group.get("title") or "").strip()
        items = group.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not item_id or not name:
                continue
            price = item.get("price")
            qty = item.get("qty")
            currency = item.get("currency")
            parts = [f"item_id={item_id}", f"name={name}"]
            if price is not None:
                parts.append(f"price={price}")
            if currency is not None:
                parts.append(f"currency={currency}")
            if qty is not None:
                parts.append(f"qty={qty}")
            if group_title:
                parts.append(f"group={group_title}")
            item_lines.append(f"- {'; '.join(parts)}")
            if len(item_lines) >= 30:
                break
        if len(item_lines) >= 30:
            break

    if not item_lines:
        return []
    lines.extend(item_lines)
    return lines
