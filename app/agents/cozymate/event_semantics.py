from __future__ import annotations

from typing import Any


WORK_ITEM = "work_item"
ARTIFACT = "artifact"


def tool_event_semantic(
    *,
    event_type: str,
    tool_name: str,
    tool_call_id: str = "",
    label: str = "",
    safe_args: dict[str, Any] | None = None,
    safe_output: dict[str, Any] | None = None,
    effect_scope: str = "none",
) -> dict[str, Any]:
    normalized_event_type = str(event_type or "").strip()
    normalized_tool_name = str(tool_name or "").strip()
    lifecycle = _tool_lifecycle(normalized_event_type)
    tool_copy = _TOOL_COPY.get(normalized_tool_name)
    surface = WORK_ITEM
    phase = _tool_phase(tool_copy=tool_copy, effect_scope=effect_scope)
    priority = 50

    if lifecycle == "failed":
        phase = "error"
        display_label = _failed_tool_label(tool_name=normalized_tool_name, tool_copy=tool_copy, label=label)
        priority = 90
    elif lifecycle == "completed":
        phase, display_label = _completed_tool_semantic(
            tool_copy=tool_copy,
            tool_name=normalized_tool_name,
            label=label,
            safe_args=safe_args or {},
            safe_output=safe_output or {},
            effect_scope=effect_scope,
        )
        priority = 70
    else:
        phase, display_label = _started_tool_semantic(
            tool_copy=tool_copy,
            tool_name=normalized_tool_name,
            label=label,
            safe_args=safe_args or {},
            effect_scope=effect_scope,
        )
        priority = 45

    return {
        "phase": phase,
        "label": display_label,
        "surface": surface,
        "merge_key": f"tool:{str(tool_call_id or '').strip() or normalized_tool_name or 'unknown'}",
        "priority": priority,
        "lifecycle": lifecycle,
    }


