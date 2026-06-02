from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .types import RuntimeInputs

DEFAULT_LOCALE = "en-US"
DEFAULT_TIMEZONE = "America/Los_Angeles"


@dataclass
class ContextState:
    environment_sent: bool = False
    loaded_references: list[str] = field(default_factory=list)
    client_events: list[str] = field(default_factory=list)
    available_tool_images: list[dict[str, str]] = field(default_factory=list)
    last_displayed_tool_image: dict[str, str] | None = None
    active_device_module: str = ""
    shown_step_image_urls: list[str] = field(default_factory=list)
    birth_prep_slots: dict[str, dict[str, Any]] = field(default_factory=dict)


HOSPITAL_BAG_SLOT_FIELDS = (
    "due_date_or_week",
    "return_to_work_timing",
    "budget_preference",
    "top_worries",
)
_HOSPITAL_BAG_SLOT_KEY = "hospital_bag"
_PENDING_FIELD_KEY = "_pending_field"
_MAX_SLOT_TEXT_LENGTH = 160


def build_request_context(
    inputs: RuntimeInputs,
    state: ContextState | None = None,
    loaded_skill_ids: list[str] | None = None,
) -> str:
    include_environment = state is None or not state.environment_sent
    lines = ["request_context:"]

    if include_environment:
        lines.append(f"locale: {inputs.get('locale') or DEFAULT_LOCALE}")
        lines.append(f"timezone: {inputs.get('timezone') or DEFAULT_TIMEZONE}")
        if state is not None:
            state.environment_sent = True

    lines.append(f"message_sent_at: {_message_sent_at(inputs)}")
    if loaded_skill_ids:
        lines.append("loaded_skill_context:")
        for skill_id in _unique_strings(loaded_skill_ids):
            lines.append(
                f"- {skill_id}/SKILL.md 已在当前会话中读取过；连续同一服务任务优先复用，不要重复调用 load_skill，除非用户切换服务或需要新的未读资料。"
            )
    if state is not None and state.loaded_references:
        lines.append("loaded_reference_context:")
        for reference in state.loaded_references:
            lines.append(f"- {reference}")
    if state is not None and state.client_events:
        lines.append("client_event_context:")
        for event in state.client_events[-5:]:
            lines.append(f"- {event}")
    if state is not None:
        lines.extend(_format_birth_prep_context(state))
        lines.extend(_format_device_image_context(state))
    hospital_bag_cart = _format_hospital_bag_cart_context(inputs.get("hospital_bag_cart"))
    if hospital_bag_cart:
        lines.extend(hospital_bag_cart)
    return "\n".join(line for line in lines if line)


def capture_birth_prep_user_message(inputs: RuntimeInputs, state: ContextState) -> None:
    message = str(inputs.get("user_message") or "").strip()
    if not message or "confirmed_form_data:" in message:
        return

    slots = _hospital_bag_slots(state)
    pending_field = _valid_hospital_bag_slot_field(slots.get(_PENDING_FIELD_KEY))
    if pending_field:
        value = _hospital_bag_slot_value_from_text(pending_field, message)
        if _slot_value_has_content(value):
            slots[pending_field] = value
        slots.pop(_PENDING_FIELD_KEY, None)

    for field_id, value in _explicit_hospital_bag_slots(message).items():
        if _slot_value_has_content(value):
            slots[field_id] = value


def record_birth_prep_assistant_message(state: ContextState, message: str) -> None:
    pending_field = _infer_pending_hospital_bag_field(message)
    if pending_field:
        _hospital_bag_slots(state)[_PENDING_FIELD_KEY] = pending_field


def merge_hospital_bag_slots(state: ContextState, values: dict[str, Any]) -> None:
    slots = _hospital_bag_slots(state)
    for field_id in HOSPITAL_BAG_SLOT_FIELDS:
        value = values.get(field_id)
        if _slot_value_has_content(value):
            slots[field_id] = value


def hospital_bag_slots(state: ContextState | None) -> dict[str, Any]:
    if state is None:
        return {}
    slots = state.birth_prep_slots.get(_HOSPITAL_BAG_SLOT_KEY)
    if not isinstance(slots, dict):
        return {}
    return {
        field_id: slots[field_id]
        for field_id in HOSPITAL_BAG_SLOT_FIELDS
        if _slot_value_has_content(slots.get(field_id))
    }


def set_pending_hospital_bag_slot(state: ContextState, field_id: str) -> None:
    valid_field = _valid_hospital_bag_slot_field(field_id)
    if valid_field:
        _hospital_bag_slots(state)[_PENDING_FIELD_KEY] = valid_field


def clear_pending_hospital_bag_slot(state: ContextState) -> None:
    _hospital_bag_slots(state).pop(_PENDING_FIELD_KEY, None)


