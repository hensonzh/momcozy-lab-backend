from __future__ import annotations

from dataclasses import dataclass, field
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
    client_events: list[str] = field(default_factory=list)
    available_tool_images: list[dict[str, str]] = field(default_factory=list)
    last_displayed_tool_image: dict[str, str] | None = None
    active_device_module: str = ""
    shown_step_image_urls: list[str] = field(default_factory=list)
    birth_prep_slots: dict[str, dict[str, Any]] = field(default_factory=dict)


HOSPITAL_BAG_SLOT_FIELDS = (
    "due_date_or_week",
    "first_birth",
    "fetus_count",
    "pregnancy_history_or_notes",
    "birth_path",
    "feeding_intention",
    "return_to_work_timing",
    "support_person",
    "top_worries",
)
_HOSPITAL_BAG_SLOT_KEY = "hospital_bag"
_PENDING_FIELD_KEY = "_pending_field"
_MAX_SLOT_TEXT_LENGTH = 160
_BIRTH_PREP_SHARED_MEMORY_FIELDS = {"due_date_or_week", "birth_path", "support_person"}


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
        lines.extend(_format_user_profile_context(inputs))
        if state is not None:
            state.environment_sent = True

    lines.append(f"message_sent_at: {_message_sent_at(inputs)}")
    lines.extend(_format_birth_prep_profile_context(inputs))
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
    lines.extend(_format_active_care_plan_context(inputs))
    lines.extend(_format_pregnancy_diary_context(inputs))
    hospital_bag_cart = _format_hospital_bag_cart_context(inputs.get("hospital_bag_cart"))
    if hospital_bag_cart:
        lines.extend(hospital_bag_cart)
    return "\n".join(line for line in lines if line)


def capture_birth_prep_user_message(inputs: RuntimeInputs, state: ContextState) -> None:
    message = str(inputs.get("user_message") or "").strip()
    if not message or "confirmed_form_data:" in message:
        return

    shared_values: dict[str, Any] = {}
    slots = _hospital_bag_slots(state)
    pending_field = _valid_hospital_bag_slot_field(slots.get(_PENDING_FIELD_KEY))
    if pending_field:
        value = _hospital_bag_slot_value_from_text(pending_field, message)
        if _slot_value_has_content(value):
            slots[pending_field] = value
            if pending_field in _BIRTH_PREP_SHARED_MEMORY_FIELDS:
                shared_values[pending_field] = value
        slots.pop(_PENDING_FIELD_KEY, None)

    for field_id, value in _explicit_hospital_bag_slots(message).items():
        if _slot_value_has_content(value):
            slots[field_id] = value
            if field_id in _BIRTH_PREP_SHARED_MEMORY_FIELDS:
                shared_values[field_id] = value
    _persist_birth_prep_shared_memory(inputs, shared_values)


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


def _persist_birth_prep_shared_memory(inputs: RuntimeInputs, values: dict[str, Any]) -> None:
    if not any(_slot_value_has_content(values.get(field_id)) for field_id in _BIRTH_PREP_SHARED_MEMORY_FIELDS):
        return
    user_id = _runtime_user_id(inputs)
    if not user_id:
        return
    try:
        data_store.update_birth_prep_profile_memory(
            user_id=user_id,
            due_date_or_week=values.get("due_date_or_week"),
            birth_path=values.get("birth_path"),
            support_person=values.get("support_person"),
        )
    except Exception:
        return


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
        lines.append("- birth_prep_known_fields: " + "; ".join(values))
        lines.append("- 创建产前表单或待产包表单时复用这些字段作为默认值，让用户在表单里确认或修改，不要重复追问。")
    if pending_field:
        lines.append(f"- hospital_bag_next_field: {pending_field}")
    return lines


def _format_active_care_plan_context(inputs: RuntimeInputs) -> list[str]:
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
    return [
        "active_care_plan_context:",
        "- birth_journey_plan: 已存在 active 生产全过程计划；" + "；".join(part for part in detail_parts if part),
        "- 只要该计划未被删除，就把它视为用户已有计划；用户要求生成/制定生产全过程计划时，不要再次调用 birth_journey_plan_card_create 创建新计划，先说明已有计划并继续查看或推进。",
    ]