def with_tool_event_semantic(
    payload: dict[str, Any],
    *,
    event_type: str,
    tool_name: str,
    safe_output: dict[str, Any] | None = None,
    effect_scope: str = "none",
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["semantic"] = tool_event_semantic(
        event_type=event_type,
        tool_name=tool_name,
        tool_call_id=str(payload.get("tool_call_id") or payload.get("call_id") or ""),
        label=str(payload.get("label") or ""),
        safe_args=payload.get("safe_args") if isinstance(payload.get("safe_args"), dict) else None,
        safe_output=safe_output,
        effect_scope=effect_scope,
    )
    return enriched


def artifact_event_payload_semantic(*, artifact_type: str, artifact_id: str = "") -> dict[str, Any]:
    normalized_type = str(artifact_type or "").strip()
    return {
        "phase": "done",
        "label": _artifact_completed_label(normalized_type),
        "surface": ARTIFACT,
        "merge_key": f"artifact:{str(artifact_id or '').strip() or normalized_type or 'unknown'}",
        "priority": 70,
        "lifecycle": "completed",
    }


def _started_tool_semantic(
    *,
    tool_copy: dict[str, str] | None,
    tool_name: str,
    label: str,
    safe_args: dict[str, Any],
    effect_scope: str,
) -> tuple[str, str]:
    dynamic_label = _dynamic_tool_started_label(tool_name=tool_name, safe_args=safe_args)
    if dynamic_label:
        return _tool_phase(tool_copy=tool_copy, effect_scope=effect_scope), dynamic_label
    if tool_copy is not None and tool_copy.get("started"):
        return tool_copy.get("phase", "planning"), tool_copy["started"]
    if effect_scope == "none":
        return "reading", f"我先看看{_display_subject(label)}～"
    if effect_scope in {"user_resource", "external_resource"}:
        return "saving", f"我先准备{_display_subject(label)}～"
    return "planning", "我按当前场景继续处理～"


def _completed_tool_semantic(
    *,
    tool_copy: dict[str, str] | None,
    tool_name: str,
    label: str,
    safe_args: dict[str, Any],
    safe_output: dict[str, Any],
    effect_scope: str,
) -> tuple[str, str]:
    output_status = str(safe_output.get("status") or "").strip()
    dynamic_label = _dynamic_tool_completed_label(
        tool_name=tool_name,
        safe_args=safe_args,
        safe_output=safe_output,
    )
    if dynamic_label:
        return _tool_phase(tool_copy=tool_copy, effect_scope=effect_scope), dynamic_label
    if output_status.startswith("needs_"):
        return "planning", "我还需要先确认几件事～"
    if output_status == "entry_already_exists":
        return "saving", "这一天已有记录，我继续补充～"
    if output_status == "entry_not_found":
        return "planning", "这一天还没有可更新的记录"
    if output_status == "entry_unchanged":
        return "reading", "这一天的记录没有变化"
    if safe_output.get("requires_confirmation") is True:
        return "planning", "我已经准备好预览，等你确认～"
    if tool_copy is not None and tool_copy.get("completed"):
        return tool_copy.get("completed_phase", tool_copy.get("phase", "planning")), tool_copy["completed"]
    if effect_scope == "none":
        return "reading", f"我把{_display_subject(label)}整理好啦"
    if effect_scope in {"user_resource", "external_resource"}:
        return "saving", f"我已经保存好{_display_subject(label)}啦"
    return "planning", "我准备好继续处理啦"


def _failed_tool_label(*, tool_name: str, tool_copy: dict[str, str] | None, label: str) -> str:
    if tool_name in {"pregnancy_diary_read", "pregnancy_diary_write"}:
        return "孕期日记这一步暂时没处理好"
    if tool_copy is not None and tool_copy.get("failed"):
        return tool_copy["failed"]
    return f"{_display_subject(label)}暂时没处理好"


def _tool_lifecycle(event_type: str) -> str:
    return {
        "tool.completed": "completed",
        "tool.failed": "failed",
    }.get(event_type, "running")


def _display_subject(label: str) -> str:
    normalized = str(label or "").strip()
    return normalized or "相关信息"


def _tool_phase(*, tool_copy: dict[str, str] | None, effect_scope: str) -> str:
    if tool_copy is not None:
        return tool_copy.get("phase", "planning")
    if effect_scope == "none":
        return "reading"
    if effect_scope in {"user_resource", "external_resource"}:
        return "saving"
    return "planning"


def _dynamic_tool_started_label(*, tool_name: str, safe_args: dict[str, Any]) -> str:
    if tool_name == "pregnancy_diary_read":
        return "我先看看孕期日记～"
    if tool_name == "pregnancy_diary_write":
        return "我先帮你删除孕期日记～" if safe_args.get("operation") == "delete" else "我先帮你保存孕期日记～"
    return ""


def _dynamic_tool_completed_label(
    *,
    tool_name: str,
    safe_args: dict[str, Any],
    safe_output: dict[str, Any],
) -> str:
    status = str(safe_output.get("status") or "").strip()
    if tool_name in {"pregnancy_diary_read", "pregnancy_diary_write"}:
        return {
            "action_failed": "孕期日记这一步暂时没处理好",
            "needs_delete_confirmation": "删除前还需要你确认一下",
            "entry_not_found": "没有找到这条孕期日记",
            "entry_already_exists": "这一天已有记录，我继续补充～",
            "entry_unchanged": "这一天的记录没有变化",
            "diary_entry_deleted": "我已经删除这条孕期日记啦",
            "diary_entry_written": "我已经保存好孕期日记啦",
            "diary_entry_created": "我已经保存好孕期日记啦",
            "diary_entry_updated": "我已经保存好孕期日记啦",
            "diary_list_read": "我看好孕期日记啦",
            "diary_entry_read": "我看好孕期日记啦",
            "entries_read": "我看好孕期日记啦",
            "entry_read": "我看好孕期日记啦",
            "entry_saved": "我已经保存好孕期日记啦",
            "entry_deleted": "我已经删除这条孕期日记啦",
        }.get(status, "孕期日记这一步处理好了")
    if tool_name == "pregnancy_plan_manage":
        return {
            "ready_to_generate": "孕期计划信息已经确认好啦",
            "form_created": "孕期计划信息表已经准备好啦",
            "intake_in_progress": "我整理好这一步信息啦",
            "pregnancy_plan_workflow_paused": "孕期计划已暂停",
            "pregnancy_plan_workflow_resumed": "孕期计划已恢复",
            "card_created": "孕期计划已生成",
            "blocked_by_symptoms": "我先帮你确认当前情况",
        }.get(status, "我整理好这一步信息啦")
    if tool_name == "hospital_bag_manage":
        return {
            "form_created": "待产包信息表已经准备好啦",
            "hospital_bag_intake_already_started": "待产包信息表已经恢复啦",
            "card_created": "我整理好待产包清单啦",
            "hospital_bag_card_already_created": "待产包清单已经生成啦",
            "urgent_care_required": "我先帮你处理需要立即确认的情况",
        }.get(status, "我整理好待产包这一步啦")
    if tool_name == "hospital_bag_cart_write" and status in {"needs_clarification", "cart_unchanged"}:
        return "这次购物车先不改"
    return ""


def _artifact_subject(artifact_type: str) -> str:
    return {
        "milk_analysis_card": "分析卡片",
        "milk_plan_card": "结果卡片",
        "rich_text": "说明内容",
        "rich_text_card": "说明内容",
        "hospital_bag_card": "待产包清单",
        "birth_journey_plan_card": "孕期计划",
    }.get(str(artifact_type or "").strip(), "结果卡片")


def _artifact_completed_label(artifact_type: str) -> str:
    return {
        "form": "我已经准备好确认内容啦",
        "support_ticket": "请确认售后信息",
        "support_ticket_draft": "请确认售后信息",
        "mom_baby_status_card": "我已经整理好宝宝和我页面啦",
        "milk_analysis_card": "我已经整理好奶量分析结果啦",
        "milk_plan_card": "我已经整理好奶量计划啦",
        "hospital_bag_card": "我已经帮你生成好待产包清单啦",
        "birth_journey_plan_card": "我已经帮你整理好孕期计划啦",
    }.get(artifact_type, f"我已经整理好{_artifact_subject(artifact_type)}啦")


_TOOL_COPY: dict[str, dict[str, str]] = {'profile_read': {'phase': 'reading', 'started': '我先看看妈妈和宝宝的基础信息～', 'completed': '我把妈妈和宝宝的基础信息整理好啦'},
 'profile_write': {'phase': 'saving', 'started': '我先帮你更新妈妈和宝宝的基础信息～', 'completed': '我已经保存好妈妈和宝宝的基础信息啦'},
 'lactation_timeline_read': {'phase': 'reading', 'started': '我先看看奶量日程和实际记录～', 'completed': '我把奶量日程和实际记录整理好啦'},
 'lactation_timeline_write': {'phase': 'saving', 'started': '我先帮你处理这项奶量日程或记录～', 'completed': '我已经处理好这项奶量日程或记录啦'},
 'milk_analysis_manage': {'phase': 'evaluating', 'started': '我先核对并分析一下奶量情况～', 'completed': '我把奶量情况分析好啦'},
 'plans_current_read': {'phase': 'reading', 'started': '我先看看计划和日程任务～', 'completed': '我把计划和日程整理好啦'},
 'plans_calendar_read': {'phase': 'reading', 'started': '我先看看计划和日程任务～', 'completed': '我把计划和日程整理好啦'},
 'pregnancy_diary_read': {'phase': 'reading', 'started': '我先看看孕期日记～', 'completed': '我把孕期日记看好啦'},
 'pregnancy_diary_write': {'phase': 'saving', 'started': '我先帮你保存孕期日记～', 'completed': '我已经保存好孕期日记啦'},
 'devices_guidance_manage': {'phase': 'reading', 'started': '我先看看设备指导～', 'completed': '我把设备指导整理好啦'},
 'pump_models_read': {'phase': 'reading', 'started': '我先看看各型号吸奶器的信息～', 'completed': '我已经读取了各型号吸奶器的信息'},
 'conversation_history_image_read': {'phase': 'reading', 'started': '我回看一下之前的图片～', 'completed': '我看清之前那张图片啦'},
 'plans_milk_plan_write': {'phase': 'planning', 'started': '我先帮你整理奶量计划～', 'completed': '我已经准备好奶量计划预览，等你确认～'},
 'pregnancy_plan_manage': {'phase': 'planning', 'started': '我继续处理孕期计划这一步～', 'completed': '我整理好孕期计划这一步啦'},
 'plans_task_write': {'phase': 'planning', 'started': '我先帮你调整这项任务～', 'completed': '我已经处理好这项任务啦'},
 'plans_plan_write': {'phase': 'planning', 'started': '我先帮你确认要删除的计划～', 'completed': '我已经准备好计划删除预览，等你确认～'},
 'notifications_milk_reminder_write': {'phase': 'planning', 'started': '我先帮你准备奶量提醒～', 'completed': '我已经准备好提醒预览，等你确认～'},
 'hospital_bag_manage': {'phase': 'planning', 'started': '我先帮你核对待产包信息～', 'completed': '我整理好待产包这一步啦'},
 'hospital_bag_cart_write': {'phase': 'saving', 'started': '我先帮你调整待产包购物车～', 'completed': '我已经调整好待产包购物车啦'},
 'ibclc_consult_card_write': {'phase': 'planning', 'started': '我先帮你准备 IBCLC 咨询入口～', 'completed': '我已经准备好 IBCLC 咨询入口啦'},
 'support_ticket_write': {'phase': 'planning', 'started': '我先帮你准备售后信息表～', 'completed': '请确认售后信息'}}
