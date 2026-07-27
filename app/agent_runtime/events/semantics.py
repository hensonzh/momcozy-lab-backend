from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID


STATUS_BAR = "status_bar"
THINKING_NOTE = "thinking_note"
WORK_ITEM = "work_item"
ARTIFACT = "artifact"
ACTION = "action"
HIDDEN = "hidden"


class _RunProgressPreset(TypedDict):
    phase: str
    label: str
    surface: str
    priority: int


_RUN_PROGRESS_PRESETS: dict[str, _RunProgressPreset] = {
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


def run_progress_payload(*, phase: str, label: str) -> dict[str, Any]:
    normalized_phase = str(phase or "").strip()
    normalized_label = str(label or "").strip()
    return {
        "phase": normalized_phase,
        "label": normalized_label,
        "semantic": run_progress_semantic(phase=normalized_phase, label=normalized_label),
    }


def run_progress_semantic(*, phase: str, label: str) -> dict[str, Any]:
    preset = _RUN_PROGRESS_PRESETS.get(
        phase,
        {
            "phase": "thinking",
            "label": label or "我按当前场景继续处理～",
            "surface": STATUS_BAR,
            "priority": 50,
        },
    )
    return {
        "phase": str(preset["phase"]),
        "label": label or str(preset["label"]),
        "surface": str(preset["surface"]),
        "merge_key": f"progress:{phase or 'unknown'}",
        "priority": preset["priority"],
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
    output: dict[str, Any] | None = None,
    effect_scope: str = "none",
) -> dict[str, Any]:
    del safe_args
    normalized_name = str(tool_name or "").strip()
    lifecycle = {"tool.completed": "completed", "tool.failed": "failed"}.get(event_type, "running")
    subject = str(label or "").strip() or "相关信息"
    if lifecycle == "failed":
        phase, display_label, priority = "error", f"{subject}暂时没处理好", 90
    elif lifecycle == "completed" and bool((output or {}).get("requires_confirmation")):
        phase, display_label, priority = "planning", "我已经准备好预览，等你确认～", 70
    elif lifecycle == "completed" and effect_scope == "none":
        phase, display_label, priority = "reading", f"我把{subject}整理好啦", 70
    elif lifecycle == "completed" and effect_scope in {"user_resource", "external_resource"}:
        phase, display_label, priority = "saving", f"我已经保存好{subject}啦", 70
    elif lifecycle == "completed":
        phase, display_label, priority = "planning", f"我把{subject}准备好啦", 70
    elif effect_scope == "none":
        phase, display_label, priority = "reading", f"我先看看{subject}～", 45
    elif effect_scope in {"user_resource", "external_resource"}:
        phase, display_label, priority = "saving", f"我先准备{subject}～", 45
    else:
        phase, display_label, priority = "planning", f"我先准备{subject}～", 45
    return {
        "phase": phase,
        "label": display_label,
        "surface": WORK_ITEM,
        "merge_key": f"tool:{str(tool_call_id or '').strip() or normalized_name or 'unknown'}",
        "priority": priority,
        "lifecycle": lifecycle,
    }


def with_tool_event_semantic(
    payload: dict[str, Any],
    *,
    event_type: str,
    tool_name: str,
    output: dict[str, Any] | None = None,
    effect_scope: str = "none",
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["semantic"] = tool_event_semantic(
        event_type=event_type,
        tool_name=tool_name,
        tool_call_id=str(payload.get("tool_call_id") or payload.get("call_id") or ""),
        label=str(payload.get("label") or ""),
        safe_args=payload.get("safe_args") if isinstance(payload.get("safe_args"), dict) else None,
        output=output,
        effect_scope=effect_scope,
    )
    return enriched


def artifact_event_payload_semantic(*, artifact_type: str, artifact_id: str = "") -> dict[str, Any]:
    normalized_type = str(artifact_type or "").strip()
    return {
        "phase": "done",
        "label": "我已经整理好结果啦",
        "surface": ARTIFACT,
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
    lifecycle = "failed" if normalized_status == "failed" else "completed" if normalized_status in {"queued", "applied", "rejected"} else "running"
    return {
        "phase": "saving" if normalized_status in {"queued", "applied"} else "planning",
        "label": label,
        "surface": ACTION,
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
    phase, label, surface, lifecycle, priority = presets.get(normalized_type, ("working", "", HIDDEN, "running", 0))
    return {
        "phase": phase,
        "label": label,
        "surface": surface,
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
        "merge_key": "web_search:current",
        "priority": 55,
        "lifecycle": lifecycle,
    }