def _hospital_bag_slots(state: ContextState) -> dict[str, Any]:
    slots = state.birth_prep_slots.get(_HOSPITAL_BAG_SLOT_KEY)
    if not isinstance(slots, dict):
        slots = {}
        state.birth_prep_slots[_HOSPITAL_BAG_SLOT_KEY] = slots
    return slots


def _format_birth_prep_context(state: ContextState) -> list[str]:
    slots = state.birth_prep_slots.get(_HOSPITAL_BAG_SLOT_KEY)
    if not isinstance(slots, dict):
        return []
    values = [
        f"{field_id}={_display_slot_value(slots[field_id])}"
        for field_id in HOSPITAL_BAG_SLOT_FIELDS
        if _slot_value_has_content(slots.get(field_id))
    ]
    pending_field = _valid_hospital_bag_slot_field(slots.get(_PENDING_FIELD_KEY))
    if not values and not pending_field:
        return []
    lines = ["birth_prep_context:"]
    if values:
        lines.append("- hospital_bag_confirmed_fields: " + "; ".join(values))
        lines.append("- 创建待产包表单时复用这些字段，不要让用户重复回答。")
    if pending_field:
        lines.append(f"- hospital_bag_next_field: {pending_field}")
    return lines


def _explicit_hospital_bag_slots(message: str) -> dict[str, Any]:
    slots: dict[str, Any] = {}

    due = _extract_due_or_week(message)
    if due:
        slots["due_date_or_week"] = due

    budget = _normalize_budget_preference(message)
    if budget:
        slots["budget_preference"] = budget

    if _mentions_return_to_work(message):
        slots["return_to_work_timing"] = _clip_slot_text(message)

    worries = _extract_top_worries(message)
    if worries:
        slots["top_worries"] = worries

    return slots


def _hospital_bag_slot_value_from_text(field_id: str, message: str) -> Any:
    if field_id == "due_date_or_week":
        return _extract_due_or_week(message) or _clip_slot_text(message)
    if field_id == "budget_preference":
        return _normalize_budget_preference(message) or _clip_slot_text(message)
    if field_id == "top_worries":
        return _extract_top_worries(message) or [_clip_slot_text(message)]
    return _clip_slot_text(message)


def _infer_pending_hospital_bag_field(message: str) -> str:
    text = str(message or "")
    if any(token in text for token in ("最担心", "焦虑", "怕漏", "怕住院", "怕母乳", "担心的")):
        return "top_worries"
    if any(token in text for token in ("预算", "低预算", "中预算", "高预算", "舒适", "省钱")):
        return "budget_preference"
    if any(token in text for token in ("返工", "复工", "上班", "外出计划", "回去工作")):
        return "return_to_work_timing"
    if any(token in text for token in ("孕几周", "孕周", "预产期", "哪天生", "什么时候生")):
        return "due_date_or_week"
    return ""


def _valid_hospital_bag_slot_field(value: Any) -> str:
    field_id = str(value or "").strip()
    return field_id if field_id in HOSPITAL_BAG_SLOT_FIELDS else ""


def _extract_due_or_week(message: str) -> str:
    text = str(message or "").strip()
    week_match = re.search(r"孕?\s*(\d{1,2})\s*(?:周|週)(?:\s*[+＋]\s*(\d)\s*天?)?", text)
    if week_match:
        week = week_match.group(1)
        days = week_match.group(2)
        return f"孕{week}周" + (f"+{days}天" if days else "")
    due_match = re.search(r"预产期\s*(?:是|在|:|：)?\s*([0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2}|[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*[日号]?)", text)
    if due_match:
        return f"预产期{due_match.group(1).replace(' ', '')}"
    return ""


def _normalize_budget_preference(message: str) -> str:
    text = str(message or "")
    if "高预算" in text or "舒适" in text or "不太在意价格" in text:
        return "高预算"
    if "中预算" in text or "稳妥" in text or "性价比" in text:
        return "中预算"
    if "低预算" in text or "省钱" in text or "够用" in text:
        return "低预算"
    return ""


def _mentions_return_to_work(message: str) -> bool:
    text = str(message or "")
    return any(token in text for token in ("返工", "复工", "上班", "外出", "工作"))


def _extract_top_worries(message: str) -> list[str]:
    text = str(message or "").strip()
    if not any(token in text for token in ("担心", "焦虑", "怕")):
        return []
    cleaned = re.sub(r"^(最)?(担心|焦虑)(的)?(三件事|事情)?(是|有|：|:)?", "", text).strip()
    parts = [
        _clip_slot_text(part)
        for part in re.split(r"[、，,；;。]\s*", cleaned)
        if _clip_slot_text(part)
    ]
    return parts[:3] if parts else []


def _clip_slot_text(value: Any) -> str:
    text = str(value or "").strip()
    return text[:_MAX_SLOT_TEXT_LENGTH]


def _display_slot_value(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(_clip_slot_text(item) for item in value if _clip_slot_text(item))
    return _clip_slot_text(value)


def _slot_value_has_content(value: Any) -> bool:
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
