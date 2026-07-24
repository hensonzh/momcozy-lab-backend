from __future__ import annotations

from typing import Any

from app.agent_runtime.tools.policy import ToolExecutionPolicy

from ..event_semantics import with_tool_event_semantic


_PREGNANCY_DIARY_TOOLS = frozenset(
    {"pregnancy_diary_query", "pregnancy_diary_save", "pregnancy_diary_delete"}
)
_PREGNANCY_DIARY_PRIVATE_FIELDS = frozenset(
    {
        "gestational_week",
        "mood",
        "energy_level",
        "sleep_summary",
        "fetal_movement",
        "symptom_tags",
        "appointment_note",
        "nutrition_note",
        "content",
        "content_summary",
        "confirmation_evidence",
        "attachments",
    }
)


class CozymateToolExecutionPolicy(ToolExecutionPolicy):
    def effective_effect_scope(self, *, tool_name: str, args: dict[str, Any], default: str) -> str:
        if tool_name == "pregnancy_plan_workflow" and args.get("command") != "generate_plan":
            return "agent_internal"
        return super().effective_effect_scope(tool_name=tool_name, args=args, default=default)

    def safe_args(self, *, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        safe = super().safe_args(tool_name=tool_name, args=args)
        if tool_name not in _PREGNANCY_DIARY_TOOLS:
            return safe
        if tool_name == "pregnancy_diary_query":
            return _without_private_diary_fields(safe)
        selected = {key: safe[key] for key in ("operation", "entry_date") if key in safe}
        selected["provided_field_count"] = sum(1 for key in args if key in _PREGNANCY_DIARY_PRIVATE_FIELDS)
        return selected

    def safe_output(self, *, tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
        safe = super().safe_output(tool_name=tool_name, output=output)
        return _without_private_diary_fields(safe) if tool_name in _PREGNANCY_DIARY_TOOLS else safe

    def event_label(self, *, tool_name: str, payload: dict[str, Any] | None = None) -> str:
        if tool_name in _PREGNANCY_DIARY_TOOLS:
            return {
                "pregnancy_diary_query": "孕期日记",
                "pregnancy_diary_save": "保存孕期日记",
                "pregnancy_diary_delete": "删除孕期日记",
            }[tool_name]
        return {
            "profile_read": "个人资料",
            "profile_update": "更新个人资料",
            "lactation_context_read": "母婴泌乳基础信息",
            "records_milk_summary_read": "奶量摘要",
            "records_milk_status_read": "奶量状态",
            "records_feeding_record_propose": "喂养记录草稿",
            "records_pumping_record_propose": "吸奶记录草稿",
            "plans_current_read": "计划信息",
            "plans_milk_plan_propose": "泌乳计划草稿",
            "plans_task_create_propose": "任务草稿",
            "plans_task_complete_propose": "任务状态",
            "pregnancy_plan_workflow": "孕期计划流程",
            "devices_guidance": "设备指导",
            "conversation_history_image_load": "历史图片",
            "hospital_bag_workflow": "待产包流程",
            "hospital_bag_cart_update": "我先帮你调整待产包购物车～",
            "pump_models_read": "吸奶器型号信息",
            "notifications_milk_reminder_propose": "奶量提醒草稿",
            "support_ticket_propose": "售后工单草稿",
        }.get(tool_name, "相关信息")

    def enrich_event(
        self,
        payload: dict[str, Any],
        *,
        event_type: str,
        tool_name: str,
        safe_output: dict[str, Any] | None = None,
        effect_scope: str = "none",
    ) -> dict[str, Any]:
        return with_tool_event_semantic(
            payload,
            event_type=event_type,
            tool_name=tool_name,
            safe_output=safe_output,
            effect_scope=effect_scope,
        )


def _without_private_diary_fields(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _redact_private_diary_value(item)
        for key, item in value.items()
        if key not in _PREGNANCY_DIARY_PRIVATE_FIELDS
    }


def _redact_private_diary_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _without_private_diary_fields(value)
    if isinstance(value, list):
        return [_redact_private_diary_value(item) for item in value]
    return value
