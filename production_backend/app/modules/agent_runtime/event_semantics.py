from __future__ import annotations

from typing import Any
from uuid import UUID


STATUS_BAR = "status_bar"
THINKING_NOTE = "thinking_note"
WORK_ITEM = "work_item"
ARTIFACT = "artifact"
ACTION = "action"
HIDDEN = "hidden"


def run_progress_payload(*, phase: str, label: str) -> dict[str, Any]:
    normalized_phase = str(phase or "").strip()
    normalized_label = str(label or "").strip()
    semantic = run_progress_semantic(phase=normalized_phase, label=normalized_label)
    return {
        "phase": normalized_phase,
        "label": normalized_label,
        "semantic": semantic,
    }


def run_progress_semantic(*, phase: str, label: str) -> dict[str, Any]:
    defaults = {
        "context_loading": {
            "phase": "thinking",
            "label": "我已经收到你的消息啦～",
            "surface": STATUS_BAR,
            "priority": 10,
        },
        "context_ready": {
            "phase": "reading",
            "label": "我先理解一下你的需求～",
            "surface": STATUS_BAR,
            "priority": 20,
        },
        "model_reasoning": {
            "phase": "thinking",
            "label": "我想一下",
            "surface": THINKING_NOTE,
            "priority": 40,
        },
        "model_reasoning_after_tool": {
            "phase": "thinking",
            "label": "我想一下",
            "surface": THINKING_NOTE,
            "priority": 60,
        },
        "model_followup": {
            "phase": "thinking",
            "label": "我接着处理下一步",
            "surface": STATUS_BAR,
            "priority": 55,
        },
        "response_finalizing": {
            "phase": "replying",
            "label": "我在组织回复～",
            "surface": STATUS_BAR,
            "priority": 80,
        },
        "quick_replies_preparing": {
            "phase": "planning",
            "label": "我在帮你准备下一轮的快捷输入～",
            "surface": STATUS_BAR,
            "priority": 85,
        },
    }
    preset = defaults.get(
        phase,
        {
            "phase": "thinking",
            "label": label or "我按当前场景继续处理～",
            "surface": STATUS_BAR,
            "priority": 50,
        },
    )
    surface = str(preset["surface"])
    display_label = label or str(preset["label"])
    return {
        "phase": str(preset["phase"]),
        "label": display_label,
        "surface": surface,
        "visibility": _legacy_visibility(surface),
        "merge_key": f"progress:{phase or 'unknown'}",
        "priority": int(preset["priority"]),
        "lifecycle": "running",
    }


def progress_live_dedupe_key(*, run_id: UUID, semantic: dict[str, Any]) -> str:
    merge_key = str(semantic.get("merge_key") or "").strip()
    return f"{run_id}:run.progress:{merge_key}" if merge_key else ""


