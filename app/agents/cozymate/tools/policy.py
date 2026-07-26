from __future__ import annotations

from typing import Any

from app.agent_runtime.tools.policy import ToolExecutionPolicy

from ..event_semantics import with_tool_event_semantic
from .result import cozymate_model_output_from_payload


_DIARY_TOOLS = frozenset({"diary_read", "diary_mutate"})
_DIARY_PRIVATE_FIELDS = frozenset(
    {
        "attributes",
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
        if tool_name == "milk_analysis_manage" and args.get("operation") == "review":
            return "none"
        return super().effective_effect_scope(tool_name=tool_name, args=args, default=default)

    def safe_args(self, *, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        safe = super().safe_args(tool_name=tool_name, args=args)
        if tool_name not in _DIARY_TOOLS:
            return safe
        if tool_name == "diary_read":
            return _without_private_diary_fields(safe)
        selected = {
            key: safe[key]
            for key in ("operation", "entry_date")
            if key in safe
        }
        selected["provided_field_count"] = sum(1 for key in args if key in _DIARY_PRIVATE_FIELDS)
        return selected

    def safe_output(self, *, tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
        safe = super().safe_output(tool_name=tool_name, output=output)
        return _without_private_diary_fields(safe) if tool_name in _DIARY_TOOLS else safe

    def model_output(self, *, tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
        return cozymate_model_output_from_payload(tool_name=tool_name, output=output)

    def event_label(self, *, tool_name: str, payload: dict[str, Any] | None = None) -> str:
        if tool_name in _DIARY_TOOLS:
            if tool_name == "diary_read":
                return "日记"
            return "删除日记" if (payload or {}).get("operation") == "delete" else "保存日记"
        return {
            "profile_read": "妈妈和宝宝基础信息",
            "profile_update": "更新妈妈和宝宝基础信息",
            "schedule_timeline_read": "日程时间线",
            "schedule_timeline_mutate": "管理日程时间线",
            "milk_analysis_manage": "奶量分析",
            "plan_read": "计划",
            "plan_mutate": "管理计划",
            "pregnancy_intake_manage": "孕期资料采集",
            "devices_guidance_manage": "设备指导",
            "conversation_history_image_read": "历史图片",
            "hospital_bag_manage": "待产包流程",
            "hospital_bag_cart_mutate": "我先帮你调整待产包购物车～",
            "pump_models_read": "吸奶器型号信息",
            "ibclc_consult_card_create": "IBCLC 咨询卡",
            "support_ticket_create": "售后工单草稿",
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
        if key not in _DIARY_PRIVATE_FIELDS
    }


def _redact_private_diary_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _without_private_diary_fields(value)
    if isinstance(value, list):
        return [_redact_private_diary_value(item) for item in value]
    return value
