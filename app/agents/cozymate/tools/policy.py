from __future__ import annotations

from typing import Any

from app.agent_runtime.tools.policy import ToolExecutionPolicy

from ..event_semantics import with_tool_event_semantic


_PREGNANCY_DIARY_TOOLS = frozenset(
    {"pregnancy_diary.query", "pregnancy_diary.save", "pregnancy_diary.delete"}
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
    def safe_args(self, *, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        safe = super().safe_args(tool_name=tool_name, args=args)
        if tool_name not in _PREGNANCY_DIARY_TOOLS:
            return safe
        if tool_name == "pregnancy_diary.query":
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
                "pregnancy_diary.query": "孕期日记",
                "pregnancy_diary.save": "保存孕期日记",
                "pregnancy_diary.delete": "删除孕期日记",
            }[tool_name]
        return {
            "load_service_skill": "加载服务技能",
            "profile.read": "个人资料",
            "profile.update": "更新个人资料",
            "business.context.read": "业务上下文",
            "records.milk_summary.read": "奶量摘要",
            "records.milk_status.read": "奶量状态",
            "records.feeding_record.propose": "喂养记录草稿",
            "records.pumping_record.propose": "吸奶记录草稿",
            "plans.current.read": "计划信息",
            "plans.milk_plan.propose": "泌乳计划草稿",
            "plans.task_create.propose": "任务草稿",
            "plans.task_complete.propose": "任务状态",
            "pregnancy.plan_todo.propose": "孕期计划事项",
            "pregnancy.plan_context.read": "孕期计划上下文",
            "pregnancy.plan_intake.start": "孕期计划信息表",
            "pregnancy.plan_intake.analyze": "孕期计划信息分析",
            "pregnancy.plan.propose": "孕期计划草稿",
            "devices.pump_status.read": "设备状态",
            "devices.guidance.read": "设备指导资料",
            "devices.unboxing.advance": "设备开箱步骤",
            "conversation_history.image.load": "历史图片",
            "birth_plan_form_create": "我先帮你准备确认内容～",
            "labor_communication_card_create": "我先帮你整理分娩沟通单～",
            "hospital_bag_form_create": "我先帮你准备确认内容～",
            "hospital_bag_card_create": "我先帮你整理待产包清单～",
            "hospital_bag_cart_update": "我先帮你调整待产包购物车～",
            "hospital_bag_pump_recommend": "我先帮你看看吸奶器型号～",
            "notifications.milk_reminder.propose": "奶量提醒草稿",
            "support.ticket.propose": "售后工单草稿",
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
