from __future__ import annotations

from typing import Any
from uuid import UUID


STATUS_BAR = "status_bar"
THINKING_NOTE = "thinking_note"
WORK_ITEM = "work_item"
ARTIFACT = "artifact"
ACTION = "action"


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
            "label": "我接着处理下一步",
            "surface": THINKING_NOTE,
            "priority": 60,
        },
        "response_finalizing": {
            "phase": "replying",
            "label": "我在组织回复～",
            "surface": STATUS_BAR,
            "priority": 80,
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
    label: str = "",
    safe_output: dict[str, Any] | None = None,
    read_or_write: str = "",
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    normalized_event_type = str(event_type or "").strip()
    normalized_tool_name = str(tool_name or "").strip()
    lifecycle = _tool_lifecycle(normalized_event_type)
    tool_copy = _TOOL_COPY.get(normalized_tool_name)
    surface = STATUS_BAR
    phase = "reading" if read_or_write == "read" else "planning"
    priority = 50

    if lifecycle == "failed":
        phase = "error"
        display_label = _failed_tool_label(tool_copy, label)
        priority = 90
    elif lifecycle == "completed":
        phase, display_label = _completed_tool_semantic(
            tool_copy=tool_copy,
            label=label,
            safe_output=safe_output or {},
            read_or_write=read_or_write,
            requires_confirmation=requires_confirmation,
        )
        priority = 70
    else:
        phase, display_label = _started_tool_semantic(
            tool_copy=tool_copy,
            label=label,
            read_or_write=read_or_write,
        )
        priority = 45

    return {
        "phase": phase,
        "label": display_label,
        "surface": surface,
        "visibility": _legacy_visibility(surface),
        "merge_key": f"tool:{normalized_tool_name or 'unknown'}:{lifecycle}",
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
        label=str(payload.get("label") or ""),
        safe_output=safe_output,
        read_or_write=read_or_write,
        requires_confirmation=requires_confirmation,
    )
    return enriched


def artifact_event_payload_semantic(*, artifact_type: str) -> dict[str, Any]:
    return {
        "phase": "planning",
        "label": f"已生成{_artifact_subject(artifact_type)}",
        "surface": ARTIFACT,
        "visibility": "hidden",
        "merge_key": f"artifact:{artifact_type or 'unknown'}",
        "priority": 70,
        "lifecycle": "completed",
    }


def action_event_payload_semantic(*, action_status: str) -> dict[str, Any]:
    normalized_status = str(action_status or "").strip()
    label = {
        "queued": "动作已提交",
        "applied": "动作已应用",
        "rejected": "动作已拒绝",
        "failed": "动作处理失败",
    }.get(normalized_status, "需要确认后继续")
    lifecycle = (
        "failed" if normalized_status == "failed" else "completed" if normalized_status in {"queued", "applied", "rejected"} else "running"
    )
    return {
        "phase": "saving" if normalized_status in {"queued", "applied"} else "planning",
        "label": label,
        "surface": ACTION,
        "visibility": "hidden",
        "merge_key": f"action:{normalized_status or 'pending'}",
        "priority": 75,
        "lifecycle": lifecycle,
    }


def _started_tool_semantic(*, tool_copy: dict[str, str] | None, label: str, read_or_write: str) -> tuple[str, str]:
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
    label: str,
    safe_output: dict[str, Any],
    read_or_write: str,
    requires_confirmation: bool,
) -> tuple[str, str]:
    output_status = str(safe_output.get("status") or "").strip()
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


def _failed_tool_label(tool_copy: dict[str, str] | None, label: str) -> str:
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
    return "status" if surface == STATUS_BAR else "hidden"


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
    "plans.current.read": {
        "phase": "reading",
        "started": "我先看看计划和日程任务～",
        "completed": "我把计划和日程整理好啦",
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
    "pregnancy.plan.propose": {
        "phase": "planning",
        "started": "我先帮你整理孕期计划～",
        "completed": "我已经准备好孕期计划预览，等你确认～",
    },
    "plans.milk_plan.propose": {
        "phase": "planning",
        "started": "我先帮你整理奶量计划～",
        "completed": "我已经准备好奶量计划预览，等你确认～",
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
}