def tool_event_semantic(
    *,
    event_type: str,
    tool_name: str,
    tool_call_id: str = "",
    label: str = "",
    safe_args: dict[str, Any] | None = None,
    safe_output: dict[str, Any] | None = None,
    read_or_write: str = "",
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    normalized_event_type = str(event_type or "").strip()
    normalized_tool_name = str(tool_name or "").strip()
    lifecycle = _tool_lifecycle(normalized_event_type)
    tool_copy = _TOOL_COPY.get(normalized_tool_name)
    surface = WORK_ITEM
    phase = "reading" if read_or_write == "read" else "planning"
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
            read_or_write=read_or_write,
            requires_confirmation=requires_confirmation,
        )
        priority = 70
    else:
        phase, display_label = _started_tool_semantic(
            tool_copy=tool_copy,
            tool_name=normalized_tool_name,
            label=label,
            safe_args=safe_args or {},
            read_or_write=read_or_write,
        )
        priority = 45

    return {
        "phase": phase,
        "label": display_label,
        "surface": surface,
        "visibility": _legacy_visibility(surface),
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
    read_or_write: str = "",
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["semantic"] = tool_event_semantic(
        event_type=event_type,
        tool_name=tool_name,
        tool_call_id=str(payload.get("tool_call_id") or payload.get("call_id") or ""),
        label=str(payload.get("label") or ""),
        safe_args=payload.get("safe_args") if isinstance(payload.get("safe_args"), dict) else None,
        safe_output=safe_output,
        read_or_write=read_or_write,
        requires_confirmation=requires_confirmation,
    )
    return enriched


def artifact_event_payload_semantic(*, artifact_type: str, artifact_id: str = "") -> dict[str, Any]:
    normalized_type = str(artifact_type or "").strip()
    return {
        "phase": "done",
        "label": _artifact_completed_label(normalized_type),
        "surface": ARTIFACT,
        "visibility": ARTIFACT,
        "merge_key": f"artifact:{str(artifact_id or '').strip() or normalized_type or 'unknown'}",
        "priority": 70,
        "lifecycle": "completed",
    }


def action_event_payload_semantic(*, action_status: str, action_id: str = "") -> dict[str, Any]:
    normalized_status = str(action_status or "").strip()
    label = {
        "queued": "我已经提交这次操作啦",
        "applied": "我已经保存好这次修改啦",
        "rejected": "这次操作已经取消",
        "failed": "这次操作暂时没处理好",
    }.get(normalized_status, "我需要你确认一下，再继续处理")
    lifecycle = (
        "failed" if normalized_status == "failed" else "completed" if normalized_status in {"queued", "applied", "rejected"} else "running"
    )
    return {
        "phase": "saving" if normalized_status in {"queued", "applied"} else "planning",
        "label": label,
        "surface": ACTION,
        "visibility": ACTION,
        "merge_key": f"action:{str(action_id or '').strip() or normalized_status or 'pending'}",
        "priority": 75,
        "lifecycle": lifecycle,
    }


def run_event_payload_semantic(*, event_type: str, run_id: str) -> dict[str, Any]:
    normalized_type = str(event_type or "").strip()
    normalized_run_id = str(run_id or "").strip() or "current"
    presets = {
        "run.queued": ("thinking", "我已经收到你的消息啦～", STATUS_BAR, "running", 10),
        "run.started": ("thinking", "我已经收到你的消息啦～", STATUS_BAR, "running", 10),
        "run.waiting_for_confirmation": ("confirming", "我需要你确认一下，再继续处理", ACTION, "running", 90),
        "run.completed": ("done", "我处理好啦", HIDDEN, "completed", 100),
        "run.failed": ("error", "这轮暂时没处理好", STATUS_BAR, "failed", 100),
        "run.cancelled": ("done", "本轮已停止", HIDDEN, "completed", 100),
    }
    phase, label, surface, lifecycle, priority = presets.get(
        normalized_type,
        ("working", "", HIDDEN, "running", 0),
    )
    return {
        "phase": phase,
        "label": label,
        "surface": surface,
        "visibility": _legacy_visibility(surface),
        "merge_key": f"run:{normalized_run_id}",
        "priority": priority,
        "lifecycle": lifecycle,
    }


def with_run_event_semantic(payload: dict[str, Any], *, event_type: str, run_id: str) -> dict[str, Any]:
    if not str(event_type or "").startswith("run."):
        return payload
    enriched = dict(payload)
    enriched["semantic"] = run_event_payload_semantic(event_type=event_type, run_id=run_id)
    return enriched


def web_search_event_semantic(*, status: str) -> dict[str, Any]:
    normalized_status = str(status or "").strip().lower()
    if normalized_status == "completed":
        phase, label, lifecycle = "done", "我查好专业资料啦", "completed"
    elif normalized_status == "failed":
        phase, label, lifecycle = "error", "专业资料暂时没查好", "failed"
    else:
        phase, label, lifecycle = "reading", "我在查专业资料～", "running"
    return {
        "phase": phase,
        "label": label,
        "surface": WORK_ITEM,
        "visibility": WORK_ITEM,
        "merge_key": "web_search:current",
        "priority": 55,
        "lifecycle": lifecycle,
    }


def _started_tool_semantic(
    *,
    tool_copy: dict[str, str] | None,
    tool_name: str,
    label: str,
    safe_args: dict[str, Any],
    read_or_write: str,
) -> tuple[str, str]:
    dynamic_label = _dynamic_tool_started_label(tool_name=tool_name, safe_args=safe_args)
    if dynamic_label:
        return _tool_phase(tool_copy=tool_copy, read_or_write=read_or_write), dynamic_label
    if tool_copy is not None and tool_copy.get("started"):
        return tool_copy.get("phase", "planning"), tool_copy["started"]
    if read_or_write == "read":
        return "reading", f"我先看看{_display_subject(label)}～"
    if read_or_write == "write":
        return "saving", f"我先准备{_display_subject(label)}～"
    return "planning", "我按当前场景继续处理～"


def _completed_tool_semantic(
    *,
    tool_copy: dict[str, str] | None,
    tool_name: str,
    label: str,
    safe_args: dict[str, Any],
    safe_output: dict[str, Any],
    read_or_write: str,
    requires_confirmation: bool,
) -> tuple[str, str]:
    output_status = str(safe_output.get("status") or "").strip()
    dynamic_label = _dynamic_tool_completed_label(
        tool_name=tool_name,
        safe_args=safe_args,
        safe_output=safe_output,
    )
    if dynamic_label:
        return _tool_phase(tool_copy=tool_copy, read_or_write=read_or_write), dynamic_label
    if output_status.startswith("needs_"):
        return "planning", "我还需要先确认几件事～"
    if output_status == "entry_already_exists":
        return "saving", "这一天已有记录，我继续补充～"
    if output_status == "entry_not_found":
        return "planning", "这一天还没有可更新的记录"
    if output_status == "entry_unchanged":
        return "reading", "这一天的记录没有变化"
    if output_status == "existing_plan_found":
        return "reading", "我找到已有的孕期计划啦"
    if safe_output.get("requires_confirmation") is True or requires_confirmation:
        return "planning", "我已经准备好预览，等你确认～"
    if tool_copy is not None and tool_copy.get("completed"):
        return tool_copy.get("completed_phase", tool_copy.get("phase", "planning")), tool_copy["completed"]
    if read_or_write == "read":
        return "reading", f"我把{_display_subject(label)}整理好啦"
    if read_or_write == "write":
        return "saving", f"我已经保存好{_display_subject(label)}啦"
    return "planning", "我准备好继续处理啦"


def _failed_tool_label(*, tool_name: str, tool_copy: dict[str, str] | None, label: str) -> str:
    if tool_name == "pregnancy_diary.manage":
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


def _legacy_visibility(surface: str) -> str:
    return {
        STATUS_BAR: "status",
        WORK_ITEM: "work_item",
        ARTIFACT: "artifact",
        ACTION: "action",
    }.get(surface, "hidden")


def _tool_phase(*, tool_copy: dict[str, str] | None, read_or_write: str) -> str:
    if tool_copy is not None:
        return tool_copy.get("phase", "planning")
    return "reading" if read_or_write == "read" else "saving" if read_or_write == "write" else "planning"


def _dynamic_tool_started_label(*, tool_name: str, safe_args: dict[str, Any]) -> str:
    if tool_name != "pregnancy_diary.manage":
        return ""
    action = str(safe_args.get("action") or "").strip()
    if action in {"write", "update", "create"}:
        return "我先帮你保存孕期日记～"
    if action == "delete":
        return "我先帮你删除孕期日记～"
    return "我先看看孕期日记～"


def _dynamic_tool_completed_label(
    *,
    tool_name: str,
    safe_args: dict[str, Any],
    safe_output: dict[str, Any],
) -> str:
    status = str(safe_output.get("status") or "").strip()
    if tool_name == "pregnancy_diary.manage":
        return {
            "needs_delete_confirmation": "删除前还需要你确认一下",
            "entry_not_found": "没有找到这条孕期日记",
            "entry_already_exists": "这一天已有记录，我继续补充～",
            "entry_unchanged": "这一天的记录没有变化",
            "diary_entry_deleted": "我已经删除这条孕期日记啦",
            "diary_entry_written": "我已经保存好孕期日记啦",
            "diary_entry_created": "我已经保存好孕期日记啦",
            "diary_entry_updated": "我已经保存好孕期日记啦",
            "health_consultation_recorded": "我已经记录到孕期日记啦",
            "health_consultation_updated": "我已经记录到孕期日记啦",
            "diary_list_read": "我看好孕期日记啦",
            "diary_entry_read": "我看好孕期日记啦",
        }.get(status, "孕期日记这一步处理好了")
    if tool_name == "pregnancy.plan_intake.advance":
        return {
            "ready_to_generate": "孕期计划信息已经确认好啦",
            "blocked_by_symptoms": "我先帮你确认当前情况",
        }.get(status, "我整理好这一步信息啦")
    if tool_name == "hospital_bag_cart_update" and status in {"needs_clarification", "cart_unchanged"}:
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
        "labor_communication_card": "分娩沟通单",
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
        "labor_communication_card": "我已经帮你整理好分娩沟通单啦",
    }.get(artifact_type, f"我已经整理好{_artifact_subject(artifact_type)}啦")


