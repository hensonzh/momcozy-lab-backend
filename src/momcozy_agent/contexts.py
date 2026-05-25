from __future__ import annotations

from dataclasses import dataclass, field

from .types import RuntimeInputs

DEFAULT_LOCALE = "en-US"
DEFAULT_TIMEZONE = "America/Los_Angeles"


@dataclass
class ContextState:
    environment_sent: bool = False
    loaded_references: list[str] = field(default_factory=list)
    client_events: list[str] = field(default_factory=list)


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
    hospital_bag_cart = _format_hospital_bag_cart_context(inputs.get("hospital_bag_cart"))
    if hospital_bag_cart:
        lines.extend(hospital_bag_cart)
    return "\n".join(line for line in lines if line)


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