def _format_pregnancy_diary_context(inputs: RuntimeInputs) -> list[str]:
    user_id = _runtime_user_id(inputs)
    if not user_id:
        return []
    try:
        entries = data_store.list_pregnancy_diary_entries(user_id=user_id, limit=7)
    except Exception:
        return []
    if not entries:
        return []
    question_count = 0
    tags: list[str] = []
    for entry in entries:
        note = _trim_context_value(entry.get("appointment_note"), 80)
        if note:
            question_count += max(1, len([part for part in re.split(r"[？?\n；;]", note) if part.strip()]))
        for tag in entry.get("symptom_tags") or []:
            text = _trim_context_value(tag, 20)
            if text and text not in tags:
                tags.append(text)
    latest = entries[0]
    latest_parts = [
        _trim_context_value(latest.get("entry_date"), 20),
        _trim_context_value(latest.get("gestational_week"), 20),
        _trim_context_value(latest.get("mood"), 30),
        _trim_context_value(latest.get("fetal_movement"), 40),
    ]
    detail_parts = [
        f"recent_days={len(entries)}",
        f"appointment_questions={question_count}",
        f"recent_tags={','.join(tags[:5])}" if tags else "",
        f"latest={'/'.join(part for part in latest_parts if part)}",
    ]
    return [
        "pregnancy_diary_context:",
        "- " + "；".join(part for part in detail_parts if part),
        "- 需要查看、整理、写入、更新或删除孕期日记时，使用 pregnancy_diary_manage；不要仅凭摘要臆造完整记录。",
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
    due = _trim_context_value(profile.get("birth_prep_due_date_or_week"), 80)
    birth_path = _trim_context_value(profile.get("birth_prep_birth_path"), 80)
    support_person = _trim_context_value(profile.get("birth_prep_support_person"), 80)
    details = [
        f"due_date_or_week={due}" if due else "",
        f"birth_path={birth_path}" if birth_path else "",
        f"support_person={support_person}" if support_person else "",
    ]
    context_line = "；".join(part for part in details if part)
    if not context_line:
        return []
    return [
        "birth_prep_profile_context:",
        f"- {context_line}",
        "- 这些是生产全过程计划、待产包和分娩沟通单共享的已确认信息；相关服务优先复用，不要重复询问同一个已知字段。",
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


def _explicit_hospital_bag_slots(message: str) -> dict[str, Any]:
    slots: dict[str, Any] = {}

    due = _extract_due_or_week(message)
    if due:
        slots["due_date_or_week"] = due

    first_birth = _extract_first_birth(message)
    if first_birth:
        slots["first_birth"] = first_birth

    fetus_count = _extract_fetus_count(message)
    if fetus_count:
        slots["fetus_count"] = fetus_count

    birth_path = _extract_birth_path(message)
    if birth_path:
        slots["birth_path"] = birth_path

    feeding = _extract_feeding_intention(message)
    if feeding:
        slots["feeding_intention"] = feeding

    support = _extract_support_person(message)
    if support:
        slots["support_person"] = support

    history = _extract_pregnancy_history(message)
    if history:
        slots["pregnancy_history_or_notes"] = history

    if _mentions_return_to_work(message):
        slots["return_to_work_timing"] = _clip_slot_text(message)

    worries = _extract_top_worries(message)
    if worries:
        slots["top_worries"] = worries

    return slots


def _hospital_bag_slot_value_from_text(field_id: str, message: str) -> Any:
    if field_id == "due_date_or_week":
        return _extract_due_or_week(message) or _extract_bare_pregnancy_week(message)
    if field_id == "top_worries":
        return _extract_top_worries(message) or [_clip_slot_text(message)]
    if field_id == "first_birth":
        return _extract_first_birth(message)
    if field_id == "fetus_count":
        return _extract_fetus_count(message)
    if field_id == "birth_path":
        return _extract_birth_path(message)
    if field_id == "feeding_intention":
        return _extract_feeding_intention(message)
    if field_id == "support_person":
        return _extract_support_person(message)
    if field_id == "pregnancy_history_or_notes":
        return _extract_pregnancy_history(message)
    if field_id == "return_to_work_timing" and _mentions_return_to_work(message):
        return _clip_slot_text(message)
    return _clip_slot_text(message)


def _infer_pending_hospital_bag_field(message: str) -> str:
    text = str(message or "")
    if any(token in text for token in ("最担心", "焦虑", "怕漏", "怕住院", "怕母乳", "担心的")):
        return "top_worries"
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


def _extract_bare_pregnancy_week(message: str) -> str:
    text = str(message or "").strip()
    if not re.fullmatch(r"\d{1,2}", text):
        return ""
    week = int(text)
    if 1 <= week <= 42:
        return f"孕{week}周"
    return ""


def _mentions_return_to_work(message: str) -> bool:
    text = str(message or "")
    return any(token in text for token in ("返工", "复工", "上班", "外出", "工作"))


def _extract_first_birth(message: str) -> str:
    text = str(message or "")
    if any(token in text for token in ("不是第一胎", "不是头胎", "二胎", "第二胎", "三胎", "第3胎")):
        return "否"
    if any(token in text for token in ("第一胎", "头胎", "一胎", "第1胎")):
        return "是"
    stripped = text.strip()
    if stripped in {"是", "对", "是的"}:
        return "是"
    if stripped in {"否", "不是", "不是的"}:
        return "否"
    return ""


def _extract_fetus_count(message: str) -> str:
    text = str(message or "")
    if any(token in text for token in ("三胎", "三胞胎", "多胎", "三胎及以上")):
        return "三胎及以上"
    if any(token in text for token in ("双胎", "双胞胎")):
        return "双胎"
    if "单胎" in text:
        return "单胎"
    return ""


def _extract_birth_path(message: str) -> str:
    text = str(message or "")
    if any(token in text for token in ("剖宫产", "剖腹产", "刨腹产", "剖产")):
        return "剖宫产"
    if any(token in text for token in ("顺产", "自然分娩")):
        return "顺产"
    if any(token in text for token in ("分娩方式还没确定", "生产方式还没确定", "还没确定分娩方式", "可能剖", "不确定顺产")):
        return "还不确定"
    return ""


def _extract_feeding_intention(message: str) -> str:
    text = str(message or "")
    if any(token in text for token in ("混合喂养", "混合")):
        return "混合喂养"
    if any(token in text for token in ("配方奶", "奶粉", "配方")):
        return "配方奶"
    if any(token in text for token in ("母乳", "亲喂", "纯泵", "泵奶")):
        return "亲喂母乳"
    if any(token in text for token in ("喂养还不确定", "还不确定怎么喂", "还没想好怎么喂")):
        return "还不确定"
    return ""


def _extract_support_person(message: str) -> str:
    text = str(message or "")
    if any(token in text for token in ("支持少", "没人帮", "没人照顾", "一个人", "主要自己")):
        return "支持少"
    if any(token in text for token in ("白天自己", "白天主要自己")):
        return "白天主要自己"
    if any(token in text for token in ("夜里自己", "夜间自己", "夜间主要自己")):
        return "夜间主要自己"
    if any(token in text for token in ("老公", "丈夫", "伴侣", "妈妈", "婆婆", "家人", "有人帮", "有人陪", "陪我", "全天帮")):
        return "有人全天帮忙"
    if any(token in text for token in ("支持人还不确定", "暂时没有支持人", "不确定谁陪")):
        return "不确定"
    return ""


def _extract_pregnancy_history(message: str) -> list[str]:
    text = str(message or "")
    if any(token in text for token in ("没有特殊情况", "医生没说特殊", "医生没有提示", "没有高危")):
        return ["没有"]
    values: list[str] = []
    for token, value in (
        ("妊娠糖尿病", "妊娠糖尿病"),
        ("血压", "血压或子痫前期风险"),
        ("子痫", "血压或子痫前期风险"),
        ("胎盘", "胎盘问题"),
        ("早产", "早产风险"),
        ("nicu", "宝宝可能 NICU"),
        ("NICU", "宝宝可能 NICU"),
    ):
        if token in text and value not in values:
            values.append(value)
    return values


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