_TOOL_COPY: dict[str, dict[str, str]] = {
    "load_service_skill": {
        "phase": "planning",
        "started": "我先准备一下这个场景～",
        "completed": "我准备好继续处理啦",
        "failed": "这个场景暂时没准备好",
    },
    "profile.read": {
        "phase": "reading",
        "started": "我先看看你的基础信息～",
        "completed": "我把基础信息看好啦",
    },
    "profile_update": {
        "phase": "saving",
        "started": "我先帮你记一下基础信息～",
        "completed": "我已经保存好基础信息啦",
    },
    "business.context.read": {
        "phase": "reading",
        "started": "我先看看相关业务信息～",
        "completed": "我把相关业务信息整理好啦",
    },
    "records.milk_status.read": {
        "phase": "reading",
        "started": "我先看看今天的奶量状态～",
        "completed": "我看好今天的奶量状态啦",
    },
    "records.milk_summary.read": {
        "phase": "reading",
        "started": "我先看看吸奶和喂养记录～",
        "completed": "我把吸奶和喂养记录整理好啦",
    },
    "records.milk_analysis.read": {
        "phase": "reading",
        "started": "我先看看之前的奶量分析～",
        "completed": "我把奶量分析看好啦",
    },
    "records.milk_analysis.intake": {
        "phase": "reading",
        "started": "我先把奶量分析需要的信息核对齐全～",
        "completed": "我把需要的信息核对好啦",
    },
    "records.milk_analysis.evaluate": {
        "phase": "evaluating",
        "started": "我来综合评估一下奶量问题～",
        "completed": "我完成奶量分析啦",
    },
    "records.growth.read": {
        "phase": "reading",
        "started": "我先看看宝宝的生长记录～",
        "completed": "我把宝宝生长记录整理好啦",
    },
    "plans.current.read": {
        "phase": "reading",
        "started": "我先看看计划和日程任务～",
        "completed": "我把计划和日程整理好啦",
    },
    "plans.calendar.read": {
        "phase": "reading",
        "started": "我先看看计划和日程任务～",
        "completed": "我把计划和日程整理好啦",
    },
    "pregnancy_diary.manage": {
        "phase": "saving",
        "started": "我先看看孕期日记～",
        "completed": "孕期日记这一步处理好了",
    },
    "pregnancy.plan_context.read": {
        "phase": "reading",
        "started": "我先看看孕期计划上下文～",
        "completed": "我把孕期计划上下文整理好啦",
    },
    "devices.pump_status.read": {
        "phase": "reading",
        "started": "我先看看设备状态～",
        "completed": "我把设备状态看好啦",
    },
    "devices.guidance.read": {
        "phase": "reading",
        "started": "我先看看设备说明～",
        "completed": "我把设备说明整理好啦",
    },
    "devices.unboxing.advance": {
        "phase": "planning",
        "started": "我继续带你完成这一步～",
        "completed": "这一步已经衔接好啦",
    },
    "images.inspect": {
        "phase": "reading",
        "started": "我先看看图片内容～",
        "completed": "我把图片内容看好啦",
    },
    "birth_plan_form_create": {
        "phase": "planning",
        "started": "我先帮你准备确认内容～",
        "completed": "我已经准备好确认内容啦",
    },
    "labor_communication_card_create": {
        "phase": "planning",
        "started": "我先帮你整理分娩沟通单～",
        "completed": "我整理好分娩沟通单啦",
    },
    "pregnancy.plan_intake.start": {
        "phase": "planning",
        "started": "我先帮你准备孕期计划信息表～",
        "completed": "孕期计划信息表已经准备好啦",
    },
    "pregnancy.plan_intake.analyze": {
        "phase": "planning",
        "started": "我先按你填写的信息做针对性分析～",
        "completed": "我已经把会影响计划的重点分析好啦",
    },
    "pregnancy.plan_intake.advance": {
        "phase": "planning",
        "started": "我先整理孕期计划信息～",
        "completed": "我整理好这一步信息啦",
    },
    "pregnancy.plan.propose": {
        "phase": "planning",
        "started": "我先帮你整理孕期计划～",
        "completed": "我已经准备好孕期计划预览，等你确认～",
    },
    "pregnancy.plan_todo.propose": {
        "phase": "planning",
        "started": "我先帮你更新孕期计划事项～",
        "completed": "孕期计划事项已经更新好啦",
    },
    "plans.milk_plan.propose": {
        "phase": "planning",
        "started": "我先帮你整理奶量计划～",
        "completed": "我已经准备好奶量计划预览，等你确认～",
    },
    "plans.milk_schedule.propose": {
        "phase": "planning",
        "started": "我先帮你调整一下日程～",
        "completed": "我整理好日程调整预览啦",
    },
    "plans.task_create.propose": {
        "phase": "planning",
        "started": "我先帮你准备一项任务～",
        "completed": "我已经准备好任务预览，等你确认～",
    },
    "plans.task_complete.propose": {
        "phase": "planning",
        "started": "我先帮你记录任务完成情况～",
        "completed": "我已经准备好任务状态修改，等你确认～",
    },
    "plans.task_update.propose": {
        "phase": "planning",
        "started": "我先帮你调整这项任务～",
        "completed": "我已经准备好任务修改，等你确认～",
    },
    "plans.task_delete.propose": {
        "phase": "planning",
        "started": "我先帮你确认要删除的任务～",
        "completed": "我已经准备好任务删除预览，等你确认～",
    },
    "plans.milk_task_update.propose": {
        "phase": "planning",
        "started": "我先帮你调整奶量任务～",
        "completed": "我已经准备好奶量任务修改，等你确认～",
    },
    "plans.milk_task_delete.propose": {
        "phase": "planning",
        "started": "我先帮你确认要删除的奶量任务～",
        "completed": "我已经准备好奶量任务删除预览，等你确认～",
    },
    "plans.plan_delete.propose": {
        "phase": "planning",
        "started": "我先帮你确认要删除的计划～",
        "completed": "我已经准备好计划删除预览，等你确认～",
    },
    "notifications.milk_reminder.propose": {
        "phase": "planning",
        "started": "我先帮你准备奶量提醒～",
        "completed": "我已经准备好提醒预览，等你确认～",
    },
    "records.feeding_record.propose": {
        "phase": "planning",
        "started": "我先帮你整理这条喂养记录～",
        "completed": "我已经准备好喂养记录预览，等你确认～",
    },
    "records.pumping_record.propose": {
        "phase": "planning",
        "started": "我先帮你整理这条吸奶记录～",
        "completed": "我已经准备好吸奶记录预览，等你确认～",
    },
    "records.feeding_record_delete.propose": {
        "phase": "planning",
        "started": "我先帮你确认要删除的喂养记录～",
        "completed": "我已经准备好删除预览，等你确认～",
    },
    "records.pumping_record_delete.propose": {
        "phase": "planning",
        "started": "我先帮你确认要删除的吸奶记录～",
        "completed": "我已经准备好删除预览，等你确认～",
    },
    "records.growth_record.propose": {
        "phase": "planning",
        "started": "我先帮你整理宝宝成长记录～",
        "completed": "我已经准备好成长记录预览，等你确认～",
    },
    "records.growth_record_update.propose": {
        "phase": "planning",
        "started": "我先帮你调整宝宝成长记录～",
        "completed": "我已经准备好成长记录修改，等你确认～",
    },
    "records.growth_record_delete.propose": {
        "phase": "planning",
        "started": "我先帮你确认要删除的成长记录～",
        "completed": "我已经准备好成长记录删除预览，等你确认～",
    },
    "hospital_bag_form_create": {
        "phase": "planning",
        "started": "我先帮你准备确认内容～",
        "completed": "我已经准备好确认内容啦",
    },
    "hospital_bag_card_create": {
        "phase": "planning",
        "started": "我先帮你整理待产包清单～",
        "completed": "我整理好待产包清单啦",
    },
    "hospital_bag_cart_update": {
        "phase": "saving",
        "started": "我先帮你调整待产包购物车～",
        "completed": "我已经调整好待产包购物车啦",
    },
    "hospital_bag_pump_recommend": {
        "phase": "planning",
        "started": "我先帮你看看吸奶器型号～",
        "completed": "我把吸奶器型号整理好啦",
    },
    "support.ticket.propose": {
        "phase": "planning",
        "started": "我先帮你准备售后信息表～",
        "completed": "请确认售后信息",
    },
    "ibclc_consult_card_create": {
        "phase": "planning",
        "started": "我先帮你准备 IBCLC 咨询入口～",
        "completed": "我已经准备好 IBCLC 咨询入口啦",
    },
}
