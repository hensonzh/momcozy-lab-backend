from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from .contexts import (
    ContextState,
    active_service_domain,
    birth_journey_intake_state,
    build_request_context,
    hospital_bag_slots,
    merge_birth_journey_intake_state,
    merge_hospital_bag_slots,
    record_loaded_tool,
    record_milk_management_tool_state,
    set_active_service_domain,
)
from .health_guidance import health_guidance_request_context_lines, health_guidance_required_web_search_tool_choice
from .static_context import STATIC_AGENT_INSTRUCTIONS
from .tool_handlers.cards import birth_journey_intake_quick_reply_guidance
from .tool_schemas import FUNCTION_TOOLS
from .tool_registry import DEFERRED_TOOL_NAMESPACES, execute_tool, select_runtime_tools
from .types import AgUiEvent, AgUiEventHandler, AgentEvent, AgentEventHandler, AgentEventPhase, BuildAgentRequestOptions, ResponsesClientLike, ResponsesRequest, RuntimeInputs, TextDeltaHandler

AG_UI_STATUS_ACTIVITY_TYPE = "MOMCOZY_AGENT_STATUS"
AG_UI_STATUS_CUSTOM_NAME = "momcozy.agent.status"
AG_UI_THINKING_CUSTOM_NAME = "momcozy.agent.thinking"
AG_UI_WEB_SEARCH_CUSTOM_NAME = "momcozy.agent.web_search"
AG_UI_WEB_SEARCH_CITATIONS_CUSTOM_NAME = "momcozy.web_search.citations"
PRIVATE_USE_CITATION_START = "\ue200"
PRIVATE_USE_CITATION_END = "\ue201"
MAX_INLINE_CITATION_MARKER_CHARS = 240
QUICK_REPLIES_TOOL_NAME = "ui_quick_replies_create"
MILK_WRITE_TOOL_NAMES = {
    "milk_record_mutate",
    "milk_plan_mutate",
    "milk_calendar_mutate",
    "milk_task_complete",
    "infant_growth_mutate",
}
_BIRTH_PREP_TOOL_NAMES = {
    "birth_plan_form_create",
    "labor_communication_card_create",
    "birth_journey_intake_manage",
    "birth_journey_plan_card_create",
    "birth_journey_plan_delete",
    "birth_journey_plan_todo_update",
    "pregnancy_diary_manage",
    "hospital_bag_form_create",
    "hospital_bag_card_create",
    "hospital_bag_cart_update",
    "hospital_bag_pump_recommend",
}
_SERVICE_DOMAIN_BY_SKILL_ID = {
    "birth-prep": "birth_prep",
    "milk-management": "milk_management",
    "device-guidance": "device_guidance",
    "emotion-support": "emotion_support",
}
_DEFERRED_TOOL_NAMES = {
    str(tool_name)
    for namespace in DEFERRED_TOOL_NAMESPACES.values()
    for tool_name in namespace.get("tool_names", [])
}
_PROFILE_LOADED_FROM_DB_FLAG = "_user_profile_loaded_from_db"

AgUiSemantic = dict[str, Any]

MAX_TOOL_ROUNDS = 6
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "skills"
MAX_TOOL_IMAGE_INPUTS = 2
MAX_TOOL_IMAGE_METADATA = 8
MAX_TOOL_IMAGE_BYTES = 2 * 1024 * 1024
MAX_TOOL_IMAGE_TOTAL_BYTES = 2 * 1024 * 1024
TOOL_IMAGE_CONTENT_TYPES = {
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def new_ag_ui_run_id() -> str:
    return f"run_{uuid4().hex}"


def default_ag_ui_thread_id(inputs: RuntimeInputs) -> str:
    user_id = inputs.get("user_profile", {}).get("user_id")
    if isinstance(user_id, str) and user_id:
        return f"thread_{user_id}"
    return "thread_anonymous"


def run_started_event(thread_id: str, run_id: str, parent_run_id: str | None = None, input_payload: Any | None = None) -> AgUiEvent:
    event: AgUiEvent = {
        "type": "RUN_STARTED",
        "timestamp": _timestamp_ms(),
        "thread_id": thread_id,
        "run_id": run_id,
        "semantic": _semantic_payload(
            "thinking",
            "我已经收到你的消息啦～",
            "status",
            f"run:{run_id}",
            priority=10,
        ),
    }
    if parent_run_id:
        event["parent_run_id"] = parent_run_id
    if input_payload is not None:
        event["input"] = input_payload
    return event


def run_finished_event(thread_id: str, run_id: str, result: Any | None = None) -> AgUiEvent:
    event: AgUiEvent = {
        "type": "RUN_FINISHED",
        "timestamp": _timestamp_ms(),
        "thread_id": thread_id,
        "run_id": run_id,
        "semantic": _semantic_payload("done", "我处理好啦", "hidden", f"run:{run_id}", priority=100),
    }
    if result is not None:
        event["result"] = result
    return event


def run_error_event(message: str, code: str | None = None, *, thread_id: str | None = None, run_id: str | None = None) -> AgUiEvent:
    event: AgUiEvent = {
        "type": "RUN_ERROR",
        "timestamp": _timestamp_ms(),
        "message": message,
        "semantic": _semantic_payload("error", "这轮暂时没处理好", "status", f"run_error:{code or 'unknown'}", priority=100),
    }
    if thread_id:
        event["thread_id"] = thread_id
    if run_id:
        event["run_id"] = run_id
    if code:
        event["code"] = code
    return event


def quick_replies_event(message_id: str, replies: list[dict[str, str]]) -> AgUiEvent:
    return {
        "type": "QUICK_REPLIES",
        "timestamp": _timestamp_ms(),
        "message_id": message_id,
        "replies": replies,
        "semantic": _semantic_payload("done", "我帮你准备好下一轮的快捷输入啦", "hidden", f"quick_replies:{message_id}", priority=80),
    }


def web_search_citations_event(message_id: str, citations: list[dict[str, Any]]) -> AgUiEvent:
    return {
        "type": "CUSTOM",
        "timestamp": _timestamp_ms(),
        "name": AG_UI_WEB_SEARCH_CITATIONS_CUSTOM_NAME,
        "message_id": message_id,
        "value": {"citations": _compact_web_search_citations_for_display(citations)},
        "semantic": _semantic_payload("done", "我整理好参考来源啦", "hidden", f"citations:{message_id}", priority=75),
    }


def web_search_status_event(status: str, metadata: dict[str, Any] | None = None) -> AgUiEvent:
    label = _web_search_status_label(status)
    return {
        "type": "CUSTOM",
        "timestamp": _timestamp_ms(),
        "name": AG_UI_WEB_SEARCH_CUSTOM_NAME,
        "value": {
            "type": "agent.web_search",
            "status": _normalize_web_search_status(status),
            "label": label,
            "metadata": metadata or {},
        },
        "semantic": _web_search_semantic(status, metadata or {}),
    }


def step_started_event(step_name: str) -> AgUiEvent:
    return {
        "type": "STEP_STARTED",
        "timestamp": _timestamp_ms(),
        "step_name": step_name,
        "semantic": _step_semantic(step_name, "started"),
    }


def step_finished_event(step_name: str) -> AgUiEvent:
    return {
        "type": "STEP_FINISHED",
        "timestamp": _timestamp_ms(),
        "step_name": step_name,
        "semantic": _step_semantic(step_name, "finished"),
    }


def tool_call_start_event(
    tool_call_id: str,
    tool_call_name: str,
    parent_message_id: str | None = None,
    *,
    response_id: str | None = None,
    output_index: int | None = None,
    item_id: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> AgUiEvent:
    event: AgUiEvent = {
        "type": "TOOL_CALL_START",
        "timestamp": _timestamp_ms(),
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_call_name,
        "semantic": _tool_semantic(tool_call_name, "start", tool_call_id=tool_call_id, arguments=arguments),
    }
    if parent_message_id:
        event["parent_message_id"] = parent_message_id
    if response_id:
        event["response_id"] = response_id
    if output_index is not None:
        event["output_index"] = output_index
    if item_id:
        event["item_id"] = item_id
    return event


def tool_call_args_event(
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    response_id: str | None = None,
    output_index: int | None = None,
    item_id: str | None = None,
) -> AgUiEvent:
    event: AgUiEvent = {
        "type": "TOOL_CALL_ARGS",
        "timestamp": _timestamp_ms(),
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_name,
        "delta": json.dumps(safe_tool_arguments(tool_name, arguments), ensure_ascii=False),
        "semantic": _tool_semantic(tool_name, "args", tool_call_id=tool_call_id, arguments=arguments),
    }
    if response_id:
        event["response_id"] = response_id
    if output_index is not None:
        event["output_index"] = output_index
    if item_id:
        event["item_id"] = item_id
    return event


def tool_call_end_event(
    tool_call_id: str,
    tool_name: str,
    *,
    response_id: str | None = None,
    output_index: int | None = None,
    item_id: str | None = None,
) -> AgUiEvent:
    event: AgUiEvent = {
        "type": "TOOL_CALL_END",
        "timestamp": _timestamp_ms(),
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_name,
        "semantic": _tool_semantic(tool_name, "end", tool_call_id=tool_call_id),
    }
    if response_id:
        event["response_id"] = response_id
    if output_index is not None:
        event["output_index"] = output_index
    if item_id:
        event["item_id"] = item_id
    return event


def tool_call_result_event(
    message_id: str,
    tool_call_id: str,
    tool_call_name: str,
    result: dict[str, Any],
    *,
    response_id: str | None = None,
    output_index: int | None = None,
    item_id: str | None = None,
) -> AgUiEvent:
    event: AgUiEvent = {
        "type": "TOOL_CALL_RESULT",
        "timestamp": _timestamp_ms(),
        "message_id": message_id,
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_call_name,
        "content": json.dumps(safe_tool_result(result), ensure_ascii=False),
        "role": "tool",
        "semantic": _tool_semantic(
            tool_call_name,
            "result",
            tool_call_id=tool_call_id,
            result=safe_tool_result(result),
        ),
    }
    if response_id:
        event["response_id"] = response_id
    if output_index is not None:
        event["output_index"] = output_index
    if item_id:
        event["item_id"] = item_id
    return event


def artifact_created_event(
    *,
    artifact_id: str,
    artifact_type: str,
    tool_call_id: str,
    tool_call_name: str,
    artifact: dict[str, Any],
    status: str = "ready",
) -> AgUiEvent:
    return {
        "type": "ARTIFACT_CREATED",
        "timestamp": _timestamp_ms(),
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_call_name,
        "status": status,
        "artifact": artifact,
        "semantic": _artifact_semantic(artifact_type, artifact_id, tool_call_name),
    }


def confirmation_required_event(
    *,
    confirmation_id: str,
    tool_call_id: str,
    tool_call_name: str,
    title: str,
    message: str = "",
    artifact_id: str | None = None,
) -> AgUiEvent:
    event: AgUiEvent = {
        "type": "CONFIRMATION_REQUIRED",
        "timestamp": _timestamp_ms(),
        "confirmation_id": confirmation_id,
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_call_name,
        "title": title,
        "message": message,
        "semantic": _semantic_payload(
            "confirming",
            title or "我需要你确认一下，再继续处理",
            "action",
            f"confirmation:{confirmation_id}",
            priority=90,
        ),
    }
    if artifact_id:
        event["artifact_id"] = artifact_id
    return event


def status_activity_snapshot_event(message_id: str, event: AgentEvent) -> AgUiEvent:
    return {
        "type": "ACTIVITY_SNAPSHOT",
        "timestamp": _timestamp_ms(),
        "message_id": message_id,
        "activity_type": AG_UI_STATUS_ACTIVITY_TYPE,
        "content": event,
        "replace": True,
        "semantic": _status_event_semantic(event),
    }


def status_custom_event(event: AgentEvent) -> AgUiEvent:
    return {
        "type": "CUSTOM",
        "timestamp": _timestamp_ms(),
        "name": AG_UI_STATUS_CUSTOM_NAME,
        "value": event,
        "semantic": _status_event_semantic(event),
    }


def thinking_custom_event(status: str, metadata: dict[str, Any] | None = None) -> AgUiEvent:
    metadata = metadata or {}
    return {
        "type": "CUSTOM",
        "timestamp": _timestamp_ms(),
        "name": AG_UI_THINKING_CUSTOM_NAME,
        "value": {
            "type": "agent.thinking",
            "status": status,
            "metadata": metadata,
        },
        "semantic": _thinking_semantic(status, metadata),
    }


def text_message_semantic(stage: str, message_id: str) -> AgUiSemantic:
    if stage == "start":
        return _semantic_payload("replying", "我在组织回复～", "hidden", f"text:{message_id}", priority=40)
    if stage == "content":
        return _semantic_payload("replying", "我在回复你～", "hidden", f"text:{message_id}", priority=40)
    if stage == "end":
        return _semantic_payload("done", "我整理好回复啦", "hidden", f"text:{message_id}", priority=80)
    return _semantic_payload("replying", "我在处理回复～", "hidden", f"text:{message_id}", priority=40)


def _semantic_payload(phase: str, label: str, visibility: str, merge_key: str, *, priority: int = 0) -> AgUiSemantic:
    return {
        "phase": phase,
        "label": label,
        "visibility": visibility,
        "merge_key": merge_key,
        "priority": priority,
    }


def _step_semantic(step_name: str, state: str) -> AgUiSemantic:
    if step_name == "routing":
        label = "我先理解一下你的需求～" if state == "started" else "我理解你的需求啦"
        phase = "thinking" if state == "started" else "done"
    else:
        label = "我继续处理当前步骤～" if state == "started" else "这一步处理好啦"
        phase = "working" if state == "started" else "done"
    return _semantic_payload(phase, label, "status", f"step:{step_name}", priority=30)


def _status_event_semantic(event: AgentEvent) -> AgUiSemantic:
    message = str(event.get("message") or "").strip()
    phase = str(event.get("phase") or "").strip()
    if phase == "failed" or message == "Step failed.":
        return _semantic_payload("error", "这一步暂时没处理好", "status", f"status:{phase or 'failed'}", priority=95)
    if phase == "requesting_model" and message == "Requesting model response with tool outputs.":
        return _semantic_payload("thinking", _status_label(message), "status", f"status:{phase or 'loop'}", priority=20)
    if phase in {"requesting_model", "started", "model_tool_call", "tool_completed"}:
        return _semantic_payload("thinking", _status_label(message), "hidden", f"status:{phase or 'loop'}", priority=20)
    return _semantic_payload("working", _status_label(message), "status", f"status:{phase or 'loop'}", priority=20)


def _thinking_semantic(status: str, metadata: dict[str, Any]) -> AgUiSemantic:
    normalized_status = status.strip().lower()
    if normalized_status in {"started", "running"}:
        label = "我接着处理下一步" if metadata.get("after_output_text") is True else "我想一下"
        return _semantic_payload("thinking", label, "hidden", "thinking:current", priority=40)
    if normalized_status == "failed":
        return _semantic_payload("error", "这一步我还没想清楚", "hidden", "thinking:current", priority=40)
    return _semantic_payload("done", "我想好啦", "hidden", "thinking:current", priority=40)


def _web_search_semantic(status: str, metadata: dict[str, Any]) -> AgUiSemantic:
    normalized_status = _normalize_web_search_status(status)
    if normalized_status == "failed":
        return _semantic_payload("error", _web_search_status_label(status), "work_item", "web_search:current", priority=55)
    if normalized_status == "completed":
        return _semantic_payload("done", _web_search_status_label(status), "work_item", "web_search:current", priority=55)
    return _semantic_payload("reading", _web_search_status_label(status), "work_item", "web_search:current", priority=55)


def _normalize_web_search_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "done", "finished", "succeeded", "success"}:
        return "completed"
    if normalized in {"failed", "error"}:
        return "failed"
    return "searching"


def _web_search_status_label(status: str) -> str:
    normalized_status = _normalize_web_search_status(status)
    if normalized_status == "completed":
        return "我查好专业资料啦"
    if normalized_status == "failed":
        return "我查好专业资料啦"
    return "我在查专业资料～"


def _artifact_semantic(artifact_type: str, artifact_id: str, tool_name: str) -> AgUiSemantic:
    normalized_artifact_type = str(artifact_type or "").strip()
    if normalized_artifact_type == "form":
        label = "我已经准备好确认内容啦"
    elif normalized_artifact_type in {"support_ticket", "support_ticket_draft"}:
        label = "请确认售后信息"
    elif normalized_artifact_type == "mom_baby_status_card":
        label = "我已经整理好宝宝和我页面啦"
    elif normalized_artifact_type == "milk_analysis_card":
        label = "我已经整理好奶量分析结果啦"
    elif normalized_artifact_type == "milk_plan_card":
        label = "我已经整理好奶量计划啦"
    else:
        label = "我已经整理好结果啦"
    merge_key = f"artifact:{artifact_id or tool_name or normalized_artifact_type or 'current'}"
    return _semantic_payload("done", label, "artifact", merge_key, priority=70)


def _tool_semantic(
    tool_name: str,
    stage: str,
    *,
    tool_call_id: str,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> AgUiSemantic:
    normalized = _normalize_tool_name(tool_name)
    if normalized == QUICK_REPLIES_TOOL_NAME:
        return _quick_replies_tool_semantic(stage, tool_call_id)
    phase = _tool_semantic_phase(normalized)
    label = _tool_stage_label(normalized, stage, arguments or {}, result or {})
    visibility = "work_item"
    return _semantic_payload(phase, label, visibility, f"tool:{tool_call_id or normalized or 'current'}", priority=50)


def _quick_replies_tool_semantic(stage: str, tool_call_id: str) -> AgUiSemantic:
    if stage == "result":
        return _semantic_payload(
            "done",
            "我帮你准备好下一轮的快捷输入啦",
            "status",
            f"quick_replies:{tool_call_id or 'current'}",
            priority=60,
        )
    if stage == "end":
        label = "我在帮你准备下一轮的快捷输入～"
    else:
        label = "我在帮你准备下一轮的快捷输入～"
    return _semantic_payload(
        "planning",
        label,
        "status",
        f"quick_replies:{tool_call_id or 'current'}",
        priority=60,
    )


def _normalize_tool_name(tool_name: str) -> str:
    token = str(tool_name or "").strip()
    if not token:
        return ""
    return token.split(".")[-1].removeprefix("milk_management__")


def _tool_semantic_phase(tool_name: str) -> str:
    if tool_name in {"tool_search", "tool_search_call"}:
        return "thinking"
    if tool_name in {"milk_analysis_evaluate", "infant_growth_evaluate", "risk_evaluate"}:
        return "evaluating"
    if tool_name in {"milk_plan_preview_create", "milk_calendar_change_preview", "milk_calendar_reschedule_preview"}:
        return "planning"
    if tool_name in {
        "milk_record_mutate",
        "milk_plan_mutate",
        "milk_calendar_mutate",
        "milk_task_complete",
        "infant_growth_mutate",
        "hospital_bag_cart_update",
        "reminder_create",
        "reminder_update",
        "reminder_delete",
        "birth_journey_plan_delete",
        "birth_journey_plan_todo_update",
        "pregnancy_diary_manage",
        "profile_update",
    }:
        return "saving"
    if tool_name in {
        "ui_form_create",
        "birth_plan_form_create",
        "hospital_bag_form_create",
        "labor_communication_card_create",
        "birth_journey_intake_manage",
        "birth_journey_plan_card_create",
        "hospital_bag_card_create",
        "ibclc_consult_card_create",
        "support_ticket_draft_create",
        "handoff_summary_generate",
    }:
        return "planning"
    if tool_name == "run_approved_skill_script":
        return "working"
    return "reading"


def _tool_stage_label(
    tool_name: str,
    stage: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> str:
    if stage == "result":
        return _tool_result_label(tool_name, result)
    if stage == "end":
        return _tool_end_label(tool_name)
    return _tool_start_label(tool_name, arguments)


def _tool_start_label(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name in {"tool_search", "tool_search_call"}:
        return "让我看看怎么处理～"
    if tool_name == "load_skill":
        return _load_skill_label(arguments)
    if tool_name == "list_skills":
        return "我看看可以怎么帮你～"
    if tool_name == "search_skill_assets":
        return "我先找一下相关资料～"
    if tool_name == "read_skill_file":
        return "我先看一下相关说明～"
    if tool_name == "profile_get":
        return "我先看一下你的基础信息～"
    if tool_name == "profile_update":
        return "我先帮你记一下基础信息～"
    if tool_name == "milk_snapshot_get":
        return "我先看看你的奶量情况～"
    if tool_name == "milk_status_query":
        return "我先看看今天的奶量状态～"
    if tool_name == "milk_analysis_intake_manage":
        return "我先把关键信息核对齐全～"
    if tool_name == "milk_records_query":
        return "我先看看吸奶和喂养记录～"
    if tool_name == "milk_plan_query":
        return "我先看看之前保存的奶量计划～"
    if tool_name == "milk_calendar_query":
        return "我先看看计划和日程任务～"
    if tool_name == "milk_analysis_evaluate":
        return "我来综合评估一下奶量问题～"
    if tool_name == "infant_growth_evaluate":
        return "我先看看宝宝的生长信号～"
    if tool_name == "risk_evaluate":
        return "我先确认一下有没有相关风险～"
    if tool_name == "milk_plan_preview_create":
        return "我先帮你拟一版奶量计划～"
    if tool_name in {"milk_calendar_change_preview", "milk_calendar_reschedule_preview"}:
        return "我先帮你调整一下日程～"
    if tool_name == "milk_record_mutate":
        return "我先帮你处理这条记录～"
    if tool_name == "milk_task_complete":
        return "我先帮你记录任务完成情况～"
    if tool_name == "milk_plan_mutate":
        return "我先帮你保存奶量计划～"
    if tool_name == "milk_calendar_mutate":
        return "我先帮你保存日程调整～"
    if tool_name == "infant_growth_mutate":
        return "我先帮你保存宝宝成长记录～"
    if tool_name in {"ui_form_create", "birth_plan_form_create", "hospital_bag_form_create"}:
        return "我先帮你准备确认内容～"
    if tool_name == "labor_communication_card_create":
        return "我先帮你整理分娩沟通单～"
    if tool_name == "birth_journey_plan_card_create":
        return "我先帮你整理孕期计划～"
    if tool_name == "birth_journey_plan_delete":
        return "我先帮你删除孕期计划～"
    if tool_name == "birth_journey_plan_todo_update":
        return "我先帮你同步计划完成状态～"
    if tool_name == "pregnancy_diary_manage":
        action = str(arguments.get("action") or "").strip()
        if action in {"write", "update", "create"}:
            return "我先帮你保存孕期日记～"
        if action == "delete":
            return "我先帮你删除孕期日记～"
        return "我先看看孕期日记～"
    if tool_name == "hospital_bag_card_create":
        return "我先帮你整理待产包清单～"
    if tool_name == "ibclc_consult_card_create":
        return "我先帮你准备 IBCLC 咨询入口～"
    if tool_name == "support_ticket_draft_create":
        return "我先帮你准备售后信息表～"
    if tool_name == "hospital_bag_pump_recommend":
        return "我先看看适合你的吸奶器型号～"
    if tool_name == "hospital_bag_cart_update":
        return "我先帮你调整待产包购物车～"
    if tool_name == "device_manual_search":
        return "我先确认设备这一步～"
    if tool_name == "knowledge_search":
        return "我去找找相关资料～"
    if tool_name == "memory_search":
        return "我去找一下之前的信息～"
    if tool_name == "reminder_list":
        return "我先看看你的提醒～"
    if tool_name == "birth_journey_intake_manage":
        return "我先整理孕期计划信息～"
    if tool_name == "handoff_summary_generate":
        return "我先整理转接摘要～"
    if tool_name == "ui_quick_replies_create":
        return "我在帮你准备下一轮的快捷输入～"
    if tool_name == "run_approved_skill_script":
        return "我按场景说明处理这一步～"
    return "我按当前场景继续处理～"


def _tool_end_label(tool_name: str) -> str:
    if tool_name in {"tool_search", "tool_search_call"}:
        return "我找到合适的方案啦"
    if tool_name in {"milk_records_query", "milk_status_query", "milk_snapshot_get", "milk_plan_query", "milk_calendar_query"}:
        return "我把奶量和日程信息整理一下～"
    if tool_name in {"milk_analysis_evaluate", "infant_growth_evaluate", "risk_evaluate"}:
        return "我把评估结果整理一下～"
    if tool_name == "milk_plan_preview_create":
        return "我再完善一下计划草稿～"
    if tool_name in {"milk_calendar_change_preview", "milk_calendar_reschedule_preview"}:
        return "我把调整后的日程整理一下～"
    if tool_name in {"milk_record_mutate", "milk_task_complete", "milk_plan_mutate", "milk_calendar_mutate", "infant_growth_mutate"}:
        return "我在保存这次修改～"
    if tool_name == "hospital_bag_pump_recommend":
        return "我把推荐结果整理一下～"
    if tool_name == "hospital_bag_cart_update":
        return "我在保存购物车修改～"
    if tool_name == "device_manual_search":
        return "我把这一步整理好了～"
    if tool_name == "support_ticket_draft_create":
        return "我在准备售后信息表～"
    if tool_name == "birth_journey_plan_delete":
        return "我在处理删除结果～"
    if tool_name == "birth_journey_plan_todo_update":
        return "我在同步这项计划进度～"
    if tool_name == "birth_journey_intake_manage":
        return "我在整理下一步需要确认的信息～"
    if tool_name == "pregnancy_diary_manage":
        return "我在整理孕期日记结果～"
    if tool_name in {"ui_form_create", "birth_plan_form_create", "hospital_bag_form_create", "labor_communication_card_create", "birth_journey_plan_card_create", "hospital_bag_card_create", "ibclc_consult_card_create"}:
        return "我在把结果整理出来～"
    return "我继续处理一下～"


def _tool_result_label(tool_name: str, result: dict[str, Any]) -> str:
    status = str(result.get("status") or "").strip()
    if result.get("result_ok") is False:
        if status == "needs_write_confirmation":
            return "接下来需要你确认一下～"
        if status == "calendar_write_strategy_required":
            return "这版计划还需要确认写入方式"
        if tool_name == "milk_plan_mutate":
            return "这次还没保存成功"
        return "这一步暂时没处理好"
    if result.get("ok") is False:
        return "这一步暂时没处理好"
    if status.startswith("needs_"):
        return "我还需要先确认几件事～"
    if status == "plan_preview_needs_revision":
        return "这版结果还需要再调一下"
    if status == "plan_preview_not_recommended":
        return "这版方案我不建议继续用"
    if status == "plan_preview_needs_medical_confirmation":
        return "我需要先确认一下医疗边界～"
    if result.get("requires_confirmation") is True:
        return "我已经准备好预览～"
    if tool_name in {"tool_search", "tool_search_call"}:
        return "我在执行这个方案啦～"
    if tool_name == "load_skill":
        return "我准备好继续处理啦"
    if tool_name == "profile_get":
        return "我看过你的基础信息啦"
    if tool_name == "profile_update":
        return "我已经记好了"
    if tool_name == "milk_records_query":
        return "我把吸奶和喂养记录整理好啦"
    if tool_name == "milk_status_query":
        return "我看好今天的奶量状态啦"
    if tool_name == "milk_snapshot_get":
        return "我把奶量情况整理好啦"
    if tool_name == "milk_calendar_query":
        return "我把计划和日程整理好啦"
    if tool_name == "milk_plan_query":
        return "我看好之前的奶量计划啦"
    if tool_name == "milk_analysis_intake_manage":
        return "我把需要的信息核对好啦"
    if tool_name == "milk_analysis_evaluate":
        return "我完成奶量分析啦"
    if tool_name == "infant_growth_evaluate":
        return "我完成宝宝生长评估啦"
    if tool_name == "risk_evaluate":
        return "我确认好风险边界啦"
    if tool_name == "milk_record_mutate":
        return _mutation_result_label(status, "我已经保存好这条记录啦")
    if tool_name == "milk_plan_mutate":
        return _mutation_result_label(status, "我已经保存好奶量计划啦")
    if tool_name == "milk_calendar_mutate":
        return _mutation_result_label(status, "我已经保存好日程调整啦")
    if tool_name == "milk_task_complete":
        return _task_result_label(status)
    if tool_name == "infant_growth_mutate":
        return _mutation_result_label(status, "我已经保存好宝宝成长记录啦")
    if tool_name == "milk_plan_preview_create":
        return "我拟好奶量计划草稿啦"
    if tool_name in {"milk_calendar_change_preview", "milk_calendar_reschedule_preview"}:
        return "我整理好日程调整预览啦"
    if tool_name in {"ui_form_create", "birth_plan_form_create", "hospital_bag_form_create"}:
        return "我已经准备好确认内容啦"
    if tool_name == "labor_communication_card_create":
        return "我已经帮你整理好分娩沟通单啦"
    if tool_name == "birth_journey_intake_manage":
        if status == "ready_to_generate":
            return "孕期计划信息已经确认好啦"
        if status == "blocked_by_symptoms":
            return "我先帮你确认当前情况"
        return "我整理好这一步信息啦"
    if tool_name == "birth_journey_plan_card_create":
        return "我已经帮你整理好孕期计划啦"
    if tool_name == "birth_journey_plan_delete":
        status = str(result.get("status") or "").strip()
        if status == "needs_delete_confirmation":
            return "删除前还需要你确认一下"
        if status == "plan_not_found":
            return "当前没有孕期计划可删除"
        return "我已经删除孕期计划啦" if status == "plan_deleted" else "删除孕期计划暂时没成功"
    if tool_name == "birth_journey_plan_todo_update":
        status = str(result.get("status") or "").strip()
        if status == "todo_completion_updated":
            return "我已经同步计划完成状态啦"
        if status in {"needs_todo_reference", "todo_not_found"}:
            return "我还需要确认是哪一项"
        if status == "plan_not_found":
            return "当前没有孕期计划可更新"
        return "计划完成状态暂时没同步成功"
    if tool_name == "pregnancy_diary_manage":
        status = str(result.get("status") or "").strip()
        if status == "needs_delete_confirmation":
            return "删除前还需要你确认一下"
        if status == "entry_not_found":
            return "没有找到这条孕期日记"
        if status == "diary_entry_deleted":
            return "我已经删除这条孕期日记啦"
        if status in {"diary_entry_written", "diary_entry_created", "diary_entry_updated"}:
            return "我已经保存好孕期日记啦"
        if status in {"diary_list_read", "diary_entry_read"}:
            return "我看好孕期日记啦"
        return "孕期日记这一步处理好了"
    if tool_name == "hospital_bag_card_create":
        return "我已经帮你生成好待产包清单啦"
    if tool_name == "ibclc_consult_card_create":
        return "我已经准备好 IBCLC 咨询入口啦"
    if tool_name == "support_ticket_draft_create":
        return "请确认售后信息"
    if tool_name == "hospital_bag_pump_recommend":
        return "我已经帮你整理好吸奶器推荐啦"
    if tool_name == "hospital_bag_cart_update":
        return "这次购物车先不改" if status in {"needs_clarification", "cart_unchanged"} else "我已经帮你更新好待产包购物车啦"
    if tool_name == "device_manual_search":
        return "我把设备资料整理好啦"
    if tool_name == "run_approved_skill_script":
        return "这一步处理好啦"
    if tool_name in {"read_skill_file", "search_skill_assets", "knowledge_search", "memory_search"}:
        return "我找到相关资料啦"
    return "这一步处理好啦"


def _mutation_result_label(status: str, fallback: str) -> str:
    if "deleted" in status:
        return "我已经删除相关修改啦"
    if any(token in status for token in ("updated", "patched", "shifted")):
        return "我已经保存好修改啦"
    if "idempotent_replay" in status:
        return "我已经用上之前保存的修改啦"
    return fallback


def _task_result_label(status: str) -> str:
    if status == "milk_task_completed":
        return "我已经记录好任务完成啦"
    if status == "milk_task_completion_cancelled":
        return "我已经取消这次修改啦"
    if status == "milk_task_skipped":
        return "我已经记录为跳过啦"
    return "我已经记录好任务状态啦"


def _load_skill_label(arguments: dict[str, Any]) -> str:
    skill_id = str(arguments.get("skill_id") or arguments.get("id") or "").strip()
    labels = {
        "milk-management": "我先切到奶量管理这件事上～",
        "birth-prep": "我先切到待产准备这件事上～",
        "health-consultation": "我先帮你看健康咨询这件事～",
        "device-guidance": "我先切到设备指导这件事上～",
        "emotion-support": "我先切到情绪支持这件事上～",
    }
    return labels.get(skill_id, "我先准备一下这个场景～")


def _status_label(message: str) -> str:
    labels = {
        "Agent loop started.": "我在接收你的消息～",
        "Requesting model response.": "我想一下",
        "Requesting model response with tool outputs.": "我接着处理下一步",
        "Selecting the next step.": "我来判断下一步怎么做～",
        "Loading relevant context.": "我去看一下相关信息～",
        "Reading relevant information.": "我去看一下相关信息～",
        "Processing relevant information.": "我把刚看到的信息整理一下～",
        "Step completed.": "这一步处理好啦",
        "Step failed.": "这一步暂时没处理好",
    }
    return labels.get(message, message or "我继续处理当前步骤～")


def safe_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name in {"load_skill", "read_skill_file", "search_skill_assets", "run_approved_skill_script"}:
        return {key: arguments[key] for key in ("skill_id", "kind", "path", "script_name", "query") if key in arguments}

    redacted: dict[str, Any] = {"argument_keys": sorted(arguments.keys())}
    if "idempotency_key" in arguments:
        redacted["has_idempotency_key"] = True
    return redacted


def _plan_feedback_from_safe_tool_result(tool_name: Any, tool_result: dict[str, Any]) -> dict[str, Any] | None:
    name = str(tool_name or "").strip()
    if not tool_result.get("ok"):
        return None

    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    status = str(tool_result.get("status") or "").strip()
    if name == "milk_plan_mutate":
        reason_by_status = {
            "plan_applied": "synced",
            "milk_plan_updated": "updated",
            "milk_plan_deleted": "deleted",
            "milk_plan_already_absent": "deleted",
        }
        reason = reason_by_status.get(status)
        if not reason:
            return None
        dates = _plan_feedback_dates(data)
        label_by_reason = {
            "synced": "奶量计划已同步",
            "updated": "奶量计划已更新",
            "deleted": "奶量计划已删除",
        }
        return _drop_empty(
            {
                "kind": "milk_plan",
                "target": "schedule",
                "reason": reason,
                "label": label_by_reason.get(reason, "奶量计划已更新"),
                "plan_id": data.get("plan_id") or tool_result.get("plan_id"),
                "plan_type": data.get("plan_type") or tool_result.get("plan_type"),
                "dates": dates,
                "summary": tool_result.get("summary"),
            }
        )

    if name == "milk_calendar_mutate":
        reason = _calendar_feedback_reason(status)
        if not reason:
            return None
        dates = _plan_feedback_dates(data)
        label_by_reason = {
            "updated": "奶量日程已调整",
            "rescheduled": "奶量日程已重排",
            "deleted": "奶量日程已删除",
        }
        return _drop_empty(
            {
                "kind": "milk_plan",
                "target": "schedule",
                "reason": reason,
                "label": label_by_reason.get(reason, "奶量日程已调整"),
                "plan_id": data.get("plan_id"),
                "dates": dates,
                "summary": tool_result.get("summary"),
            }
        )
    return None


def _calendar_feedback_reason(status: str) -> str:
    if status in {"calendar_reschedule_applied", "calendar_reschedule_idempotent_replay", "calendar_range_shifted"}:
        return "rescheduled"
    if status in {"calendar_adjustment_applied", "calendar_adjustment_idempotent_replay", "calendar_range_patched", "calendar_item_updated"}:
        return "updated"
    if status in {"calendar_range_deleted", "calendar_item_deleted"}:
        return "deleted"
    return ""


def _plan_feedback_dates(data: dict[str, Any]) -> list[str]:
    candidates: list[Any] = [
        data.get("target_date"),
        data.get("date"),
    ]
    for key in ("calendar_items", "inserted_events", "applied_updates", "changed_items", "items"):
        value = data.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    calendar = data.get("calendar")
    if isinstance(calendar, dict):
        for key in ("items", "calendar_items"):
            value = calendar.get(key)
            if isinstance(value, list):
                candidates.extend(value)

    dates: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        for date in _dates_from_plan_feedback_candidate(item):
            if date in seen:
                continue
            seen.add(date)
            dates.append(date)
    return dates


def _dates_from_plan_feedback_candidate(item: Any) -> list[str]:
    if isinstance(item, dict):
        values = [
            item.get("date"),
            item.get("target_date"),
            item.get("start_time"),
            item.get("end_time"),
            item.get("new_start_time"),
            item.get("new_end_time"),
            item.get("old_start_time"),
            item.get("old_end_time"),
            item.get("task_time"),
        ]
    else:
        values = [item]

    dates: list[str] = []
    for value in values:
        text = str(value or "").strip()
        match = re.match(r"^\d{4}-\d{2}-\d{2}", text)
        if match:
            dates.append(match.group(0))
    return dates


def _compact_milk_plan_calendar_delta(delta: dict[str, Any]) -> dict[str, Any]:
    strategy_options = delta.get("strategy_options") if isinstance(delta.get("strategy_options"), dict) else {}
    compact_options: dict[str, Any] = {}
    for key, option in strategy_options.items():
        if not isinstance(option, dict):
            continue
        compact_options[str(key)] = _drop_empty(
            {
                "label": option.get("label"),
                "final_task_count": option.get("final_task_count"),
                "added_task_count": option.get("added_task_count"),
                "replaced_task_count": option.get("replaced_task_count"),
            }
        )
    return _drop_empty(
        {
            "date_range": delta.get("date_range") if isinstance(delta.get("date_range"), dict) else None,
            "existing_future_plan_task_count": delta.get("existing_future_plan_task_count"),
            "draft_task_count": delta.get("draft_task_count"),
            "has_existing_future_plan_tasks": delta.get("has_existing_future_plan_tasks"),
            "requires_calendar_write_strategy": delta.get("requires_calendar_write_strategy"),
            "selected_strategy": delta.get("selected_strategy"),
            "selected_final_task_count": delta.get("selected_final_task_count"),
            "strategy_options": compact_options,
        }
    )


def safe_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "tool_name": result.get("tool_name"),
    }
    tool_result = result.get("result")
    if isinstance(tool_result, dict):
        if "ok" in tool_result:
            safe["result_ok"] = bool(tool_result.get("ok"))
        for key in ("id", "skill_id", "status", "resource_id", "side_effect_performed", "summary", "missing_fields", "plan_id", "plan_type", "action", "next_step", "entry_id", "entry_date", "profile_onboarding_complete", "profile_onboarding_skipped"):
            if key in tool_result:
                safe[key] = tool_result[key]
        tool_data = tool_result.get("data")
        if isinstance(tool_data, dict):
            for key in ("requires_confirmation", "requires_medical_confirmation", "confirmation_question", "assistant_instruction"):
                if key in tool_data:
                    safe[key] = tool_data[key]
            if isinstance(tool_data.get("milk_flow_decision"), dict):
                safe["milk_flow_decision"] = tool_data["milk_flow_decision"]
            if result.get("tool_name") == "milk_analysis_intake_manage":
                for key in ("checklist", "missing_fields", "current_field", "next_question", "remaining_count", "executed_step", "next_tool"):
                    if key in tool_data:
                        safe[key] = tool_data[key]
                if isinstance(tool_data.get("analysis_context"), dict):
                    safe["analysis_context"] = tool_data["analysis_context"]
            if result.get("tool_name") == "milk_analysis_evaluate":
                assessment_result = tool_data.get("assessment_result") if isinstance(tool_data.get("assessment_result"), dict) else {}
                assessment_data = assessment_result.get("data") if isinstance(assessment_result.get("data"), dict) else {}
                if assessment_data:
                    safe["assessment"] = _compact_milk_assessment_data(assessment_data)
                for key in (
                    "missing_fields",
                    "suggested_questions",
                    "current_field",
                    "next_question",
                    "remaining_count",
                    "next_tool",
                    "executed_step",
                ):
                    if key in tool_data:
                        safe[key] = tool_data[key]
            if result.get("tool_name") == "milk_plan_mutate":
                for key in ("allowed_strategies", "validation"):
                    if key in tool_data:
                        safe[key] = tool_data[key]
                if isinstance(tool_data.get("calendar_delta"), dict):
                    safe["calendar_delta"] = _compact_milk_plan_calendar_delta(tool_data["calendar_delta"])
            if result.get("tool_name") == "milk_plan_preview_create":
                for key in ("workflow_intent", "continuation_instruction", "missing_fields", "suggested_questions", "current_field", "next_question", "remaining_count", "analysis_context"):
                    if key in tool_data:
                        safe[key] = tool_data[key]
                clinical_data = tool_data.get("clinical_assessment")
                if isinstance(clinical_data, dict):
                    safe["clinical_assessment"] = _compact_milk_context_status_data(clinical_data)
        if result.get("tool_name") in {"ui_form_create", "birth_plan_form_create", "hospital_bag_form_create", "birth_journey_intake_manage"} and isinstance(tool_result.get("form"), dict):
            safe["form"] = tool_result["form"]
        if result.get("tool_name") in {"labor_communication_card_create", "birth_journey_plan_card_create", "hospital_bag_card_create"} and isinstance(tool_result.get("card"), dict):
            safe["card"] = tool_result["card"]
        if result.get("tool_name") in {"milk_status_query", "milk_analysis_evaluate", "milk_plan_preview_create", "milk_plan_mutate"} and isinstance(tool_result.get("card"), dict):
            safe["card"] = tool_result["card"]
            if result.get("tool_name") == "milk_plan_mutate":
                plan_feedback = _plan_feedback_from_safe_tool_result(result.get("tool_name"), tool_result)
                if plan_feedback:
                    safe["plan_feedback"] = plan_feedback
                    if isinstance(plan_feedback.get("dates"), list):
                        safe["calendar_dates"] = plan_feedback.get("dates")
        if result.get("tool_name") == "milk_plan_mutate" and not isinstance(tool_result.get("card"), dict):
            plan_feedback = _plan_feedback_from_safe_tool_result(result.get("tool_name"), tool_result)
            if plan_feedback:
                safe["plan_feedback"] = plan_feedback
                if isinstance(plan_feedback.get("dates"), list):
                    safe["calendar_dates"] = plan_feedback.get("dates")
        if result.get("tool_name") == "milk_calendar_mutate":
            plan_feedback = _plan_feedback_from_safe_tool_result(result.get("tool_name"), tool_result)
            if plan_feedback:
                safe["plan_feedback"] = plan_feedback
                if isinstance(plan_feedback.get("dates"), list):
                    safe["calendar_dates"] = plan_feedback.get("dates")
        if result.get("tool_name") == "ibclc_consult_card_create" and isinstance(tool_result.get("card"), dict):
            safe["card"] = tool_result["card"]
        if result.get("tool_name") == "support_ticket_draft_create" and isinstance(tool_result.get("ticket"), dict):
            safe["ticket"] = tool_result["ticket"]
            if "submit_label" in tool_result:
                safe["submit_label"] = tool_result["submit_label"]
        if result.get("tool_name") == "hospital_bag_cart_update" and isinstance(tool_result.get("cart_update"), dict):
            safe["cart_update"] = tool_result["cart_update"]
            message = tool_result["cart_update"].get("message")
            if isinstance(message, str) and message.strip():
                safe["message"] = message.strip()
        if result.get("tool_name") == "hospital_bag_pump_recommend":
            for key in ("recommended_product", "alternatives", "cart_sync_suggestion", "message", "source_urls"):
                if key in tool_result:
                    safe[key] = tool_result[key]
        if result.get("tool_name") == "device_manual_search":
            media_voice = _media_voice_from_tool_result(tool_result)
            if media_voice:
                safe["media_voice"] = media_voice
        if result.get("tool_name") == "pregnancy_diary_manage" and isinstance(tool_result.get("diary"), dict):
            diary = tool_result["diary"]
            safe["diary"] = {
                "entry_id": diary.get("entry_id"),
                "entry_date": diary.get("entry_date"),
            }
        if result.get("tool_name") == QUICK_REPLIES_TOOL_NAME and isinstance(tool_result.get("quick_replies"), list):
            safe["quick_replies"] = tool_result["quick_replies"]
    if isinstance(result.get("error"), dict):
        safe["error"] = result["error"]
    return safe


def model_tool_output(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the follow-up model turn small after UI artifacts are already streamed."""

    safe = safe_tool_result(result)
    tool_name = str(safe.get("tool_name") or "")
    if tool_name == "milk_analysis_intake_manage":
        return _compact_milk_analysis_intake_output(safe, result)
    if tool_name == "milk_analysis_evaluate":
        return _compact_milk_analysis_evaluate_output(safe, result)
    if tool_name == "milk_plan_preview_create" and safe.get("status") == "milk_plan_needs_clinical_context":
        return _compact_milk_plan_missing_context_output(safe)
    if tool_name == "milk_plan_preview_create" and isinstance(safe.get("card"), dict):
        return _compact_milk_plan_card_output(safe, result)
    if tool_name == "milk_plan_preview_create":
        return _compact_milk_plan_no_card_output(safe, result)
    if tool_name == "milk_status_query" and isinstance(safe.get("card"), dict):
        return _compact_mom_baby_status_card_output(safe)
    if tool_name in MILK_WRITE_TOOL_NAMES and safe.get("status") == "needs_write_confirmation":
        return _compact_milk_write_confirmation_output(safe)
    if tool_name == "milk_plan_mutate":
        return _compact_milk_plan_mutate_output(safe)
    if tool_name == "pregnancy_diary_manage":
        return _compact_pregnancy_diary_output(safe, result)
    if tool_name == "birth_journey_intake_manage":
        return _compact_birth_journey_intake_output(safe, result)

    if tool_name == QUICK_REPLIES_TOOL_NAME:
        return {
            "ok": safe.get("ok"),
            "tool_name": safe.get("tool_name"),
            "status": safe.get("status"),
            "quick_replies_ready": bool(safe.get("quick_replies")),
            "final_response_instruction": "快捷输入已经作为前端 UI 元数据准备好。最终回复不要提到快捷输入，也不要把这些提示写进正文。",
        }

    if tool_name not in {
        "ui_form_create",
        "birth_plan_form_create",
        "birth_journey_intake_manage",
        "labor_communication_card_create",
        "birth_journey_plan_card_create",
        "birth_journey_plan_delete",
        "birth_journey_plan_todo_update",
        "hospital_bag_form_create",
        "hospital_bag_card_create",
        "hospital_bag_cart_update",
        "hospital_bag_pump_recommend",
        "ibclc_consult_card_create",
        "support_ticket_draft_create",
    }:
        return result

    if tool_name == "birth_journey_plan_card_create" and isinstance(safe.get("card"), dict):
        return _compact_birth_journey_plan_card_output(safe)
    if tool_name == "birth_journey_plan_delete":
        status = str(safe.get("status") or "").strip()
        instructions = {
            "plan_deleted": "孕期计划已经删除。最终回复只说：已删除孕期计划，宝宝和我页面不会再展示这份计划。需要时可以重新制定。",
            "plan_not_found": "没有找到 active 孕期计划。最终回复只说明当前没有可删除的孕期计划，不要说已经删除。",
            "plan_delete_failed": "删除孕期计划失败。最终回复简短说明暂时删除失败，请稍后再试。",
            "needs_delete_confirmation": "删除孕期计划前还需要用户明确确认。最终回复只询问是否确认删除，不要调用生成计划，也不要说已经删除。",
        }
        return {
            "ok": safe.get("ok"),
            "tool_name": safe.get("tool_name"),
            "status": safe.get("status"),
            "side_effect_performed": safe.get("side_effect_performed"),
            "plan_type": safe.get("plan_type"),
            "plan_id": safe.get("plan_id"),
            "summary": safe.get("summary"),
            "final_response_instruction": instructions.get(status, "最终回复简短说明删除孕期计划的处理结果。"),
        }
    if tool_name == "birth_journey_plan_todo_update":
        status = str(safe.get("status") or "").strip()
        updated_items = safe.get("updated_items") if isinstance(safe.get("updated_items"), list) else []
        instructions = {
            "todo_completion_updated": "孕期计划待办完成状态已经同步。最终回复只简短说明已同步，并点名已更新的事项；不要重新生成计划，不要复述完整计划。",
            "needs_todo_reference": "还不能确定要更新哪一项。最终回复只请用户提供接下来 7 天行动清单里的编号或事项名。",
            "todo_not_found": "没有匹配到对应事项。最终回复只请用户提供接下来 7 天行动清单里的编号或完整事项名，不要猜测。",
            "plan_not_found": "没有找到 active 孕期计划。最终回复只说明当前没有可更新的孕期计划。",
            "todo_update_failed": "更新孕期计划待办失败。最终回复简短说明暂时没同步成功，请稍后再试。",
        }
        return {
            "ok": safe.get("ok"),
            "tool_name": safe.get("tool_name"),
            "status": safe.get("status"),
            "side_effect_performed": safe.get("side_effect_performed"),
            "plan_type": safe.get("plan_type"),
            "plan_id": safe.get("plan_id"),
            "completed": safe.get("completed"),
            "updated_items": updated_items,
            "missing_refs": safe.get("missing_refs"),
            "ambiguous_refs": safe.get("ambiguous_refs"),
            "summary": safe.get("summary"),
            "final_response_instruction": instructions.get(status, "最终回复简短说明孕期计划待办完成状态的处理结果。"),
        }
    if tool_name == "birth_journey_plan_card_create" and safe.get("status") == "needs_required_context":
        question = str(safe.get("confirmation_question") or safe.get("summary") or "").strip()
        compact_missing = {
            "ok": safe.get("ok"),
            "tool_name": safe.get("tool_name"),
            "status": safe.get("status"),
            "summary": safe.get("summary"),
            "missing_fields": safe.get("missing_fields"),
            "confirmation_question": question,
            "final_response_instruction": (
                "孕期计划还不能生成。最终回复只向用户补问 confirmation_question 中缺失的信息，"
                "不要输出路线图、不要表达已经创建完成，也不要提待产包或分娩沟通单。"
            ),
        }
        return {key: value for key, value in compact_missing.items() if value not in (None, "", [])}
    if tool_name in {"hospital_bag_form_create", "hospital_bag_card_create", "labor_communication_card_create"} and str(safe.get("status") or "").startswith("needs_"):
        question = str(safe.get("confirmation_question") or safe.get("summary") or "").strip()
        compact_missing = {
            "ok": safe.get("ok"),
            "tool_name": safe.get("tool_name"),
            "status": safe.get("status"),
            "summary": safe.get("summary"),
            "missing_fields": safe.get("missing_fields"),
            "confirmation_question": question,
            "final_response_instruction": (
                "工具没有生成表单或结构化内容。最终回复只根据 confirmation_question 补问或提示用户先提交对应表单；"
                "不要说已经生成、不要输出清单或结构化内容，也不要改走其它产前服务。"
            ),
        }
        return {key: value for key, value in compact_missing.items() if value not in (None, "", [])}

    compact: dict[str, Any] = {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
    }
    followup_message = _assistant_followup_message(result)
    for key in (
        "id",
        "skill_id",
        "status",
        "resource_id",
        "side_effect_performed",
        "summary",
        "missing_fields",
        "requires_confirmation",
        "requires_medical_confirmation",
        "confirmation_question",
        "submit_label",
        "message",
        "cart_update",
        "recommended_product",
        "alternatives",
        "cart_sync_suggestion",
        "source_urls",
        "profile_onboarding_complete",
        "profile_onboarding_skipped",
        "error",
    ):
        if key in safe:
            compact[key] = safe[key]

    form = safe.get("form")
    if isinstance(form, dict):
        fields = form.get("fields")
        compact["form"] = {
            "id": form.get("id"),
            "title": form.get("title"),
            "field_count": len(fields) if isinstance(fields, list) else 0,
        }
        instruction = _form_artifact_final_response_instruction(tool_name)
        if instruction:
            compact["final_response_instruction"] = instruction

    card = safe.get("card")
    if isinstance(card, dict):
        card_json = card.get("card_json")
        card_json_dict = card_json if isinstance(card_json, dict) else {}
        compact["card"] = {
            "card_type": card.get("card_type") or card_json_dict.get("card_type"),
            "schema_version": card.get("schema_version") or card_json_dict.get("schema_version"),
            "created": True,
        }
        instruction = _card_artifact_final_response_instruction(tool_name, card)
        if instruction:
            compact["final_response_instruction"] = instruction

    ticket = safe.get("ticket")
    if isinstance(ticket, dict):
        compact["ticket"] = {
            "draft_id": ticket.get("draft_id"),
            "status": ticket.get("status"),
            "created": True,
        }
        instruction = _ticket_artifact_final_response_instruction(tool_name)
        if instruction:
            compact["final_response_instruction"] = instruction

    if followup_message:
        compact["final_response_instruction"] = _followup_final_response_instruction(
            tool_name,
            followup_message,
            artifact_created=isinstance(ticket, dict) or isinstance(card, dict) or isinstance(form, dict),
        )

    return compact


def _compact_pregnancy_diary_output(safe: dict[str, Any], raw_result: dict[str, Any]) -> dict[str, Any]:
    status = str(safe.get("status") or "").strip()
    compact: dict[str, Any] = {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
        "status": safe.get("status"),
        "action": safe.get("action"),
        "side_effect_performed": safe.get("side_effect_performed"),
        "summary": safe.get("summary"),
    }
    diary = safe.get("diary")
    if isinstance(diary, dict):
        compact["diary"] = {
            "entry_id": diary.get("entry_id"),
            "entry_date": diary.get("entry_date"),
        }
    tool_result = raw_result.get("result") if isinstance(raw_result.get("result"), dict) else {}
    raw_diary = tool_result.get("diary") if isinstance(tool_result.get("diary"), dict) else {}
    if status in {"diary_entry_read", "entry_already_exists"} and raw_diary:
        compact["diary"] = {
            "entry_id": raw_diary.get("entry_id"),
            "entry_date": raw_diary.get("entry_date"),
            "content": str(raw_diary.get("content") or ""),
        }
    if status == "diary_list_read":
        diary_list = tool_result.get("diary_list") if isinstance(tool_result.get("diary_list"), list) else []
        compact["diary_list"] = [
            {
                "entry_id": entry.get("entry_id"),
                "entry_date": entry.get("entry_date"),
                "content_preview": _text_preview(entry.get("content"), 160),
            }
            for entry in diary_list
            if isinstance(entry, dict)
        ]
        compact["final_response_instruction"] = (
            "孕期日记列表已经读取。最终回复根据 diary_list 里的日期和 content_preview 简短回答；"
            "如果用户要查看某一天完整内容，继续调用 pregnancy_diary_manage action=read，不要凭摘要补全。"
        )
    elif status == "diary_entry_read":
        if raw_diary:
            compact["final_response_instruction"] = "孕期日记已经读取。最终回复只根据 diary.content 回答用户，不要编造未返回的内容。"
        else:
            compact["final_response_instruction"] = "该日期暂无孕期日记。最终回复简短说明没有找到，并询问是否需要现在记录。"
    elif status in {"diary_entry_written", "diary_entry_created"}:
        compact["final_response_instruction"] = (
            "孕期日记已经记录。最终回复用 1-2 句中文自然告诉用户："
            "已经帮她记录好这篇孕期日记，可以在宝宝和我页面的孕期日记模块查看。"
            "不要复述完整日记内容，不要加入新的建议或判断。"
        )
    elif status == "diary_entry_updated":
        compact["final_response_instruction"] = (
            "孕期日记已经修改。最终回复用 1-2 句中文自然告诉用户："
            "已经帮她修改好这篇孕期日记，可以在宝宝和我页面的孕期日记模块查看。"
            "不要复述完整日记内容，不要加入新的建议或判断。"
        )
    elif status == "needs_diary_content":
        compact["final_response_instruction"] = "孕期日记还不能保存。最终回复只温和补问用户想记录或修改的具体内容，不要说已经保存。"
    elif status == "entry_already_exists":
        compact["final_response_instruction"] = (
            "该日期已经有孕期日记。若本轮用户提供了新的日记内容，不要输出最终回复，"
            "应结合 diary.content 和用户补充信息重新组织完整正文后调用 pregnancy_diary_manage action=update。"
            "如果用户只是询问是否已有记录，最终回复说明该日期已有记录即可；不要说已经新建。"
        )
    elif status == "entry_not_found":
        compact["final_response_instruction"] = "没有找到要修改的孕期日记。最终回复说明没有找到对应记录，并请用户补充日期或要修改的内容。"
    elif status == "diary_entry_deleted":
        compact["final_response_instruction"] = "孕期日记已经删除。最终回复只简短说明已删除这条孕期日记。"
    elif status == "needs_delete_confirmation":
        compact["final_response_instruction"] = "删除孕期日记前还需要用户明确确认。最终回复只询问是否确认删除，不要说已经删除。"
    elif status == "unsupported_action":
        compact["final_response_instruction"] = "这个孕期日记动作已停用。最终回复不要说已经记录；如果用户要记录，请改用 write 或 update，只记录用户明确表达的日记内容。"
    return {key: value for key, value in compact.items() if value not in (None, "", [])}


def _text_preview(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _form_artifact_final_response_instruction(tool_name: str) -> str:
    if tool_name == "birth_journey_intake_manage":
        return (
            "孕期计划基础信息表已经展示。最终回复只简短说明表单已打开，"
            "请用户填完提交；不要在聊天里重复表单字段，也不要说计划已经生成。"
        )
    if tool_name == "birth_plan_form_create":
        return (
            "分娩沟通单信息表已经展示。最终回复只输出下面两段中文，保留空行，"
            "不要改写、扩写，不要提表单里没有的字段、医院会额外确认什么或已经生成沟通单：\n\n"
            "好，我先帮你把分娩沟通单信息表打开了。\n\n"
            "你填完并提交后，我会按表单里确认的信息整理成一份给医生/护士看的沟通单。"
        )
    if tool_name == "hospital_bag_form_create":
        return (
            "待产包信息采集表已经展示。最终回复只用一句简短中文说明表单已打开，"
            "请用户填完提交后会按表单里确认的信息整理成一份清单；不要复述调用工具前已经说过的理由，"
            "不要提医院、家里已有物品、购物或下单。"
        )
    if tool_name == "ui_form_create":
        return (
            "信息确认表已经展示。最终回复只简短说明表单已打开，并请用户提交后继续处理；"
            "不要补充表单中没有的字段、不要承诺已经生成后续结果，也不要承诺任何外部提交。"
        )
    return ""


def _compact_birth_journey_intake_output(safe: dict[str, Any], raw_result: dict[str, Any]) -> dict[str, Any]:
    tool_result = raw_result.get("result") if isinstance(raw_result.get("result"), dict) else {}
    next_step = str(safe.get("next_step") or tool_result.get("next_step") or "").strip()
    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    auto_tool_result = tool_result.get("auto_tool_result") if isinstance(tool_result.get("auto_tool_result"), dict) else None
    compact: dict[str, Any] = {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
        "status": safe.get("status"),
        "action": safe.get("action"),
        "next_step": next_step,
        "summary": safe.get("summary"),
        "confirmation_question": safe.get("confirmation_question"),
        "assistant_instruction": safe.get("assistant_instruction") or data.get("assistant_instruction"),
        "completed_groups": data.get("completed_groups"),
    }
    if auto_tool_result:
        auto_compact = model_tool_output(auto_tool_result)
        compact["auto_executed_tool"] = "birth_journey_plan_card_create"
        compact["plan_result"] = {
            "status": auto_compact.get("status"),
            "card": auto_compact.get("card"),
        }
        compact["final_response_instruction"] = (
            str(auto_compact.get("final_response_instruction") or "孕期计划已经处理完成。最终回复简短说明处理结果。")
            + "\n\nbirth_journey_plan_card_create 已由应用侧自动执行，"
            "不要再次调用 birth_journey_plan_card_create，也不要再调用 ui_quick_replies_create。"
        )
        return {key: value for key, value in compact.items() if value not in (None, "", [])}
    if isinstance(safe.get("form"), dict):
        form = safe["form"]
        fields = form.get("fields")
        compact["form"] = {
            "id": form.get("id"),
            "title": form.get("title"),
            "field_count": len(fields) if isinstance(fields, list) else 0,
        }
        compact["final_response_instruction"] = _form_artifact_final_response_instruction("birth_journey_intake_manage")
    elif safe.get("status") == "ready_to_generate":
        plan_context = tool_result.get("plan_context")
        if isinstance(plan_context, dict):
            compact["plan_context"] = plan_context
        compact["final_response_instruction"] = (
            "孕期计划信息采集已完成，但本轮没有收到应用侧自动生成计划的结果。"
            "最终回复简短说明正在整理计划，请用户稍后重试；不要自行输出路线图或总结，"
            "也不要再次调用 birth_journey_plan_card_create。"
        )
    elif safe.get("status") == "blocked_by_symptoms":
        compact["final_response_instruction"] = (
            "用户报告了需要先处理的当前症状。最终回复先承接用户，再建议优先联系医生/医院确认；"
            "不要继续调用 birth_journey_plan_card_create。"
        )
    else:
        compact["final_response_instruction"] = (
            "最终回复只推进 next_step 对应的一步：如果有 confirmation_question，就只问这个问题；"
            "confirmation_question 是本轮唯一要问的问题；不要同时询问多个后续阶段，也不要生成孕期计划。"
            "不要自行追加字段完整性判断，尤其不要追问已经填写过的孕周是否为整周或 30+几天；"
            "如果已有 30周/孕30周 这类大致孕周，视为可用信息。"
            "当前步骤的快捷回复已由应用侧准备好，不要再调用 ui_quick_replies_create。"
        )
    return {key: value for key, value in compact.items() if value not in (None, "", [])}


def _card_artifact_final_response_instruction(tool_name: str, card: dict[str, Any]) -> str:
    if tool_name != "ibclc_consult_card_create":
        return ""
    card_json = card.get("card_json")
    card_body = card_json if isinstance(card_json, dict) else card
    chat = card_body.get("chat") if isinstance(card_body.get("chat"), dict) else {}
    note = str(chat.get("note") or "启动咨询后，会自动将你的问题同步给顾问").strip()
    consultant = card_body.get("consultant") if isinstance(card_body.get("consultant"), dict) else {}
    consultant_name = str(consultant.get("name") or "").strip()
    recommendation_reason = str(card_body.get("recommendation_reason") or "").strip()
    if not recommendation_reason:
        consultant_label = consultant_name or "这位 IBCLC 顾问"
        recommendation_reason = (
            f"我推荐 {consultant_label}，是因为这位顾问适合继续看含乳、排乳、亲喂/吸奶效果和乳房不适这类细节问题。"
        )
    if recommendation_reason[-1] not in "。！？!?":
        recommendation_reason = f"{recommendation_reason}。"
    return (
        "IBCLC 咨询入口已经展示。最终回复只输出下面两段中文，保留空行，"
        "不要改写、扩写，不要承诺已经预约、已经接通、顾问正在处理或任何入口内容里没有的服务能力。"
        "必须保留推荐理由；不要补写位置、距离、排班或更快接入，除非推荐理由里已经明确包含这些信息。"
        "语气要温柔承接，不要像系统通知：\n\n"
        "IBCLC 咨询入口我准备好了。\n\n"
        f"你刚才这个情况不用一个人反复猜。{recommendation_reason}勾选隐私政策和服务协议后，就可以启动咨询；{note}。"
    )


def _ticket_artifact_final_response_instruction(tool_name: str) -> str:
    if tool_name != "support_ticket_draft_create":
        return ""
    return (
        "售后工单信息表已经展示。最终回复要简短、温暖，并结合当前问题场景做情绪承接；"
        "自然表达，不要机械照抄工具结果或重复字段。"
        "最终回复最多两段，每段 1 句；“售后信息已经整理好”这个交付信息只能出现一次。"
        "不要提“草稿”“未提交”“确认后才提交”，也不要暴露内部服务是否打通；"
        "不要继续排查，不要重复工单字段，也不要列举购买渠道、照片、视频、联系方式等补充字段示例。"
    )


def _assistant_followup_message(result: dict[str, Any]) -> str:
    tool_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    followup = tool_result.get("assistant_followup") if isinstance(tool_result, dict) else None
    if not isinstance(followup, dict):
        return ""
    message = followup.get("message")
    return str(message or "").strip() if isinstance(message, str) else ""


def _followup_final_response_instruction(tool_name: str, message: str, *, artifact_created: bool = False) -> str:
    message = str(message or "").strip()
    if not message:
        return ""
    if tool_name == "hospital_bag_card_create":
        return _hospital_bag_final_response_instruction(message)
    if tool_name == "support_ticket_draft_create":
        if not artifact_created:
            return (
                "售后工单还没有创建。最终回复参考下面建议内容的情绪承接、语气和结构，"
                "自然表达并询问用户是否需要现在创建售后工单，不要机械照抄建议内容；"
                "最终回复最多两段，每段 1 句。"
                "不要说售后信息已经整理好、不要说工单信息表已经展示，"
                "不要提“草稿”“未提交”“确认后才提交”、demo、模拟提交或内部服务是否打通；"
                "不要继续排查，也不要列举购买渠道、照片、视频、联系方式等补充字段示例。"
                f"\n\n建议内容：\n{message}"
            )
        return (
            "售后工单信息表已经展示。最终回复参考下面建议内容的情绪承接、语气和结构，"
            "结合当前售后问题场景自然表达，不要机械照抄建议内容；"
            "最终回复最多两段，每段 1 句；"
            "必须说明售后信息已经整理好，并请用户查看是否需要补充或修改，但这类交付信息只能出现一次。"
            "不要提“草稿”“未提交”“确认后才提交”、demo、模拟提交或内部服务是否打通；"
            "不要重复工单字段，不要继续排查，也不要列举购买渠道、照片、视频、联系方式等补充字段示例。"
            f"\n\n建议内容：\n{message}"
        )
    return (
        "最终回复参考下面建议内容的语气、结构和关键信息自然表达；"
        "不要机械照抄、不要重复交付语或再补充无关下一步。"
        "如果建议内容里包含链接，不要在正文重复裸链接，相关入口由前端卡片展示。"
        f"\n\n建议内容：\n{message}"
    )


def _hospital_bag_final_response_instruction(message: str) -> str:
    material = _remove_hospital_bag_cart_link(message)
    return (
        "最终回复的内容结构：第一段 1 句说明待产包清单已整理好；"
        "第二段根据下方可参考内容，用 1-3 句说清楚特殊物品取舍，必须尽量保留具体条件和物品名；"
        "第三段说明购物车只是购买参考、不用一次买完，可以按清单优先级删减后再决定是否购买；"
        "最后一行必须使用回复示例里的 Markdown 购物车链接。"
        "如果下方内容里有“特殊物品我按这几个情况做了取舍”，正文要提炼其中 2-4 条，避免只说“做了取舍”；"
        "可参考这些具体表达：剖宫产时可以说准备了高腰宽松内裤/不压腹出院裤，收腹带先放在医生确认项；"
        "混合喂养时可以说保留哺乳文胸/哺乳背心、防溢乳垫、便携式吸奶器和储奶袋/储奶瓶；"
        "双胎时可以说宝宝出院衣物和包被数量按双胎调整；"
        "产后返工时可以说加入冷藏包/冰袋和吸奶配件清洁包。"
        "如果下方内容没有特殊物品取舍，就只说按孕周、喂养方式和医院确认项保留必要非常规物品，"
        "不要编造用户没给的情况。"
        "不要复述表单字段、设计思路、住院天数或完整清单；"
        "不要承诺真实下单、一键打包下单或医疗建议。"
        "\n\n回复示例：\n"
        "待产包清单我整理好了。\n\n"
        "特殊物品我按这几个情况做了取舍：\n\n"
        "- 考虑到你倾向剖宫产，我为你准备了高腰宽松内裤和不压腹出院裤/裙，收腹带先放在需要问医生的项目里。\n"
        "- 考虑到你准备混合喂养，我为你准备了哺乳文胸/哺乳背心、防溢乳垫、便携式吸奶器和储奶袋/储奶瓶，乳盾和奶瓶先放在需要确认的项目里。\n"
        "- 考虑到你预计6 周后返工，我为你准备了冷藏包/冰袋、储奶袋/储奶瓶和吸奶配件清洁包。\n"
        "具体可以看下面的待产包清单。\n\n"
        "我也把适合放入购物车参考的妈妈/宝宝用品整理好了，不用一次买完，先看清单里的优先级，按实际情况删减后再决定是否购买。\n\n"
        "**[打开待产包购物车](/hospital-bag-cart)**"
        f"\n\n下方内容只供提炼最终回复，不要原样输出本行说明：\n{material}"
    )


def _remove_hospital_bag_cart_link(message: str) -> str:
    paragraphs = [paragraph.strip() for paragraph in str(message or "").split("\n\n")]
    kept = [paragraph for paragraph in paragraphs if paragraph and "/hospital-bag-cart" not in paragraph]
    return "\n\n".join(kept).strip()


def _compact_birth_journey_plan_card_output(safe: dict[str, Any]) -> dict[str, Any]:
    card = safe.get("card")
    card_json = card.get("card_json") if isinstance(card, dict) else None
    card_json_dict = card_json if isinstance(card_json, dict) else {}
    response = _birth_journey_plan_final_response(card_json_dict)
    reused_existing_plan = safe.get("status") == "existing_plan_found"
    if reused_existing_plan:
        response = "你之前已经有一份孕期计划，我先沿用这份，不重复生成。\n\n" + response

    return _compact_card_tool_output(
        safe,
        (
            ("已找到用户已有的孕期计划并展示完整路线图。" if reused_existing_plan else "孕期计划已经展示完整路线图。")
            + "最终回复用 2-4 句中文自然组织语言，"
            "需要覆盖下面的接下来 7 天行动清单和完成项追问，但不要机械照抄。"
            + (
                "这是已有计划，最终回复说明已沿用这份计划，不要说新生成。"
                if reused_existing_plan
                else "这是新生成计划，最终回复必须自然表达：计划已生成，可以在宝宝和我页面查看，接下来我会按照计划主动提醒你哦。"
            )
            + "不要提本周重点、当前优先级或当前阶段总结；不要补充外部资料、来源引用或引用编号。"
            "不要使用“卡片”这类界面形式词，不要再输出“我先帮你生成”或“我整理好了”这类重复交付句，"
            "不要复述未来 2-4 周、后续大节点或完整计划。参考信息：\n\n"
            f"{response}"
        ),
    )


def _birth_journey_plan_final_response(card_json: dict[str, Any]) -> str:
    layers = card_json.get("planning_layers") if isinstance(card_json.get("planning_layers"), dict) else {}
    next_7_days = layers.get("next_7_days") if isinstance(layers.get("next_7_days"), dict) else {}
    next_7_items = next_7_days.get("items") if isinstance(next_7_days.get("items"), list) else []
    next_7_summary = _birth_journey_next_7_todo_summary(next_7_items)
    if next_7_summary:
        lines = [
            "接下来 7 天行动清单：" + next_7_summary + "。",
            "最终回复需要追问：这里面是否有已经完成的事项；如果有，可以让用户直接回复编号或事项名，你会同步更新完成状态。",
        ]
        return "\n\n".join(lines)

    return (
        "接下来 7 天行动清单暂时没有可复述的事项。"
        "最终回复只说明计划已生成，可以在宝宝和我页面查看；不要提本周重点、当前优先级或当前阶段总结。"
    )


def _birth_journey_next_7_todo_summary(items: list[Any]) -> str:
    titles: list[str] = []
    for item in items:
        if isinstance(item, dict):
            title = _clean_birth_journey_fragment(item.get("title"))
        else:
            title = _clean_birth_journey_fragment(item)
        if title:
            titles.append(title)
    return "；".join(f"{index + 1}. {title}" for index, title in enumerate(titles))


def _birth_journey_phase_summary_sentence(phase_title: str, watchout: str, action: str, goal: str) -> str:
    watchout = _birth_journey_watchout_fragment(watchout)
    action_fragment = _birth_journey_action_fragment(action)
    if phase_title and watchout and action_fragment:
        return f"你现在在{phase_title}，{watchout}；准备上先{action_fragment}。"
    if phase_title and watchout:
        return f"你现在在{phase_title}，{watchout}。"
    if phase_title and action_fragment:
        return f"你现在在{phase_title}，准备上先{action_fragment}。"
    if phase_title and goal:
        return f"你现在在{phase_title}，这阶段先{goal}。"
    if phase_title:
        return f"你现在在{phase_title}，可以先看计划里的当前阶段。"
    if watchout:
        return f"{watchout}。"
    if action_fragment:
        return f"你可以先{action_fragment}。"
    return "你可以先看计划里的当前阶段。"


def _birth_journey_current_phase(card_json: dict[str, Any]) -> dict[str, Any]:
    phases = card_json.get("phases")
    if not isinstance(phases, list):
        return {}
    for phase in phases:
        if isinstance(phase, dict) and phase.get("status") == "current":
            return phase
    for phase in phases:
        if isinstance(phase, dict):
            return phase
    return {}


def _first_birth_journey_item(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    for item in value:
        text = _clean_birth_journey_fragment(item)
        if text:
            return text
    return ""


def _clean_birth_journey_fragment(value: Any) -> str:
    return str(value or "").strip().rstrip("。；;，, ")


def _birth_journey_action_fragment(action: str) -> str:
    text = _clean_birth_journey_fragment(action)
    for prefix in ("你可以先", "可以先", "先"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    if text.startswith("生成"):
        return f"整理{text.removeprefix('生成').strip()}"
    return text


def _birth_journey_watchout_fragment(watchout: str) -> str:
    text = _clean_birth_journey_fragment(watchout).replace("；", "，")
    if text.startswith("这个阶段"):
        text = text.removeprefix("这个阶段").lstrip("，, ")
    if text.startswith("不用"):
        text = f"先{text}"
    return text


def _birth_journey_service_sentence(label: str) -> str:
    service = str(label or "").strip().rstrip("。；;，, ")
    if not service:
        return "接下来我可以再陪你按孕周、医院流程和支持人分工继续细化。"
    if service.startswith("继续"):
        service = service.removeprefix("继续").strip()
    return f"接下来我可以先陪你{service}。"


def _compact_milk_assessment_data(data: dict[str, Any]) -> dict[str, Any]:
    normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    days = normality.get("days") if isinstance(normality.get("days"), list) else []
    valid_days = [item for item in days if isinstance(item, dict) and item.get("ok") is True]
    valid_days = sorted(valid_days, key=lambda item: str(item.get("date") or ""))
    latest = valid_days[-1] if valid_days else {}
    estimated_values = [
        _safe_number(item.get("estimated_daily_milk_ml"))
        for item in valid_days
        if _safe_number(item.get("estimated_daily_milk_ml")) is not None
    ]
    low_days = [item for item in valid_days if str(item.get("status") or "").strip() == "low"]
    high_days = [item for item in valid_days if str(item.get("status") or "").strip() == "high"]
    pumping = data.get("pumping_summary") if isinstance(data.get("pumping_summary"), dict) else {}
    feeding = data.get("feeding_summary") if isinstance(data.get("feeding_summary"), dict) else {}
    calendar = data.get("calendar_task_summary") if isinstance(data.get("calendar_task_summary"), dict) else {}
    clinical = data.get("clinical_assessment") if isinstance(data.get("clinical_assessment"), dict) else {}
    domains = clinical.get("domains") if isinstance(clinical.get("domains"), dict) else {}
    record_domain = domains.get("record_completeness") if isinstance(domains.get("record_completeness"), dict) else {}
    baby_domain = domains.get("infant_intake") if isinstance(domains.get("infant_intake"), dict) else {}
    mother_domain = domains.get("maternal_breast_symptoms") if isinstance(domains.get("maternal_breast_symptoms"), dict) else {}
    recent_milk_rhythm = data.get("recent_milk_rhythm") if isinstance(data.get("recent_milk_rhythm"), dict) else {}

    compact = {
        "assessment_status": data.get("assessment_status"),
        "overall_status": normality.get("overall_status"),
        "summary": data.get("summary") or normality.get("summary"),
        "window": data.get("window"),
        "milk_flow_decision": data.get("milk_flow_decision") if isinstance(data.get("milk_flow_decision"), dict) else {},
        "control_suggestion": data.get("control_suggestion") if isinstance(data.get("control_suggestion"), dict) else {},
        "milk_volume": {
            "estimated_daily_milk_range": _number_range_text(estimated_values, "ml/天"),
            "latest_estimated_daily_milk_ml": latest.get("estimated_daily_milk_ml"),
            "latest_reference_range": _day_reference_range(latest),
            "valid_days": len(valid_days),
            "low_days": len(low_days),
            "high_days": len(high_days),
            "pumping_total_ml": pumping.get("total_ml"),
            "pumping_count": pumping.get("count"),
            "average_pumping_ml": pumping.get("average_ml"),
            "breastfeeding_estimate_note": "亲喂部分为估算" if any(item.get("estimated_breastfeeding_ml") for item in valid_days) else "",
        },
        "records": {
            "data_confidence": record_domain.get("data_confidence"),
            "missing_data": data.get("missing_data") or record_domain.get("missing_data"),
            "calendar_pump_task_count": calendar.get("pump_task_count"),
            "completed_pump_task_count": calendar.get("completed_pump_task_count"),
            "feeding_record_count": feeding.get("count"),
            "has_breastfeeding": feeding.get("has_breastfeeding"),
            "has_formula": feeding.get("has_formula"),
        },
        "baby_intake": {
            "status": baby_domain.get("status"),
            "wet_diapers_24h": baby_domain.get("wet_diapers_24h"),
            "baby_state": baby_domain.get("baby_state"),
            "poor_feeding": baby_domain.get("poor_feeding"),
            "lethargy": baby_domain.get("lethargy"),
        },
        "maternal_state": {
            "status": mother_domain.get("status"),
            "fever": mother_domain.get("fever"),
            "breast_redness": mother_domain.get("breast_redness"),
            "lump_or_hard_area": mother_domain.get("lump_or_hard_area"),
            "worsening_pain": mother_domain.get("worsening_pain"),
            "nipple_damage": mother_domain.get("nipple_damage"),
            "breast_fullness": mother_domain.get("breast_fullness"),
            "fullness_without_red_flags": mother_domain.get("fullness_without_red_flags"),
            "pain_level": mother_domain.get("pain_level"),
        },
        "clinical": {
            "risk_level": clinical.get("risk_level"),
            "data_confidence": clinical.get("data_confidence"),
            "risk_reasons": clinical.get("risk_reasons"),
            "next_actions": clinical.get("next_actions"),
            "plan_gate": clinical.get("plan_gate"),
        },
        "recent_milk_rhythm": _compact_recent_milk_rhythm_for_model(recent_milk_rhythm),
    }
    return _drop_empty(compact)


def _compact_recent_milk_rhythm_for_model(value: Any) -> dict[str, Any]:
    rhythm = value if isinstance(value, dict) else {}
    summary = rhythm.get("summary") if isinstance(rhythm.get("summary"), dict) else {}
    days = rhythm.get("days") if isinstance(rhythm.get("days"), list) else []
    selected_day = rhythm.get("selected_day") if isinstance(rhythm.get("selected_day"), dict) else {}
    return _drop_empty(
        {
            "summary": {
                "basis_date": summary.get("basis_date"),
                "basis_summary": summary.get("basis_summary"),
                "confidence": summary.get("confidence"),
                "usable_for_schedule": summary.get("usable_for_schedule"),
                "ask_daily_counts": summary.get("ask_daily_counts"),
                "ask_missing_records": summary.get("ask_missing_records"),
                "average_pumping_count_per_day": summary.get("average_pumping_count_per_day"),
                "average_nursing_count_per_day": summary.get("average_nursing_count_per_day"),
                "typical_pumping_times": summary.get("typical_pumping_times"),
                "typical_nursing_times": summary.get("typical_nursing_times"),
                "longest_gap_hours": summary.get("longest_gap_hours"),
            },
            "selected_day": _compact_recent_milk_rhythm_day(selected_day),
            "daily_records": [_compact_recent_milk_rhythm_day(day) for day in days if isinstance(day, dict)],
        }
    )


def _compact_recent_milk_rhythm_day(day: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "date": day.get("date"),
            "completeness": day.get("completeness"),
            "planned_pumping_times": day.get("planned_pumping_times"),
            "planned_nursing_times": day.get("planned_nursing_times"),
            "actual_pumping": _compact_recent_milk_events(day.get("actual_pumping"), include_amount=True, include_duration=True),
            "actual_nursing_times": day.get("actual_nursing_times"),
            "breastmilk_bottle": _compact_recent_milk_events(day.get("breastmilk_bottle"), include_amount=True),
            "formula_bottle": _compact_recent_milk_events(day.get("formula_bottle"), include_amount=True),
            "all_times": day.get("all_times"),
        }
    )


def _compact_recent_milk_events(value: Any, *, include_amount: bool = False, include_duration: bool = False) -> list[dict[str, Any]]:
    events = value if isinstance(value, list) else []
    compact = []
    for item in events:
        if not isinstance(item, dict):
            continue
        event = {"time": item.get("time")}
        if include_amount:
            event["milk_ml"] = item.get("milk_ml")
        if include_duration:
            event["duration_minutes"] = item.get("duration_minutes")
        compact.append(event)
    return _drop_empty(compact)


def _compact_milk_context_status_data(data: dict[str, Any]) -> dict[str, Any]:
    domains = data.get("domains") if isinstance(data.get("domains"), dict) else {}
    baby_domain = domains.get("infant_intake") if isinstance(domains.get("infant_intake"), dict) else {}
    mother_domain = domains.get("maternal_breast_symptoms") if isinstance(domains.get("maternal_breast_symptoms"), dict) else {}
    record_domain = domains.get("record_completeness") if isinstance(domains.get("record_completeness"), dict) else {}
    return _drop_empty(
        {
            "user_context": {
                "宝宝摄入信号": _plain_baby_status(baby_domain),
                "妈妈状态": _plain_mother_status(mother_domain),
                "记录情况": _plain_record_confidence(record_domain),
                "需要留意的情况": data.get("risk_reasons"),
                "下一步": _plain_clinical_next_step(data),
            }
        }
    )


def _compact_milk_analysis_intake_output(safe: dict[str, Any], original_result: dict[str, Any]) -> dict[str, Any]:
    tool_result = original_result.get("result") if isinstance(original_result.get("result"), dict) else {}
    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    intake = _compact_milk_intake_flow(data)
    analysis_context = data.get("analysis_context") if isinstance(data.get("analysis_context"), dict) else {}
    status = str(safe.get("status") or "").strip()
    if status == "milk_analysis_ready_to_evaluate" and analysis_context:
        return _drop_empty(
            {
                "ok": safe.get("ok"),
                "tool_name": safe.get("tool_name"),
                "status": safe.get("status"),
                "workflow": {
                    "intake": intake,
                    "next_tool": "milk_analysis_evaluate",
                },
                "analysis_context": analysis_context,
            }
        )
    compact = {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
        "status": safe.get("status"),
        "workflow": {
            "intake": intake,
            "next_tool": "milk_analysis_intake_manage",
        },
    }
    followup_message = _assistant_followup_message(original_result)
    next_question = str(intake.get("next_question") or followup_message or "").strip()
    if next_question:
        compact["final_response_instruction"] = _milk_single_question_final_response_instruction(
            next_question,
            field_id=intake.get("current_field"),
            remaining_count=intake.get("remaining_count"),
        )
    return _drop_empty(compact)


def _compact_milk_analysis_evaluate_output(safe: dict[str, Any], original_result: dict[str, Any]) -> dict[str, Any]:
    tool_result = original_result.get("result") if isinstance(original_result.get("result"), dict) else {}
    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    missing_fields = safe.get("missing_fields") if isinstance(safe.get("missing_fields"), list) else []
    if missing_fields:
        missing_context = _missing_milk_context_for_model(
            missing_fields,
            suggested_questions=safe.get("suggested_questions"),
            current_field=safe.get("current_field"),
            next_question=safe.get("next_question"),
            remaining_count=safe.get("remaining_count"),
        )
        compact = {
            "ok": safe.get("ok"),
            "tool_name": safe.get("tool_name"),
            "status": safe.get("status"),
            "workflow": {
                "missing_context": missing_context,
                "next_tool": data.get("next_tool"),
            },
            "需要继续确认": missing_context,
        }
        current_question = str(missing_context.get("current_question") or "").strip()
        if current_question:
            compact["final_response_instruction"] = _milk_single_question_final_response_instruction(
                current_question,
                field_id=missing_context.get("current_field"),
                remaining_count=missing_context.get("remaining_count"),
            )
        return _drop_empty(compact)
    assessment = data.get("assessment_result") if isinstance(data.get("assessment_result"), dict) else {}
    if assessment:
        assessment_data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
        assessment_safe: dict[str, Any] = {
            "ok": assessment.get("ok") is not False,
            "result_ok": assessment.get("ok") is not False,
            "tool_name": "milk_analysis_evaluate",
            "status": assessment.get("status"),
            "summary": assessment.get("summary"),
            "assessment": _compact_milk_assessment_data(assessment_data) if assessment_data else {},
        }
        for key in ("workflow_intent", "continuation_instruction", "missing_fields", "suggested_questions", "current_field", "next_question", "remaining_count"):
            if key in assessment_data:
                assessment_safe[key] = assessment_data[key]
        compact = _compact_milk_assessment_output(assessment_safe, {"result": assessment})
        if isinstance(compact, dict):
            workflow = compact.get("workflow") if isinstance(compact.get("workflow"), dict) else {}
            compact["workflow"] = _drop_empty(
                {
                    **workflow,
                    "next_tool": data.get("next_tool") or workflow.get("next_tool"),
                    "intake_complete": True,
                }
            )
            return compact
    return _drop_empty(
        {
            "ok": safe.get("ok"),
            "tool_name": safe.get("tool_name"),
            "status": safe.get("status"),
            "summary": safe.get("summary"),
            "workflow": {
                "next_tool": data.get("next_tool"),
            },
        }
    )


def _compact_milk_intake_flow(data: dict[str, Any]) -> dict[str, Any]:
    flow_state = data.get("flow_state") if isinstance(data.get("flow_state"), dict) else {}
    checklist = data.get("checklist") if isinstance(data.get("checklist"), list) else flow_state.get("checklist") if isinstance(flow_state.get("checklist"), list) else []
    return _drop_empty(
        {
            "stage": flow_state.get("stage"),
            "executed_step": data.get("executed_step"),
            "completed_fields": [item.get("id") for item in checklist if isinstance(item, dict) and item.get("status") == "collected"],
            "missing_fields": data.get("missing_fields") if isinstance(data.get("missing_fields"), list) else [
                item.get("id") for item in checklist if isinstance(item, dict) and item.get("status") != "collected"
            ],
            "current_field": data.get("current_field") or flow_state.get("current_field"),
            "next_question": data.get("next_question") or flow_state.get("next_question"),
            "remaining_count": data.get("remaining_count"),
        }
    )


def _compact_milk_assessment_output(safe: dict[str, Any], original_result: dict[str, Any] | None = None) -> dict[str, Any]:
    assessment = safe.get("assessment") if isinstance(safe.get("assessment"), dict) else {}
    original_tool_result = original_result.get("result") if isinstance(original_result, dict) else {}
    original_data = original_tool_result.get("data") if isinstance(original_tool_result, dict) and isinstance(original_tool_result.get("data"), dict) else {}
    status = str(
        assessment.get("overall_status")
        or original_data.get("assessment_status")
        or safe.get("status")
        or ""
    ).strip()
    flow_decision = (
        original_data.get("milk_flow_decision")
        if isinstance(original_data.get("milk_flow_decision"), dict)
        else assessment.get("milk_flow_decision")
        if isinstance(assessment.get("milk_flow_decision"), dict)
        else {}
    )
    missing_context = _missing_milk_context_for_model(
        safe.get("missing_fields"),
        suggested_questions=safe.get("suggested_questions"),
        current_field=safe.get("current_field"),
        next_question=safe.get("next_question"),
        remaining_count=safe.get("remaining_count"),
    )
    compact = {
        "ok": safe.get("result_ok", safe.get("ok")),
        "tool_name": safe.get("tool_name"),
        "facts": _milk_assessment_facts_for_model(
            original_data,
            compact_assessment=assessment,
            include_recent_milk_rhythm=False,
        ),
        "interpretation": _milk_assessment_interpretation_for_model(
            original_data,
            compact_assessment=assessment,
            status=status,
            include_control_suggestion=False,
        ),
        "workflow": _drop_empty(
            {
                "status": safe.get("status"),
                "missing_context": missing_context,
                "milk_flow_decision": _plain_milk_flow_decision(flow_decision),
                "next_tool": _milk_flow_next_tool(flow_decision),
            }
        ),
        # Backward-compatible aliases for existing downstream prompt tests.
        "milk_flow_decision": _plain_milk_flow_decision(flow_decision),
        "需要继续确认": missing_context,
    }
    current_question = str(missing_context.get("current_question") or "").strip()
    if current_question:
        compact["final_response_instruction"] = _milk_single_question_final_response_instruction(
            current_question,
            field_id=missing_context.get("current_field"),
            remaining_count=missing_context.get("remaining_count"),
        )
    else:
        plan_decision = flow_decision.get("plan_decision") if isinstance(flow_decision.get("plan_decision"), dict) else {}
        if plan_decision.get("can_start_plan") is True:
            compact["final_response_instruction"] = (
                "奶量分析已经完成，信息采集已经结束。最终回复只能包含两部分："
                "1) 用 1-2 句说明当前判断和适合的方向；"
                "2) 只询问用户是否现在生成奶量计划。"
                "不要继续追问任何新的诊断、排程或计划细节；不要把记录节奏、分析素材或计划排程参考改写成新问题。"
                "任何用户追问都必须来自 milk_analysis_intake_manage 当前返回的 next_question。"
                "不要说已经开始制定计划，不要直接给完整计划，也不要调用或暗示已经生成计划卡片。"
            )
    return _drop_empty(compact)


def _milk_assessment_facts_for_model(
    data: dict[str, Any],
    *,
    compact_assessment: dict[str, Any] | None = None,
    include_recent_milk_rhythm: bool = True,
) -> dict[str, Any]:
    compact_assessment = compact_assessment if isinstance(compact_assessment, dict) else {}
    source_context = data.get("source_record_context") if isinstance(data.get("source_record_context"), dict) else {}
    normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    pumping = data.get("pumping_summary") if isinstance(data.get("pumping_summary"), dict) else {}
    feeding = data.get("feeding_summary") if isinstance(data.get("feeding_summary"), dict) else {}
    calendar = data.get("calendar_task_summary") if isinstance(data.get("calendar_task_summary"), dict) else {}
    recent_milk_rhythm = data.get("recent_milk_rhythm") if isinstance(data.get("recent_milk_rhythm"), dict) else {}
    return _drop_empty(
        {
            "window": data.get("window") or source_context.get("window") or compact_assessment.get("window"),
            "record_counts": source_context.get("record_counts") or _milk_record_counts_from_summaries(pumping, feeding),
            "raw_records": source_context.get("raw_records"),
            "daily_rollups": source_context.get("daily_rollups") or _milk_daily_rollups_from_normality(normality),
            "summaries": {
                "pumping": pumping,
                "feeding": feeding,
                "calendar_tasks": calendar,
            },
            "recent_milk_rhythm": (
                (
                    _compact_recent_milk_rhythm_for_model(recent_milk_rhythm)
                    if recent_milk_rhythm
                    else compact_assessment.get("recent_milk_rhythm")
                )
                if include_recent_milk_rhythm
                else None
            ),
        }
    )


def _milk_assessment_interpretation_for_model(
    data: dict[str, Any],
    *,
    compact_assessment: dict[str, Any] | None = None,
    status: str = "",
    include_control_suggestion: bool = True,
) -> dict[str, Any]:
    compact_assessment = compact_assessment if isinstance(compact_assessment, dict) else {}
    normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    clinical = data.get("clinical_assessment") if isinstance(data.get("clinical_assessment"), dict) else {}
    compact_clinical = compact_assessment.get("clinical") if isinstance(compact_assessment.get("clinical"), dict) else {}
    return _drop_empty(
        {
            "assessment_status": data.get("assessment_status") or status,
            "overall_status": normality.get("overall_status") or compact_assessment.get("overall_status"),
            "summary": data.get("summary") or normality.get("summary") or compact_assessment.get("summary"),
            "milk_volume": compact_assessment.get("milk_volume"),
            "records": compact_assessment.get("records"),
            "baby_intake": compact_assessment.get("baby_intake"),
            "maternal_state": compact_assessment.get("maternal_state"),
            "clinical": compact_clinical or clinical,
            "control_suggestion": (
                data.get("control_suggestion") or compact_assessment.get("control_suggestion")
            )
            if include_control_suggestion
            else None,
        }
    )


def _milk_record_counts_from_summaries(pumping: dict[str, Any], feeding: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "pumping": pumping.get("count"),
            "feeding": feeding.get("count"),
        }
    )


def _milk_daily_rollups_from_normality(normality: dict[str, Any]) -> list[dict[str, Any]]:
    days = normality.get("days") if isinstance(normality.get("days"), list) else []
    rollups = []
    for day in days:
        if not isinstance(day, dict):
            continue
        rollups.append(
            _drop_empty(
                {
                    "date": day.get("date"),
                    "postpartum_day": day.get("postpartum_day"),
                    "pumping_ml_total": day.get("pumping_ml_total"),
                    "pumping_count": day.get("pumping_count"),
                    "breastfeeding_count": day.get("breastfeeding_count"),
                    "breastmilk_bottle_ml": day.get("breastmilk_bottle_ml"),
                    "breastmilk_bottle_count": day.get("breastmilk_bottle_count"),
                    "feeding_count_total": day.get("feeding_count_total"),
                    "estimated_daily_milk_ml": day.get("estimated_daily_milk_ml"),
                    "estimated_breastfeeding_ml": day.get("estimated_breastfeeding_ml"),
                    "estimated_breastmilk_bottle_ml": day.get("estimated_breastmilk_bottle_ml"),
                    "breastfeeding_per_session_ml": day.get("breastfeeding_per_session_ml"),
                    "yield_reference": day.get("yield_reference"),
                    "estimated_frequency": day.get("estimated_frequency"),
                    "milk_basis_rule": day.get("milk_basis_rule"),
                    "status": day.get("status"),
                    "normal": day.get("normal"),
                    "ok": day.get("ok"),
                    "rule_hit": day.get("rule_hit"),
                    "message": day.get("message"),
                }
            )
        )
    return rollups


def _milk_flow_next_tool(flow_decision: dict[str, Any]) -> str:
    plan_decision = flow_decision.get("plan_decision") if isinstance(flow_decision.get("plan_decision"), dict) else {}
    return str(plan_decision.get("next_tool") or "").strip()


def _milk_assessment_data_for_model(data: dict[str, Any]) -> dict[str, Any]:
    blocked_keys = {"assistant_followup", "card", "source_record_context"}
    return _drop_empty({key: value for key, value in data.items() if key not in blocked_keys})


def _plain_milk_assessment_context(assessment: dict[str, Any], *, status: str) -> dict[str, Any]:
    milk = assessment.get("milk_volume") if isinstance(assessment.get("milk_volume"), dict) else {}
    records = assessment.get("records") if isinstance(assessment.get("records"), dict) else {}
    baby = assessment.get("baby_intake") if isinstance(assessment.get("baby_intake"), dict) else {}
    mother = assessment.get("maternal_state") if isinstance(assessment.get("maternal_state"), dict) else {}
    clinical = assessment.get("clinical") if isinstance(assessment.get("clinical"), dict) else {}
    rhythm = assessment.get("recent_milk_rhythm") if isinstance(assessment.get("recent_milk_rhythm"), dict) else {}

    low_days = milk.get("low_days")
    high_days = milk.get("high_days")
    valid_days = milk.get("valid_days")
    milk_facts = []
    range_text = str(milk.get("estimated_daily_milk_range") or "").strip()
    reference = str(milk.get("latest_reference_range") or "").strip()
    if range_text:
        milk_facts.append(f"近几天含亲喂估算大约是 {range_text}")
    if reference:
        milk_facts.append(f"最近一天参考区间大约是 {reference}")
    if low_days:
        milk_facts.append(f"{valid_days or '近几'} 天里有 {low_days} 天偏低")
    if high_days:
        milk_facts.append(f"{valid_days or '近几'} 天里有 {high_days} 天偏高")

    record_facts = []
    missing = records.get("missing_data")
    confidence = str(records.get("data_confidence") or "").strip()
    if confidence == "low":
        record_facts.append("现在记录还不够，不适合直接下结论")
    elif confidence == "medium":
        record_facts.append("记录大致可用，完整度以综合奶量分析状态机为准")
    elif confidence == "high":
        record_facts.append("记录比较完整，可以作为这次判断的主要依据")
    if missing:
        record_facts.append("最近记录里有一些关键缺口")
    if records.get("has_breastfeeding"):
        record_facts.append("里面包含亲喂，亲喂奶量只能按估算看")

    baby_text = _plain_baby_status(baby)
    mother_text = _plain_mother_status(mother)

    return _drop_empty(
        {
            "判断结果": {
                "奶量产出": _plain_milk_status(status),
                "记录情况": record_facts,
                "宝宝摄入信号": baby_text,
                "妈妈状态": mother_text,
            },
            "判断依据": milk_facts,
            "需要留意的情况": clinical.get("risk_reasons"),
            "最近7天吸奶和亲喂节奏": _plain_recent_milk_rhythm_context(rhythm),
            "不能这样推断": _milk_assessment_do_not_infer(status),
        }
    )


def _plain_milk_flow_decision(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    plan_decision = value.get("plan_decision") if isinstance(value.get("plan_decision"), dict) else {}
    return _drop_empty(
        {
            "当前流程阶段": value.get("stage"),
            "还缺什么": _plain_missing_milk_assessment_fields(value.get("missing_user_inputs")),
            "现在能不能开始制定计划": plan_decision.get("can_start_plan"),
            "建议计划方向": _plain_milk_plan_type(plan_decision.get("recommended_plan_type")),
            "原因": plan_decision.get("reason_for_user"),
            "下一步工具": plan_decision.get("next_tool"),
            "数据库已读取的信息": value.get("facts_from_database") if isinstance(value.get("facts_from_database"), dict) else {},
        }
    )


def _plain_milk_plan_type(value: Any) -> str:
    plan_type = str(value or "").strip()
    labels = {
        "increase_milk": "追奶计划",
        "maintain_milk": "稳奶计划",
        "decrease_milk": "减奶计划",
    }
    return labels.get(plan_type, plan_type)


def _plain_recent_milk_rhythm_context(value: Any) -> dict[str, Any]:
    rhythm = value if isinstance(value, dict) else {}
    summary = rhythm.get("summary") if isinstance(rhythm.get("summary"), dict) else {}
    days = rhythm.get("daily_records") if isinstance(rhythm.get("daily_records"), list) else rhythm.get("days")
    days = days if isinstance(days, list) else []
    facts = []
    basis_summary = str(summary.get("basis_summary") or "").strip()
    if basis_summary:
        facts.append(basis_summary)
    pumping_times = [str(item) for item in (summary.get("typical_pumping_times") or []) if str(item).strip()]
    nursing_times = [str(item) for item in (summary.get("typical_nursing_times") or []) if str(item).strip()]
    if pumping_times:
        facts.append(f"参考吸奶时间：{'、'.join(pumping_times)}。")
    if nursing_times:
        facts.append(f"参考亲喂时间：{'、'.join(nursing_times)}。")
    if summary.get("usable_for_schedule") is True:
        facts.append("这些记录可以用来安排计划时间，不需要重复追问已知节奏信息。")
    elif summary.get("ask_daily_counts") is True:
        facts.append("最近时间记录不足，计划会优先使用已读记录或默认模板，不把补充频次作为排程前置条件。")
    daily_records = []
    for day in days:
        if not isinstance(day, dict):
            continue
        actual_pumping = day.get("actual_pumping") if isinstance(day.get("actual_pumping"), list) else []
        pumping_items = []
        for item in actual_pumping:
            if not isinstance(item, dict):
                continue
            time = str(item.get("time") or "").strip()
            if not time:
                continue
            detail = time
            milk = _safe_number(item.get("milk_ml"))
            duration = item.get("duration_minutes")
            extras = []
            if milk is not None:
                extras.append(f"{milk:.0f} ml")
            if duration:
                extras.append(f"{duration} 分钟")
            if extras:
                detail = f"{detail}（{'，'.join(extras)}）"
            pumping_items.append(detail)
        daily_records.append(
            _drop_empty(
                {
                    "日期": day.get("date"),
                    "记录完整度": day.get("completeness"),
                    "计划吸奶时间": day.get("planned_pumping_times"),
                    "计划亲喂时间": day.get("planned_nursing_times"),
                    "实际吸奶": pumping_items,
                    "实际亲喂时间": day.get("actual_nursing_times"),
                    "瓶喂母乳时间": [item.get("time") for item in day.get("breastmilk_bottle", []) if isinstance(item, dict)],
                    "奶粉时间": [item.get("time") for item in day.get("formula_bottle", []) if isinstance(item, dict)],
                }
            )
        )
    return _drop_empty({"摘要": facts, "每天明细": daily_records})


def _plain_missing_milk_assessment_fields(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else []
    labels = {
        "infant_signals": "宝宝近 24 小时尿布、精神和吃奶表现",
        "maternal_symptoms": "妈妈有没有发热、乳房红肿、硬块或疼痛加重",
        "infant_wet_diapers": "宝宝近 24 小时尿量/尿布情况",
        "infant_state_or_satisfaction": "宝宝精神状态和吃奶后表现",
        "infant_state_or_feeding_satisfaction": "宝宝精神状态和吃奶后表现",
        "infant_growth_signal": "宝宝近期体重增长情况",
        "maternal_red_flags": "妈妈有没有发热、寒战、红肿、硬块或疼痛加重",
        "maternal_breast_comfort": "吸奶或亲喂后乳房舒适度",
    }
    return [labels.get(str(item), str(item)) for item in raw_items if str(item).strip()]


def _milk_question_for_missing_field(field_id: str) -> str:
    questions = {
        "infant_wet_diapers": "宝宝近 24 小时尿量/尿布大概正常吗？",
        "infant_state_or_satisfaction": "宝宝精神状态怎么样，吃奶后通常能安稳一会儿吗？",
        "infant_state_or_feeding_satisfaction": "宝宝精神状态怎么样，吃奶后通常能安稳一会儿吗？",
        "infant_growth_signal": "宝宝最近体重增长看起来还正常吗？",
        "maternal_red_flags": "你有没有发热、寒战、乳房明显红肿、硬块，或疼痛越来越重？",
        "maternal_breast_comfort": "吸奶或亲喂后乳房是比较舒服，还是还会胀、排不空或疼？",
    }
    return questions.get(str(field_id or "").strip(), "")


def _milk_single_question_final_response_instruction(question: str, *, field_id: Any = None, remaining_count: Any = None) -> str:
    question = str(question or "").strip()
    if not question:
        return ""
    field_text = str(field_id or "").strip()
    count_text = ""
    if isinstance(remaining_count, int) and remaining_count > 0:
        count_text = f" 当前内部仍有 {remaining_count} 项待确认，但本轮只问当前这一项。"
    return (
        "当前仍处于奶量分析信息采集阶段。最终回复可以先用一句话简短承接用户刚补充的信息，"
        "但必须只追问下面这一项。"
        "不要说“最后一个”“最后再问”“只差一个”“再确认最后一个”；"
        "不要同时追问其它缺失项，不要输出奶量结论，不要给追奶、稳奶或减奶计划。"
        f"{count_text}"
        + (f"\n\n当前字段：{field_text}" if field_text else "")
        + f"\n\n当前只问：\n{question}"
    )


def _missing_milk_context_for_model(
    value: Any,
    *,
    suggested_questions: Any = None,
    current_field: Any = None,
    next_question: Any = None,
    remaining_count: Any = None,
) -> dict[str, Any]:
    labels = _plain_missing_milk_assessment_fields(value)
    raw_items = [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []
    current_field_text = str(current_field or "").strip() or (raw_items[0] if raw_items else "")
    suggested = [str(item).strip() for item in suggested_questions if str(item).strip()] if isinstance(suggested_questions, list) else []
    current_question = str(next_question or "").strip()
    if not current_question and suggested:
        current_question = suggested[0]
    if not current_question and current_field_text:
        current_question = _milk_question_for_missing_field(current_field_text)
    remaining_count_value = remaining_count if isinstance(remaining_count, int) else len(raw_items)
    why = []
    if "infant_signals" in raw_items or any(item in raw_items for item in ("infant_wet_diapers", "infant_state_or_feeding_satisfaction", "infant_growth_signal")):
        why.append("宝宝尿布、精神和吃奶表现会影响下一步是继续观察、调整节奏，还是先联系专业支持。")
    if "maternal_symptoms" in raw_items or any(item in raw_items for item in ("maternal_red_flags", "maternal_breast_comfort")):
        why.append("妈妈有没有发热、红肿、硬块或疼痛加重，会影响是否适合继续调整奶量安排。")
    return _drop_empty(
        {
            "还需要确认": labels,
            "current_field": current_field_text,
            "current_question": current_question,
            "remaining_count": remaining_count_value,
            "当前只问": current_question,
            "回复限制": "最终回复只问 current_question 这一项，不要说最后一个或只差一个，也不要同时追问其它字段。",
            "为什么要确认": why,
        }
    )


def _plain_record_confidence(record_domain: dict[str, Any]) -> str:
    confidence = str(record_domain.get("data_confidence") or "").strip()
    if confidence == "high":
        return "记录比较完整，可以作为这次判断的主要依据。"
    if confidence == "medium":
        return "记录大致可用，完整度以综合奶量分析状态机为准。"
    if confidence == "low":
        return "记录还不够，需要先补最近吸奶、亲喂或瓶喂情况。"
    return "还没有足够记录信息。"


def _plain_clinical_next_step(data: dict[str, Any]) -> str:
    risk_level = str(data.get("risk_level") or "").strip()
    if risk_level in {"medical_recommended", "urgent"}:
        return "先联系医生或线下医疗渠道确认身体情况。"
    if risk_level == "ibclc_recommended":
        return "适合结合 IBCLC 一起看含乳、吸奶或亲喂效果，以及乳房不适。"
    if str(data.get("data_confidence") or "").strip() == "low":
        return "先补齐宝宝状态和妈妈乳房/全身状态，再继续判断。"
    return "宝宝和妈妈状态目前没有提示需要先暂停计划的信号，可以继续结合奶量记录看下一步。"


def _milk_assessment_do_not_infer(status: str) -> list[str]:
    rules = ["宝宝是否吃够要结合尿布、精神和吃奶表现一起看", "不要把一次评估自动扩展成计划制定"]
    if status == "under_supply_alert":
        rules.extend(
            [
                "不要把宝宝需求变高说成奶量产出偏低的原因",
                "不要说记录完整就应该马上制定追奶计划",
                "不要把宝宝尿布、精神、吃奶满足感当作解释低奶量的原因；它们只用于判断宝宝摄入是否够",
            ]
        )
    if status == "over_supply_alert":
        rules.append("不要建议突然减吸或一次性大幅减少")
    return rules


def _plain_milk_status(status: str) -> str:
    if status == "under_supply_alert":
        return "最近整体偏低，但要结合记录是否完整、宝宝状态和妈妈状态一起看。"
    if status == "over_supply_alert":
        return "最近整体偏高，重点不是突然少吸，而是温和减少过强刺激。"
    if status == "normal":
        return "最近整体在可接受范围里，重点是稳住节奏。"
    if status == "needs_clinical_context":
        return "还差宝宝和妈妈状态，先把会影响判断的信息补齐。"
    return "现在信息还不够完整，先补关键情况再判断。"


def _plain_baby_status(baby: dict[str, Any]) -> str:
    status = str(baby.get("status") or "").strip()
    if status == "concern":
        return "宝宝摄入信号需要优先确认，比如尿布、精神、吃奶或体重。"
    if status == "reassuring":
        return "宝宝尿布、精神或吃奶表现目前看起来比较安心。"
    return "还没有足够的宝宝尿布、精神或吃奶信息。"


def _plain_mother_status(mother: dict[str, Any]) -> str:
    status = str(mother.get("status") or "").strip()
    if status == "medical_concern":
        return "妈妈有发热或明显乳房不适信号，先优先处理身体不适，再看奶量计划。"
    if status == "ibclc_concern":
        return "妈妈有乳房不适、乳头损伤或反复堵奶信号，适合结合 IBCLC 看。"
    if status == "reassuring":
        if mother.get("fullness_without_red_flags") is True:
            return "妈妈主要是胀或感觉没排空，但暂时没有发热、明显红肿、硬块加重或疼痛加重信号；这个情况可以作为奶量计划里的约束一起处理。"
        return "妈妈这边暂时没有明显发热、红肿、硬块或疼痛加重信号。"
    return "还没有足够的妈妈乳房或全身状态信息。"


def _safe_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _number_range_text(values: list[float], unit: str) -> str:
    values = [value for value in values if isinstance(value, (int, float))]
    if not values:
        return ""
    low = min(values)
    high = max(values)
    if abs(low - high) < 0.5:
        return f"{low:.0f} {unit}"
    return f"{low:.0f}-{high:.0f} {unit}"


def _day_reference_range(day: dict[str, Any]) -> str:
    reference = day.get("yield_reference") if isinstance(day.get("yield_reference"), dict) else {}
    p15 = _safe_number(reference.get("p15"))
    p85 = _safe_number(reference.get("p85"))
    if p15 is None or p85 is None:
        return ""
    return f"{p15:.0f}-{p85:.0f} ml/天"


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _drop_empty(item)) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := _drop_empty(item)) not in (None, "", [], {})]
    return value


def _compact_mom_baby_status_card_output(safe: dict[str, Any]) -> dict[str, Any]:
    card = safe.get("card")
    card_json = card.get("card_json") if isinstance(card, dict) else None
    card_json_dict = card_json if isinstance(card_json, dict) else {}
    tabs = card_json_dict.get("tabs") if isinstance(card_json_dict.get("tabs"), list) else []
    return {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
        "card": {
            "card_type": (card or {}).get("card_type") if isinstance(card, dict) else "mom_baby_status_card",
            "schema_version": (card or {}).get("schema_version") if isinstance(card, dict) else "1.0",
            "created": True,
        },
        "status_tabs": [
            {"id": tab.get("id"), "title": tab.get("title")}
            for tab in tabs
            if isinstance(tab, dict)
        ],
        "final_response_instruction": (
            "宝宝和我页面已经按“妈妈数字分身”和“宝宝数字分身”两个顶部 tab 展示。"
            "最终回复只提示用户可以切换 tab 查看，不要把两个 tab 的指标和建议逐条复述到正文里。"
        ),
    }


def _compact_milk_plan_card_output(safe: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    card = safe.get("card")
    card_json = card.get("card_json") if isinstance(card, dict) else None
    card_json_dict = card_json if isinstance(card_json, dict) else {}
    data = _tool_result_data(result)
    calendar_sync_prompt = _milk_plan_calendar_sync_prompt(data)
    plan_preview = _compact_milk_plan_preview_for_model(result)
    compact: dict[str, Any] = {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
        "card": {
            "card_type": (card or {}).get("card_type") if isinstance(card, dict) else "milk_plan_card",
            "schema_version": (card or {}).get("schema_version") if isinstance(card, dict) else "1.0",
            "created": True,
        },
        "facts": _milk_plan_facts_for_model(data),
        "interpretation": _milk_plan_interpretation_for_model(data),
        "workflow": _drop_empty(
            {
                "requires_confirmation": safe.get("requires_confirmation"),
                "requires_medical_confirmation": safe.get("requires_medical_confirmation"),
                "confirmation_question": safe.get("confirmation_question"),
                "calendar_sync_prompt": calendar_sync_prompt,
                "milk_flow_decision": _plain_milk_flow_decision(safe.get("milk_flow_decision") if isinstance(safe.get("milk_flow_decision"), dict) else {}),
                "next_actions": ["同步到日历", "调整计划", "展开具体时间表"],
            }
        ),
        "next_actions": ["同步到日历", "调整计划", "展开具体时间表"],
        "milk_flow_decision": _plain_milk_flow_decision(safe.get("milk_flow_decision") if isinstance(safe.get("milk_flow_decision"), dict) else {}),
    }
    for key in ("requires_confirmation", "requires_medical_confirmation", "confirmation_question"):
        if key in safe:
            compact[key] = safe[key]

    if calendar_sync_prompt:
        compact["calendar_sync_prompt"] = calendar_sync_prompt

    if plan_preview:
        compact["plan_preview"] = plan_preview
    return compact


def _milk_plan_facts_for_model(data: dict[str, Any]) -> dict[str, Any]:
    assessment = data.get("assessment") if isinstance(data.get("assessment"), dict) else {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    assessment_compact = _compact_milk_assessment_data(assessment) if assessment else {}
    assessment_facts = _milk_assessment_facts_for_model(assessment, compact_assessment=assessment_compact)
    return _drop_empty(
        {
            "assessment_window": assessment_facts.get("window"),
            "record_counts": assessment_facts.get("record_counts"),
            "raw_records": assessment_facts.get("raw_records"),
            "daily_rollups": assessment_facts.get("daily_rollups"),
            "summaries": assessment_facts.get("summaries"),
            "recent_milk_rhythm": assessment_facts.get("recent_milk_rhythm"),
            "plan_baseline": {
                "current_daily_ml": draft.get("current_daily_ml"),
                "target_daily_ml": draft.get("target_daily_ml"),
                "current_frequency": draft.get("current_frequency"),
                "milk_status": draft.get("milk_status"),
                "schedule_basis": draft.get("schedule_basis"),
                "observation_context": draft.get("observation_context"),
            },
        }
    )


def _milk_plan_interpretation_for_model(data: dict[str, Any]) -> dict[str, Any]:
    assessment = data.get("assessment") if isinstance(data.get("assessment"), dict) else {}
    assessment_compact = _compact_milk_assessment_data(assessment) if assessment else {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    validation = data.get("validation") if isinstance(data.get("validation"), dict) else {}
    eligibility = data.get("eligibility") if isinstance(data.get("eligibility"), dict) else {}
    plan_type = str(draft.get("plan_type") or "").strip()
    return _drop_empty(
        {
            "assessment": _milk_assessment_interpretation_for_model(
                assessment,
                compact_assessment=assessment_compact,
                status=str(assessment.get("assessment_status") or ""),
            ),
            "plan_type": _plain_plan_type(plan_type),
            "plan_summary": draft.get("summary"),
            "validation": {
                key: validation[key]
                for key in ("valid", "status", "summary", "warnings")
                if key in validation
            },
            "eligibility": eligibility,
            "control_strategy": draft.get("control_strategy"),
            "rule_notes": draft.get("rule_notes"),
            "watch_items": draft.get("watch_items"),
        }
    )


def _compact_milk_plan_mutate_output(safe: dict[str, Any]) -> dict[str, Any]:
    status = str(safe.get("status") or "").strip()
    result_ok = safe.get("result_ok")
    if result_ok is True and status == "plan_applied":
        return _compact_milk_plan_saved_output(safe)
    if result_ok is True and status in {"milk_plan_updated", "milk_plan_deleted", "milk_plan_already_absent"}:
        return _compact_milk_plan_completed_output(safe)
    return _compact_milk_plan_not_saved_output(safe)


def _compact_milk_plan_saved_output(safe: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "ok": safe.get("result_ok", safe.get("ok")),
        "tool_name": safe.get("tool_name"),
        "card": {
            "card_type": "milk_plan_card",
            "schema_version": "1.0",
            "created": True,
        },
        "user_context": {
            "结果": "奶量计划已经同步到计划页。",
            "接下来": "后续会按计划提醒；用户可以去计划页查看未来几天的安排。如果用户近期有会议、外出、上班、睡眠安排，或者其他不方便吸奶的时间，可以继续帮用户调整计划时间。",
        },
        "next_actions": ["调整近期日程", "查看计划页", "先这样执行"],
    }
    return compact


def _compact_milk_plan_completed_output(safe: dict[str, Any]) -> dict[str, Any]:
    status = str(safe.get("status") or "").strip()
    if status == "milk_plan_deleted":
        result_text = "奶量计划已经删除。"
        next_text = "对应的计划任务也已按本次操作处理。"
    elif status == "milk_plan_already_absent":
        result_text = "这份奶量计划已经不存在。"
        next_text = "不需要重复删除。"
    else:
        result_text = "奶量计划已经更新。"
        next_text = "用户可以继续查看计划页，或继续调整后续日程。"
    return {
        "ok": safe.get("result_ok", safe.get("ok")),
        "tool_name": safe.get("tool_name"),
        "status": status,
        "user_context": {
            "结果": result_text,
            "接下来": next_text,
        },
    }


def _compact_milk_plan_not_saved_output(safe: dict[str, Any]) -> dict[str, Any]:
    status = str(safe.get("status") or "").strip()
    summary = str(safe.get("summary") or "").strip()
    calendar_delta = safe.get("calendar_delta") if isinstance(safe.get("calendar_delta"), dict) else {}
    if status == "calendar_write_strategy_required":
        strategy_options = calendar_delta.get("strategy_options") if isinstance(calendar_delta.get("strategy_options"), dict) else {}
        option_labels = [
            str(option.get("label") or key)
            for key, option in strategy_options.items()
            if isinstance(option, dict)
        ]
        user_context = {
            "当前状态": "这版奶量计划还没有保存。",
            "原因": "明天起已经有未来奶量计划任务，需要先选择写入方式。",
            "可选方式": option_labels or ["追加到现有日程", "替换未来未完成计划任务"],
            "现有未来计划任务数": calendar_delta.get("existing_future_plan_task_count"),
        }
    else:
        user_context = {
            "当前状态": "这次奶量计划没有保存成功。",
            "原因": summary or "计划保存前还有信息或校验问题需要处理。",
        }
    return _drop_empty(
        {
            "ok": safe.get("result_ok", False),
            "tool_name": safe.get("tool_name"),
            "status": status,
            "calendar_delta": calendar_delta,
            "allowed_strategies": safe.get("allowed_strategies"),
            "user_context": user_context,
        }
    )


def _compact_milk_write_confirmation_output(safe: dict[str, Any]) -> dict[str, Any]:
    question = str(safe.get("confirmation_question") or safe.get("summary") or "确认执行这次保存吗？").strip()
    return {
        "ok": safe.get("result_ok", safe.get("ok")),
        "tool_name": safe.get("tool_name"),
        "status": safe.get("status"),
        "requires_confirmation": True,
        "confirmation_question": question,
    }


def _compact_milk_plan_missing_context_output(safe: dict[str, Any]) -> dict[str, Any]:
    missing_context = _missing_milk_context_for_model(
        safe.get("missing_fields"),
        suggested_questions=safe.get("suggested_questions"),
        current_field=safe.get("current_field"),
        next_question=safe.get("next_question"),
        remaining_count=safe.get("remaining_count"),
    )
    compact: dict[str, Any] = {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
        "milk_flow_decision": _plain_milk_flow_decision(safe.get("milk_flow_decision") if isinstance(safe.get("milk_flow_decision"), dict) else {}),
        "user_context": {
            "当前进度": "正在制定奶量计划，但还需要先补齐会影响计划安全和方向的信息。",
            "需要继续确认": missing_context,
            "补齐后": "再继续生成奶量计划。",
        },
    }
    current_question = str(missing_context.get("current_question") or "").strip()
    if current_question:
        compact["final_response_instruction"] = _milk_single_question_final_response_instruction(
            current_question,
            field_id=missing_context.get("current_field"),
            remaining_count=missing_context.get("remaining_count"),
        )
    return _drop_empty(compact)


def _compact_milk_plan_no_card_output(safe: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    clinical = safe.get("clinical_assessment") if isinstance(safe.get("clinical_assessment"), dict) else {}
    clinical_context = clinical.get("user_context") if isinstance(clinical.get("user_context"), dict) else {}
    summary = str(safe.get("summary") or "").strip()
    data = _tool_result_data(result or {}) if isinstance(result, dict) else {}
    missing_context = _missing_milk_context_for_model(
        safe.get("missing_fields"),
        suggested_questions=safe.get("suggested_questions"),
        current_field=safe.get("current_field"),
        next_question=safe.get("next_question"),
        remaining_count=safe.get("remaining_count"),
    )
    user_context: dict[str, Any] = {
        "当前结果": _plain_milk_plan_no_card_result(safe, clinical_context=clinical_context, summary=summary),
        "宝宝和妈妈情况": clinical_context,
        "下一步": _plain_milk_plan_no_card_next_step(safe, clinical_context=clinical_context),
    }
    compact = {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
        "facts": _milk_plan_facts_for_model(data),
        "interpretation": _milk_plan_interpretation_for_model(data),
        "workflow": {
            "status": safe.get("status"),
            "requires_confirmation": safe.get("requires_confirmation"),
            "missing_context": missing_context,
            "milk_flow_decision": _plain_milk_flow_decision(safe.get("milk_flow_decision") if isinstance(safe.get("milk_flow_decision"), dict) else {}),
        },
        "milk_flow_decision": _plain_milk_flow_decision(safe.get("milk_flow_decision") if isinstance(safe.get("milk_flow_decision"), dict) else {}),
        "user_context": user_context,
    }
    current_question = str(missing_context.get("current_question") or "").strip()
    if current_question:
        compact["final_response_instruction"] = _milk_single_question_final_response_instruction(
            current_question,
            field_id=missing_context.get("current_field"),
            remaining_count=missing_context.get("remaining_count"),
        )
    return _drop_empty(compact)


def _plain_milk_plan_no_card_result(safe: dict[str, Any], *, clinical_context: dict[str, Any], summary: str) -> str:
    status = str(safe.get("status") or "").strip()
    if status == "milk_plan_clinical_gate_blocked":
        next_step = str(clinical_context.get("下一步") or "").strip()
        return next_step or "当前更适合先确认宝宝或妈妈状态，再继续安排奶量计划。"
    if status == "milk_plan_preview_missing_plan_type":
        return "还需要先确认计划方向。"
    if status == "milk_plan_target_invalid":
        return "计划目标需要再确认一下。"
    if status == "milk_plan_needs_milk_records":
        return "过去 7 天还缺少可用于计算计划目标的有效奶量数据。"
    return "这次还需要补充一点信息，再继续安排奶量计划。"


def _plain_milk_plan_no_card_next_step(safe: dict[str, Any], *, clinical_context: dict[str, Any]) -> str:
    status = str(safe.get("status") or "").strip()
    if status == "milk_plan_clinical_gate_blocked":
        return str(clinical_context.get("下一步") or "先处理会影响计划的宝宝或妈妈状态。")
    if status == "milk_plan_preview_missing_plan_type":
        return "问用户现在更想追奶、稳奶，还是减奶。"
    if status == "milk_plan_target_invalid":
        return "问用户希望调整到什么方向或目标。"
    if status == "milk_plan_needs_milk_records":
        return "请用户补充过去 7 天每天大约吸奶多少 ml；如果有亲喂，只补充可用于估算亲喂转移量的瓶喂或补奶毫升数。不把补充频次作为排程前置条件。"
    return "补齐关键信息后再继续制定计划。"


def _tool_result_data(result: dict[str, Any]) -> dict[str, Any]:
    tool_result = result.get("result")
    if not isinstance(tool_result, dict):
        return {}
    data = tool_result.get("data")
    return data if isinstance(data, dict) else {}


def _milk_plan_calendar_sync_prompt(data: dict[str, Any]) -> str:
    calendar_delta = data.get("calendar_delta") if isinstance(data.get("calendar_delta"), dict) else {}
    date_range = calendar_delta.get("date_range") if isinstance(calendar_delta.get("date_range"), dict) else {}
    start_date = str(date_range.get("start_date") or "").strip()
    end_date = str(date_range.get("end_date") or "").strip()
    task_count = calendar_delta.get("draft_calendar_task_count")
    strategy_required = bool(calendar_delta.get("calendar_write_strategy_required"))
    range_text = f"{start_date} 到 {end_date}" if start_date and end_date else "明天开始的计划周期"

    if strategy_required:
        return f"如果要同步到日历，我会先让你选择追加还是替换未来未完成计划，再写入 {range_text} 的提醒。"
    if task_count is not None:
        return f"如果方向没问题，我可以把这版计划同步到 {range_text} 的日历提醒；也可以先帮你调整。"
    return f"如果方向没问题，我可以把这版计划同步到 {range_text} 的日历提醒。"


def _compact_card_tool_output(safe: dict[str, Any], final_response_instruction: str) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
        "final_response_instruction": final_response_instruction,
    }
    for key in (
        "id",
        "skill_id",
        "status",
        "resource_id",
        "side_effect_performed",
        "summary",
        "requires_confirmation",
        "requires_medical_confirmation",
        "confirmation_question",
        "error",
    ):
        if key in safe:
            compact[key] = safe[key]

    card = safe.get("card")
    if isinstance(card, dict):
        card_json = card.get("card_json")
        card_json_dict = card_json if isinstance(card_json, dict) else {}
        compact["card"] = {
            "card_type": card.get("card_type") or card_json_dict.get("card_type"),
            "schema_version": card.get("schema_version") or card_json_dict.get("schema_version"),
            "created": True,
        }
    return compact


def _compact_milk_plan_preview_for_model(result: dict[str, Any]) -> dict[str, Any]:
    """Keep save-critical plan payload available without asking the model to narrate it."""

    tool_result = result.get("result")
    if not isinstance(tool_result, dict):
        return {}
    data = tool_result.get("data")
    if not isinstance(data, dict):
        return {}

    compact: dict[str, Any] = {
        "usage": "仅供用户确认保存计划时调用 milk_plan_mutate；最终回复不要展开这里的字段。",
    }
    for key in ("requires_confirmation", "requires_medical_confirmation", "confirmation_question"):
        if key in data:
            compact[key] = data[key]

    validation = data.get("validation")
    if isinstance(validation, dict):
        compact["validation"] = {
            key: validation[key]
            for key in ("valid", "status", "summary")
            if key in validation
        }

    calendar_delta = data.get("calendar_delta")
    if isinstance(calendar_delta, dict):
        compact["calendar_delta"] = {
            key: calendar_delta[key]
            for key in (
                "date_range",
                "draft_calendar_task_count",
                "existing_future_plan_task_count",
                "calendar_write_strategy_required",
                "recommended_calendar_write_strategy",
            )
            if key in calendar_delta
        }

    draft = data.get("draft")
    if isinstance(draft, dict):
        compact["plan_context"] = _plain_milk_plan_context(draft)
        compact["confirmed_plan_for_save"] = draft

    return compact


def _plain_milk_plan_context(draft: dict[str, Any]) -> dict[str, Any]:
    rules = draft.get("plan_rules") if isinstance(draft.get("plan_rules"), dict) else {}
    plan_type = str(draft.get("plan_type") or "").strip()
    plan_context = _plain_plan_context_by_type(plan_type, draft=draft)
    return _drop_empty(
        {
            "计划类型": _plain_plan_type(plan_type),
            **plan_context,
            **_plain_schedule_basis_context(draft),
            "计划天数": f"{draft.get('plan_days')} 天" if draft.get("plan_days") else "",
            "预计每天提醒次数": f"{rules.get('desired_pumping_count')} 次" if rules.get("desired_pumping_count") else "",
            "下一步": "询问用户是否同步到计划页，或是否要先调整时间。",
        }
    )


def _plain_schedule_basis_context(draft: dict[str, Any]) -> dict[str, Any]:
    basis = draft.get("schedule_basis") if isinstance(draft.get("schedule_basis"), dict) else {}
    generation_context = draft.get("generation_context") if isinstance(draft.get("generation_context"), dict) else {}
    if not basis:
        basis = generation_context.get("schedule_basis") if isinstance(generation_context.get("schedule_basis"), dict) else {}
    if not basis:
        return {}
    rhythm = generation_context.get("recent_milk_rhythm") if isinstance(generation_context.get("recent_milk_rhythm"), dict) else {}

    facts = []
    summary = str(basis.get("basis_summary") or "").strip()
    if summary:
        facts.append(summary)
    source = str(basis.get("source") or "").strip()
    confidence = str(basis.get("confidence") or "").strip()
    source_label = {
        "recent_calendar_schedule": "最近计划提醒",
        "recent_records": "最近实际记录",
        "recent_pumping_records": "最近吸奶记录",
        "recent_breastfeeding_records": "最近亲喂记录",
        "default_template": "通用节奏",
    }.get(source, "")
    confidence_label = {"high": "比较可靠", "medium": "可参考", "low": "不足"}.get(confidence, "")
    if source_label and confidence_label:
        facts.append(f"排程参考的是{source_label}，信息{confidence_label}。")
    if basis.get("ask_daily_counts") is False:
        facts.append("已经有可用记录，不需要再向用户确认已知节奏信息。")
    else:
        facts.append("记录不足时，计划会使用通用节奏或已读记录生成草稿，不把补充节奏作为排程前置条件。")
    longest_gap = basis.get("longest_gap_hours")
    if longest_gap is not None:
        facts.append(f"最近参考节奏里最长间隔约 {longest_gap} 小时。")
    return _drop_empty(
        {
            "计划依据": facts,
            "最近7天吸奶和亲喂节奏": _plain_recent_milk_rhythm_context(rhythm),
        }
    )


def _plain_plan_context_by_type(plan_type: str, *, draft: dict[str, Any]) -> dict[str, Any]:
    if plan_type == "increase_milk":
        return {
            "为什么建议这个方向": [
                "近期多数天低于参考区间。",
                "宝宝和妈妈目前没有提示需要先暂停计划的信号。",
                "这版计划先做小幅调整，不一下子改太多。",
            ],
            "时间安排逻辑": [
                "尽量减少太长的间隔。",
                "必要时增加一次更容易坚持的吸奶或亲喂提醒。",
            ],
            "每次吸奶或亲喂": [
                "每次不需要无限延长。",
                "吸奶到奶流明显变慢后，再多 1-2 分钟即可。",
                "疼或不舒服时先停，再调整吸力、法兰或姿势。",
            ],
            "复盘方式": [
                "连续执行 2-3 天后，看平均奶量、宝宝尿布/精神和妈妈舒适度。",
            ],
            "需要先停下来的情况": [
                "如果发热、寒战、乳房红肿热痛扩大、疼痛明显加重，或宝宝尿布/精神/体重让人担心，先暂停调整并联系医生或 IBCLC。",
            ],
        }
    if plan_type == "decrease_milk":
        return {
            "为什么建议这个方向": [
                "当前更适合逐步减少过强刺激。",
                "调整幅度要小，避免突然变化带来胀痛或堵奶。",
            ],
            "时间安排逻辑": [
                "先减少不必要的额外吸奶。",
                "容易胀或堵时，优先少量减少单次时长或单次量，再考虑拉长间隔。",
            ],
            "每次吸奶或亲喂": [
                "只吸到舒服即可，不追求特别空。",
                "如果只是胀，少量吸出到不难受就可以停。",
            ],
            "复盘方式": [
                "每 2-3 天看胀痛、硬块、总量和宝宝状态，再决定下一步。",
            ],
            "需要先停下来的情况": [
                "如果胀痛、硬块、发热或明显不适加重，先暂停减量并联系医生或 IBCLC。",
            ],
        }
    return {
        "为什么建议这个方向": [
            "当前更适合先保持稳定节奏。",
            "先观察几天平均变化，比只看单次波动更可靠。",
        ],
        "时间安排逻辑": [
            "尽量沿用已经能坚持的时间。",
            "不要因为一两次波动大改安排。",
        ],
        "每次吸奶或亲喂": [
            "保持舒服、稳定即可。",
            "吸奶到奶流明显变慢、乳房舒服一些就可以。",
        ],
        "复盘方式": [
            "第 3 天和第 7 天复盘奶量、宝宝表现和妈妈舒适度。",
        ],
    }


def _plain_plan_type(plan_type: str) -> str:
    if plan_type == "increase_milk":
        return "温和追奶计划"
    if plan_type == "decrease_milk":
        return "温和减奶计划"
    if plan_type == "maintain_milk":
        return "稳奶计划"
    return "奶量计划"


def artifact_events_from_tool_result(
    *,
    tool_call_id: str,
    tool_call_name: str,
    safe_result: dict[str, Any],
) -> list[AgUiEvent]:
    events: list[AgUiEvent] = []
    artifact_specs: list[tuple[str, str, dict[str, Any], str]] = []
    if isinstance(safe_result.get("form"), dict):
        artifact_specs.append(("form", str(safe_result["form"].get("id") or f"{tool_call_id}:form"), safe_result["form"], "ready"))
    if isinstance(safe_result.get("card"), dict):
        card = safe_result["card"]
        artifact_type = str(card.get("card_type") or "card")
        if not (tool_call_name == "milk_plan_mutate" and artifact_type == "milk_plan_card"):
            artifact_specs.append((artifact_type, str(card.get("id") or f"{tool_call_id}:card"), card, "ready"))
    if isinstance(safe_result.get("ticket"), dict):
        ticket = safe_result["ticket"]
        artifact_specs.append(("support_ticket", str(ticket.get("draft_id") or f"{tool_call_id}:ticket"), ticket, "preview"))

    for artifact_type, artifact_id, artifact, status in artifact_specs:
        event = artifact_created_event(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            tool_call_id=tool_call_id,
            tool_call_name=tool_call_name,
            artifact=artifact,
            status=status,
        )
        if artifact_type == "support_ticket" and "submit_label" in safe_result:
            event["submit_label"] = safe_result["submit_label"]
        events.append(event)
    return events


def confirmation_event_from_tool_result(
    *,
    tool_call_id: str,
    tool_call_name: str,
    safe_result: dict[str, Any],
) -> AgUiEvent | None:
    if safe_result.get("ok") is False:
        return None
    artifact_id = _primary_artifact_id(tool_call_id, safe_result)
    if tool_call_name == "support_ticket_draft_create" and isinstance(safe_result.get("ticket"), dict):
        return confirmation_required_event(
            confirmation_id=f"{tool_call_id}:confirm",
            tool_call_id=tool_call_id,
            tool_call_name=tool_call_name,
            artifact_id=artifact_id,
            title="我需要你确认售后信息",
            message="请核对信息是否准确，有需要可以直接修改。",
        )
    if safe_result.get("requires_confirmation") is True:
        title = _confirmation_title(tool_call_name, safe_result)
        message = str(safe_result.get("confirmation_question") or safe_result.get("summary") or "").strip()
        return confirmation_required_event(
            confirmation_id=f"{tool_call_id}:confirm",
            tool_call_id=tool_call_id,
            tool_call_name=tool_call_name,
            artifact_id=artifact_id,
            title=title,
            message=message,
        )
    return None


def _primary_artifact_id(tool_call_id: str, safe_result: dict[str, Any]) -> str | None:
    if isinstance(safe_result.get("form"), dict):
        return str(safe_result["form"].get("id") or f"{tool_call_id}:form")
    if isinstance(safe_result.get("card"), dict):
        return str(safe_result["card"].get("id") or f"{tool_call_id}:card")
    if isinstance(safe_result.get("ticket"), dict):
        return str(safe_result["ticket"].get("draft_id") or f"{tool_call_id}:ticket")
    return None


def _confirmation_title(tool_name: str, safe_result: dict[str, Any]) -> str:
    if tool_name == "milk_plan_preview_create":
        if safe_result.get("requires_medical_confirmation"):
            return "我需要先确认健康边界"
        return "我需要你确认奶量计划"
    if tool_name == "milk_calendar_change_preview":
        return "我需要你确认日程调整"
    return "我需要你确认后，再继续处理"


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def build_agent_request(inputs: RuntimeInputs, options: BuildAgentRequestOptions | None = None) -> ResponsesRequest:
    options = options or {}
    request_context = _build_request_context_for_request(inputs, options)
    input_items = [_user_input_item(request_context, inputs["user_message"], inputs.get("images", []))]
    prior_tool_image_input = _prior_tool_image_input_item(inputs, options)
    if prior_tool_image_input is not None:
        input_items.append(prior_tool_image_input)
    return _build_response_request(inputs, options, input_items)


def _build_response_request(
    inputs: RuntimeInputs,
    options: BuildAgentRequestOptions,
    input_items: list[dict[str, Any]],
) -> ResponsesRequest:
    request: ResponsesRequest = {
        "model": options.get("model", "gpt-5.5"),
        "instructions": STATIC_AGENT_INSTRUCTIONS,
        "input": input_items,
        "reasoning": {"effort": "low"},
        "text": {
            "format": {"type": "text"},
            "verbosity": "low",
        },
        "store": options.get("store", True),
        "prompt_cache_key": options.get("prompt_cache_key", "momcozy-agent-v2"),
    }
    if options.get("enable_tools", True):
        tools = select_runtime_tools(inputs)
        disabled_tool_names = _disabled_tool_names_from_options(options)
        if disabled_tool_names:
            tools = _remove_function_tools(tools, disabled_tool_names)
        required_milk_tool = _forced_required_tool_from_options(options) or (
            _required_milk_management_tool(inputs, options) if _is_initial_user_request(input_items) else None
        )
        if required_milk_tool:
            tools = _promote_deferred_function_tool(tools, required_milk_tool)
        request["tools"] = tools
        loaded_skill_ids = options.get("loaded_skill_ids")
        if not isinstance(loaded_skill_ids, list):
            loaded_skill_ids = None
        tool_choice = health_guidance_required_web_search_tool_choice(inputs, loaded_skill_ids) or "auto"
        request["tool_choice"] = _tool_choice_with_milk_plan_contract(required_milk_tool, tools, tool_choice)
        request["include"] = ["web_search_call.action.sources"]

    max_output_tokens = options.get("max_output_tokens")
    if isinstance(max_output_tokens, int) and max_output_tokens > 0:
        request["max_output_tokens"] = max_output_tokens

    previous_response_id = inputs.get("previous_response_id")
    if previous_response_id:
        request["previous_response_id"] = previous_response_id

    return request


def _is_initial_user_request(input_items: list[dict[str, Any]]) -> bool:
    return any(item.get("role") == "user" for item in input_items)


def _forced_required_tool_from_options(options: BuildAgentRequestOptions) -> str | None:
    tool_name = str(options.get("_required_tool_name") or "").strip()
    return tool_name or None


def _should_disable_tools_after_tool_results(results: list[dict[str, Any]]) -> bool:
    return any(_tool_result_disables_followup_tools(result) for result in results)


def _required_milk_tool_after_tool_results(results: list[dict[str, Any]], inputs: RuntimeInputs) -> str | None:
    for result in results:
        tool_name = str(result.get("tool_name") or "")
        tool_result = result.get("result") if isinstance(result.get("result"), dict) else {}
        data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
        status = str(tool_result.get("status") or "").strip()
        if tool_name != "milk_analysis_intake_manage" and str(data.get("next_tool") or "").strip() == "milk_analysis_intake_manage":
            return "milk_analysis_intake_manage"
        if tool_name == "milk_analysis_intake_manage" and status == "milk_analysis_ready_to_evaluate":
            if isinstance(data.get("analysis_context"), dict):
                return "milk_analysis_evaluate"
        if tool_name == "milk_plan_preview_create" and status == "milk_plan_preview_needs_analysis_evaluation":
            if str(data.get("next_tool") or "").strip() == "milk_analysis_evaluate":
                return "milk_analysis_evaluate"
        if tool_name == "milk_analysis_evaluate" and str(data.get("next_tool") or "").strip() == "milk_plan_preview_create":
            if _user_message_accepts_milk_plan_preview(inputs.get("user_message")):
                return "milk_plan_preview_create"
    return None


def _tool_calls_with_quick_replies_last(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        tool_calls,
        key=lambda tool_call: 1 if str(tool_call.get("name") or "") == QUICK_REPLIES_TOOL_NAME else 0,
    )


def _tool_result_disables_followup_tools(result: dict[str, Any]) -> bool:
    tool_name = str(result.get("tool_name") or "")
    tool_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    status = str(tool_result.get("status") or "").strip()
    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    if tool_name == "birth_journey_intake_manage":
        return status == "blocked_by_symptoms" or isinstance(tool_result.get("auto_tool_result"), dict)
    if tool_name == "birth_journey_plan_card_create" and isinstance(tool_result.get("card"), dict):
        return True
    if tool_name.startswith("milk_"):
        missing = data.get("missing_fields") if isinstance(data.get("missing_fields"), list) else []
        if missing and str(data.get("next_tool") or "").strip() == "milk_analysis_intake_manage":
            return True
    if tool_name == "milk_analysis_intake_manage":
        if status in {"milk_analysis_intake_collecting", "milk_analysis_intake_needs_records"}:
            return True
    if tool_name == "milk_plan_preview_create":
        if status == "milk_plan_preview_needs_analysis_evaluation":
            return str(data.get("next_tool") or "").strip() != "milk_analysis_evaluate"
        if isinstance(tool_result.get("card"), dict):
            return True
        return status in {
            "plan_preview_ready",
            "plan_preview_needs_revision",
            "plan_preview_not_recommended",
            "milk_plan_preview_missing_plan_type",
            "milk_plan_target_invalid",
            "milk_plan_needs_milk_records",
            "milk_plan_clinical_gate_blocked",
            "milk_plan_needs_clinical_context",
        }
    if tool_name in MILK_WRITE_TOOL_NAMES:
        return True
    return False


def _disabled_tool_names_from_options(options: BuildAgentRequestOptions) -> set[str]:
    value = options.get("_disabled_tool_names")  # type: ignore[typeddict-item]
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item or "").strip()}


def _remove_function_tools(tools: list[dict[str, Any]], disabled_names: set[str]) -> list[dict[str, Any]]:
    if not disabled_names:
        return tools
    next_tools: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") == "function" and str(tool.get("name") or "") in disabled_names:
            continue
        if tool.get("type") == "namespace" and isinstance(tool.get("tools"), list):
            copied = dict(tool)
            copied["tools"] = [
                item
                for item in tool["tools"]
                if not (isinstance(item, dict) and item.get("type") == "function" and str(item.get("name") or "") in disabled_names)
            ]
            next_tools.append(copied)
            continue
        next_tools.append(tool)
    return next_tools


def _promote_deferred_function_tool(tools: list[dict[str, Any]], tool_name: str) -> list[dict[str, Any]]:
    promoted = dict(FUNCTION_TOOLS[tool_name])  # type: ignore[index]
    promoted.pop("defer_loading", None)
    next_tools: list[dict[str, Any]] = []
    inserted = False
    for tool in tools:
        if tool.get("type") == "function" and tool.get("name") == tool_name:
            continue
        if not inserted and tool.get("type") == "namespace":
            next_tools.append(promoted)
            inserted = True
        if tool.get("type") == "namespace" and isinstance(tool.get("tools"), list):
            nested = [
                item
                for item in tool["tools"]
                if not (isinstance(item, dict) and item.get("type") == "function" and item.get("name") == tool_name)
            ]
            copied = dict(tool)
            copied["tools"] = nested
            next_tools.append(copied)
            continue
        next_tools.append(tool)
    if not inserted:
        next_tools.append(promoted)
    return next_tools


def _tool_choice_with_milk_plan_contract(
    required_tool: str | None,
    tools: list[dict[str, Any]],
    current_choice: str | dict[str, Any],
) -> str | dict[str, Any]:
    if current_choice != "auto":
        return current_choice
    if required_tool and _tool_available_in_request(tools, required_tool):
        return {
            "type": "allowed_tools",
            "mode": "required",
            "tools": [{"type": "function", "name": required_tool}],
        }
    return current_choice


def _required_milk_management_tool(inputs: RuntimeInputs, options: BuildAgentRequestOptions) -> str | None:
    if _user_message_requests_milk_calendar_plan(inputs.get("user_message")):
        return "milk_calendar_query"
    state = _milk_management_state_from_options(options)
    intake = state.get("analysis_intake") if isinstance(state.get("analysis_intake"), dict) else {}
    if not intake and _user_message_starts_milk_analysis_flow(inputs.get("user_message")):
        return "milk_analysis_intake_manage"
    if not intake and _user_message_should_resume_milk_analysis_intake(inputs, options, state):
        return "milk_analysis_intake_manage"
    if not intake:
        return None
    if _milk_analysis_intake_ready_for_save(intake):
        return "milk_plan_mutate" if _user_message_confirms_milk_plan_save(inputs.get("user_message")) else None
    if _milk_analysis_intake_has_missing_fields(intake):
        return "milk_analysis_intake_manage"
    stage = str(intake.get("stage") or "").strip()
    if stage == "ready_to_evaluate" and not isinstance(intake.get("assessment_result"), dict):
        return "milk_analysis_evaluate"
    if stage == "ready_to_evaluate":
        return "milk_analysis_evaluate"
    if stage == "analysis_ready" and _user_message_accepts_milk_plan_preview(inputs.get("user_message")):
        return "milk_plan_preview_create"
    return None


def _tool_available_in_request(tools: list[dict[str, Any]], tool_name: str) -> bool:
    for tool in tools:
        if tool.get("type") == "function" and tool.get("name") == tool_name:
            return True
        nested = tool.get("tools") if isinstance(tool.get("tools"), list) else []
        if any(isinstance(item, dict) and item.get("type") == "function" and item.get("name") == tool_name for item in nested):
            return True
    return False


def _milk_management_state_from_options(options: BuildAgentRequestOptions) -> dict[str, Any]:
    context_state = options.get("context_state")
    if isinstance(context_state, ContextState) and isinstance(context_state.milk_management_state, dict):
        return context_state.milk_management_state
    return {}


def _user_message_should_resume_milk_analysis_intake(
    inputs: RuntimeInputs,
    options: BuildAgentRequestOptions,
    state: dict[str, Any],
) -> bool:
    message = inputs.get("user_message")
    if not _user_message_looks_like_milk_analysis_followup_answer(message):
        return False
    if isinstance(state.get("last_fact_read"), dict):
        return True
    if not _user_message_answers_milk_record_completeness(message):
        return False
    loaded_skill_ids = options.get("loaded_skill_ids")
    if isinstance(loaded_skill_ids, list) and "milk-management" in {str(item) for item in loaded_skill_ids}:
        return True
    context_state = options.get("context_state")
    domain = active_service_domain(inputs, context_state if isinstance(context_state, ContextState) else None)
    return domain == "milk_management"


def _user_message_looks_like_milk_analysis_followup_answer(message: Any) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    if _user_message_answers_milk_record_completeness(text):
        return True
    context_terms = (
        "尿布",
        "尿量",
        "精神",
        "体重",
        "吃奶",
        "安稳",
        "发热",
        "寒战",
        "红肿",
        "硬块",
        "疼",
        "痛",
        "乳房",
        "胀",
        "涨",
        "排不空",
        "舒服",
    )
    answer_terms = ("正常", "还好", "没有", "没", "无", "可以", "稳定", "不")
    return any(term in text for term in context_terms) and any(term in text for term in answer_terms)


def _user_message_answers_milk_record_completeness(message: Any) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "没有漏",
            "没漏",
            "无漏",
            "不漏",
            "漏记",
            "记录完整",
            "记录是完整",
            "完整的",
            "都记",
            "都有记",
            "全记",
            "没少记",
            "没有少记",
            "补记录",
            "补充记录",
        )
    )


def _milk_analysis_intake_ready_for_save(intake: dict[str, Any]) -> bool:
    preview = intake.get("plan_preview") if isinstance(intake.get("plan_preview"), dict) else {}
    return str(preview.get("status") or "").strip() == "plan_preview_ready"


def _milk_analysis_intake_has_missing_fields(intake: dict[str, Any]) -> bool:
    checklist = intake.get("checklist") if isinstance(intake.get("checklist"), list) else []
    if any(isinstance(item, dict) and item.get("status") != "collected" for item in checklist):
        return True
    return str(intake.get("stage") or "").strip() == "intake_collecting"


def _user_message_requests_milk_calendar_plan(message: Any) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    normalized = text.lower()
    creation_terms = ("制定", "生成", "创建", "新建", "做一个", "做一版", "帮我做", "帮我安排")
    explicit_query_terms = ("当前", "现在", "正在", "采用", "执行", "已有", "原计划", "查看", "查", "看看", "什么", "哪个", "安排", "日程")
    date_terms = ("今天", "明天", "后天", "本周", "这周", "下周", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "星期", "接下来", "未来")
    if any(term in text for term in creation_terms) and not any(term in text for term in (*explicit_query_terms, *date_terms)):
        return False
    if any(term in text for term in creation_terms) and "计划" in text and not any(term in text for term in explicit_query_terms):
        return False

    milk_terms = ("奶量", "吸奶", "亲喂", "喂奶", "追奶", "稳奶", "减奶", "泌乳")
    plan_terms = ("计划", "安排", "日程", "任务", "提醒", "几点", "几次", "怎么吸", "怎么喂")
    current_terms = ("当前", "现在", "正在", "采用", "执行", "按哪个", "哪个计划", "什么计划")
    english_terms = ("milk plan", "pumping plan", "feeding plan")
    has_milk_domain = any(term in text for term in milk_terms) or any(term in normalized for term in english_terms)
    has_plan_intent = any(term in text for term in plan_terms)
    has_current_or_date = any(term in text for term in (*current_terms, *date_terms))
    if has_milk_domain and has_plan_intent and (has_current_or_date or any(term in text for term in ("查看", "查", "看看"))):
        return True
    if "计划" in text and has_current_or_date and any(term in text for term in current_terms):
        return True
    return False


def _user_message_starts_milk_analysis_flow(message: Any) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    normalized = text.lower()
    if any(term in text for term in ("追奶", "稳奶", "减奶")):
        return True
    milk_terms = ("奶量", "吸奶", "亲喂", "喂奶", "泌乳")
    english_terms = ("milk", "pumping", "breastfeeding", "lactation")
    has_milk_domain = any(term in text for term in milk_terms) or any(term in normalized for term in english_terms)
    if not has_milk_domain:
        return False
    analysis_terms = (
        "分析",
        "够不够",
        "是否够",
        "正常吗",
        "是否正常",
        "趋势",
        "偏低",
        "偏高",
        "下降",
        "下滑",
        "怎么调",
        "怎么调整",
        "调整",
        "制定",
        "生成",
        "做计划",
        "做一版",
        "计划",
        "增加",
        "减少",
    )
    return any(term in text for term in analysis_terms)


def _user_message_accepts_milk_plan_preview(message: Any) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    negative = ("不要", "不用", "先不", "暂不", "不做", "不生成", "不继续", "算了")
    if any(token in text for token in negative):
        return False
    if _workflow_text_accepts_plan_for_request(text):
        return True
    return any(token in text for token in ("每天多", "每天少", "做到", "目标", "追奶计划", "稳奶计划", "减奶计划", "生成计划", "制定计划", "做计划"))


def _workflow_text_accepts_plan_for_request(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized in {"好", "好的", "可以", "行", "继续", "确认", "ok", "okay", "yes"}:
        return True
    return any(token in normalized for token in ("生成计划", "制定计划", "做计划", "按这个", "先按", "milk plan"))


def _user_message_confirms_milk_plan_save(message: Any) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    negative = ("不要", "不用", "先不", "暂不", "取消", "不保存", "不同步", "再改", "调整")
    if any(token in text for token in negative):
        return False
    return any(token in text for token in ("保存", "同步", "确认", "按这版", "就这样", "执行", "写入", "好的", "可以"))


def run_agent_turn(
    client: ResponsesClientLike,
    inputs: RuntimeInputs,
    options: BuildAgentRequestOptions | None = None,
) -> object:
    request = build_agent_request(inputs, options)
    return client.responses.create(**request)


def run_agent_loop(
    client: ResponsesClientLike,
    inputs: RuntimeInputs,
    options: BuildAgentRequestOptions | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    on_event: AgentEventHandler | None = None,
    on_ag_ui_event: AgUiEventHandler | None = None,
    ag_ui_thread_id: str | None = None,
    ag_ui_run_id: str | None = None,
    ag_ui_parent_run_id: str | None = None,
    ag_ui_message_id: str | None = None,
    on_text_delta: TextDeltaHandler | None = None,
    on_response_stream_event: Any | None = None,
) -> object:
    options = dict(options or {})
    loaded_skill_ids = list(options.get("loaded_skill_ids", []))
    ag_ui_thread_id = ag_ui_thread_id or default_ag_ui_thread_id(inputs)
    ag_ui_run_id = ag_ui_run_id or new_ag_ui_run_id()
    ag_ui_message_id = ag_ui_message_id or ag_ui_run_id
    ag_ui_status_message_id = f"{ag_ui_run_id}:status"
    ag_ui_tool_result_message_id = f"{ag_ui_run_id}:tool-results"
    streamed_tool_call_keys: set[str] = set()
    streamed_web_search_citations: list[dict[str, Any]] = []

    def emit_streamed_tool_start(tool_call: dict[str, Any]) -> None:
        if _tool_call_was_seen(streamed_tool_call_keys, tool_call):
            return
        _remember_tool_call(streamed_tool_call_keys, tool_call)
        _emit_ag_ui_event(
            on_ag_ui_event,
            tool_call_start_event(
                tool_call["call_id"],
                tool_call["name"],
                ag_ui_tool_result_message_id,
                response_id=tool_call.get("response_id"),
                output_index=tool_call.get("output_index"),
                item_id=tool_call.get("item_id"),
                arguments=tool_call.get("arguments"),
            ),
        )

    def remember_streamed_web_search_citations(citations: list[dict[str, Any]]) -> None:
        nonlocal streamed_web_search_citations
        streamed_web_search_citations = _merge_web_search_citations(streamed_web_search_citations, citations)

    _emit_ag_ui_event(on_ag_ui_event, run_started_event(ag_ui_thread_id, ag_ui_run_id, ag_ui_parent_run_id))
    _emit_event(on_event, "started", "Agent loop started.", {"max_tool_rounds": max_tool_rounds}, on_ag_ui_event, ag_ui_status_message_id)
    _emit_event(on_event, "requesting_model", "Requesting model response.", {"round": 0}, on_ag_ui_event, ag_ui_status_message_id)
    try:
        request = build_agent_request(inputs, options)
        response = _create_response(
            client,
            request,
            on_text_delta,
            lambda status, metadata: _emit_thinking(on_ag_ui_event, status, metadata),
            emit_streamed_tool_start,
            lambda status, metadata: _emit_web_search_status(on_ag_ui_event, status, metadata),
            remember_streamed_web_search_citations,
            on_response_stream_event,
        )
    except Exception as exc:
        _emit_event(on_event, "failed", "Model request failed.", _error_metadata(exc), on_ag_ui_event, ag_ui_status_message_id)
        _emit_ag_ui_event(on_ag_ui_event, run_error_event(str(exc), type(exc).__name__, thread_id=ag_ui_thread_id, run_id=ag_ui_run_id))
        raise

    for round_index in range(max_tool_rounds):
        tool_calls = _extract_function_calls(response)
        if not tool_calls:
            _record_displayed_tool_images(options.get("context_state"), response)
            citations = _merge_web_search_citations(_web_search_citations_from_response(response), streamed_web_search_citations)
            if citations:
                _emit_ag_ui_event(on_ag_ui_event, web_search_citations_event(ag_ui_message_id, citations))
            _emit_event(
                on_event,
                "completed",
                "Agent loop completed.",
                {"round": round_index, "response_id": _get_response_id(response)},
                on_ag_ui_event,
                ag_ui_status_message_id,
            )
            _emit_ag_ui_event(on_ag_ui_event, run_finished_event(ag_ui_thread_id, ag_ui_run_id, {"response_id": _get_response_id(response)}))
            return response

        tool_outputs = []
        executed_tool_results = []
        direct_quick_replies_sent = False
        for tool_call in _tool_calls_with_quick_replies_last(tool_calls):
            tool_name = tool_call["name"]
            if not _tool_call_was_seen(streamed_tool_call_keys, tool_call):
                _remember_tool_call(streamed_tool_call_keys, tool_call)
                _emit_ag_ui_event(
                    on_ag_ui_event,
                    tool_call_start_event(
                        tool_call["call_id"],
                        tool_name,
                        ag_ui_tool_result_message_id,
                        response_id=tool_call.get("response_id"),
                        output_index=tool_call.get("output_index"),
                        item_id=tool_call.get("item_id"),
                        arguments=tool_call.get("arguments"),
                    ),
                )
            _emit_ag_ui_event(
                on_ag_ui_event,
                tool_call_args_event(
                    tool_call["call_id"],
                    tool_name,
                    tool_call["arguments"],
                    response_id=tool_call.get("response_id"),
                    output_index=tool_call.get("output_index"),
                    item_id=tool_call.get("item_id"),
                ),
            )
            _emit_ag_ui_event(
                on_ag_ui_event,
                tool_call_end_event(
                    tool_call["call_id"],
                    tool_name,
                    response_id=tool_call.get("response_id"),
                    output_index=tool_call.get("output_index"),
                    item_id=tool_call.get("item_id"),
                ),
            )
            _emit_event(
                on_event,
                "model_tool_call",
                _tool_call_message(tool_name),
                {"round": round_index, "tool_name": tool_name},
                on_ag_ui_event,
                ag_ui_status_message_id,
            )
            _emit_event(
                on_event,
                _tool_execution_phase(tool_name),
                _tool_execution_message(tool_name),
                {"round": round_index, "tool_name": tool_name},
                on_ag_ui_event,
                ag_ui_status_message_id,
            )
            tool_inputs = _tool_inputs_for_call(inputs, options)
            result = _execute_project_tool(tool_call["name"], tool_call["arguments"], tool_inputs)
            executed_tool_results.append(result)
            _sync_runtime_profile_from_tool_inputs(inputs, tool_inputs)
            if tool_call["name"] == "load_skill" and result.get("ok") and result.get("result", {}).get("id"):
                skill_id = result["result"]["id"]
                if skill_id not in loaded_skill_ids:
                    loaded_skill_ids.append(skill_id)
                domain = _SERVICE_DOMAIN_BY_SKILL_ID.get(str(skill_id))
                if domain and isinstance(options.get("context_state"), ContextState):
                    set_active_service_domain(options["context_state"], domain)
            _record_loaded_reference(options.get("context_state"), tool_call["name"], result)
            _record_loaded_business_tool(options.get("context_state"), tool_call["name"], result)
            _record_tool_images(options.get("context_state"), tool_call["name"], result)
            _record_birth_prep_tool_state(options.get("context_state"), tool_call["name"], tool_call["arguments"], result)
            _update_quick_reply_guidance(options, result)
            _record_milk_tool_state(options.get("context_state"), tool_call["name"], result)
            direct_quick_replies = _birth_journey_intake_direct_quick_replies(result)
            if direct_quick_replies:
                direct_quick_replies_sent = True
                _emit_ag_ui_event(on_ag_ui_event, quick_replies_event(ag_ui_message_id, direct_quick_replies))

            auto_tool_result: dict[str, Any] | None = None
            auto_arguments = _birth_journey_auto_plan_arguments(result)
            if auto_arguments is not None:
                auto_tool_name = "birth_journey_plan_card_create"
                auto_call_id = f"{tool_call['call_id']}:{auto_tool_name}"
                _emit_ag_ui_event(
                    on_ag_ui_event,
                    tool_call_start_event(
                        auto_call_id,
                        auto_tool_name,
                        ag_ui_tool_result_message_id,
                        arguments=auto_arguments,
                    ),
                )
                _emit_ag_ui_event(
                    on_ag_ui_event,
                    tool_call_args_event(auto_call_id, auto_tool_name, auto_arguments),
                )
                _emit_ag_ui_event(
                    on_ag_ui_event,
                    tool_call_end_event(auto_call_id, auto_tool_name),
                )
                _emit_event(
                    on_event,
                    "model_tool_call",
                    _tool_call_message(auto_tool_name),
                    {"round": round_index, "tool_name": auto_tool_name, "auto_chained": True},
                    on_ag_ui_event,
                    ag_ui_status_message_id,
                )
                _emit_event(
                    on_event,
                    _tool_execution_phase(auto_tool_name),
                    _tool_execution_message(auto_tool_name),
                    {"round": round_index, "tool_name": auto_tool_name, "auto_chained": True},
                    on_ag_ui_event,
                    ag_ui_status_message_id,
                )
                auto_tool_result = _execute_project_tool(auto_tool_name, auto_arguments, tool_inputs)
                executed_tool_results.append(auto_tool_result)
                _sync_runtime_profile_from_tool_inputs(inputs, tool_inputs)
                _record_loaded_reference(options.get("context_state"), auto_tool_name, auto_tool_result)
                _record_loaded_business_tool(options.get("context_state"), auto_tool_name, auto_tool_result)
                _record_tool_images(options.get("context_state"), auto_tool_name, auto_tool_result)
                _record_birth_prep_tool_state(options.get("context_state"), auto_tool_name, auto_arguments, auto_tool_result)
                _record_milk_tool_state(options.get("context_state"), auto_tool_name, auto_tool_result)
                auto_safe_result = safe_tool_result(auto_tool_result)
                _emit_ag_ui_event(
                    on_ag_ui_event,
                    tool_call_result_event(
                        ag_ui_tool_result_message_id,
                        auto_call_id,
                        auto_tool_name,
                        auto_tool_result,
                    ),
                )
                for artifact_event in artifact_events_from_tool_result(
                    tool_call_id=auto_call_id,
                    tool_call_name=auto_tool_name,
                    safe_result=auto_safe_result,
                ):
                    _emit_ag_ui_event(on_ag_ui_event, artifact_event)
                auto_confirmation_event = confirmation_event_from_tool_result(
                    tool_call_id=auto_call_id,
                    tool_call_name=auto_tool_name,
                    safe_result=auto_safe_result,
                )
                if auto_confirmation_event is not None:
                    _emit_ag_ui_event(on_ag_ui_event, auto_confirmation_event)
                _emit_event(
                    on_event,
                    "tool_completed",
                    _tool_completed_message(auto_tool_name, bool(auto_tool_result.get("ok"))),
                    _tool_result_metadata(round_index, auto_tool_name, auto_tool_result),
                    on_ag_ui_event,
                    ag_ui_status_message_id,
                )

            safe_result = safe_tool_result(result)
            model_result = _with_auto_birth_journey_plan_result(result, auto_tool_result)
            model_output = model_tool_output(model_result)
            _emit_ag_ui_event(
                on_ag_ui_event,
                tool_call_result_event(
                    ag_ui_tool_result_message_id,
                    tool_call["call_id"],
                    tool_call["name"],
                    result,
                    response_id=tool_call.get("response_id"),
                    output_index=tool_call.get("output_index"),
                    item_id=tool_call.get("item_id"),
                ),
            )
            for artifact_event in artifact_events_from_tool_result(
                tool_call_id=tool_call["call_id"],
                tool_call_name=tool_call["name"],
                safe_result=safe_result,
            ):
                _emit_ag_ui_event(on_ag_ui_event, artifact_event)
            confirmation_event = confirmation_event_from_tool_result(
                tool_call_id=tool_call["call_id"],
                tool_call_name=tool_call["name"],
                safe_result=safe_result,
            )
            if confirmation_event is not None:
                _emit_ag_ui_event(on_ag_ui_event, confirmation_event)
            _emit_event(
                on_event,
                "tool_completed",
                _tool_completed_message(tool_name, bool(result.get("ok"))),
                _tool_result_metadata(round_index, tool_name, result),
                on_ag_ui_event,
                ag_ui_status_message_id,
            )
            tool_outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call["call_id"],
                    "output": json.dumps(model_output, ensure_ascii=False),
                }
            )

        options["loaded_skill_ids"] = loaded_skill_ids
        next_inputs = dict(inputs)
        response_id = _get_response_id(response)
        if response_id:
            next_inputs["previous_response_id"] = response_id

        next_options = dict(options)
        if _should_disable_tools_after_tool_results(executed_tool_results):
            next_options["enable_tools"] = False
        else:
            if direct_quick_replies_sent:
                disabled_tool_names = list(_disabled_tool_names_from_options(next_options))
                if QUICK_REPLIES_TOOL_NAME not in disabled_tool_names:
                    disabled_tool_names.append(QUICK_REPLIES_TOOL_NAME)
                next_options["_disabled_tool_names"] = disabled_tool_names  # type: ignore[typeddict-unknown-key]
            required_next_tool = _required_milk_tool_after_tool_results(executed_tool_results, inputs)
            if required_next_tool:
                next_options["_required_tool_name"] = required_next_tool

        request = _build_response_request(next_inputs, next_options, tool_outputs)
        _emit_event(
            on_event,
            "requesting_model",
            "Requesting model response with tool outputs.",
            {"round": round_index + 1},
            on_ag_ui_event,
            ag_ui_status_message_id,
        )
        try:
            response = _create_response(
                client,
                request,
                on_text_delta,
                lambda status, metadata: _emit_thinking(on_ag_ui_event, status, metadata),
                emit_streamed_tool_start,
                lambda status, metadata: _emit_web_search_status(on_ag_ui_event, status, metadata),
                remember_streamed_web_search_citations,
                on_response_stream_event,
            )
        except Exception as exc:
            _emit_event(on_event, "failed", "Model request failed.", _error_metadata(exc, {"round": round_index + 1}), on_ag_ui_event, ag_ui_status_message_id)
            _emit_ag_ui_event(on_ag_ui_event, run_error_event(str(exc), type(exc).__name__, thread_id=ag_ui_thread_id, run_id=ag_ui_run_id))
            raise

    _emit_event(
        on_event,
        "failed",
        "Agent loop reached the maximum tool rounds.",
        {"max_tool_rounds": max_tool_rounds},
        on_ag_ui_event,
        ag_ui_status_message_id,
    )
    _emit_ag_ui_event(on_ag_ui_event, run_error_event("Agent loop reached the maximum tool rounds.", "MAX_TOOL_ROUNDS", thread_id=ag_ui_thread_id, run_id=ag_ui_run_id))
    return response


def _create_response(
    client: ResponsesClientLike,
    request: ResponsesRequest,
    on_text_delta: TextDeltaHandler | None = None,
    on_reasoning_event: Any | None = None,
    on_function_call_start: Any | None = None,
    on_web_search_event: Any | None = None,
    on_web_search_citations: Any | None = None,
    on_stream_event: Any | None = None,
) -> object:
    if on_text_delta is None:
        return client.responses.create(**request)

    final_response = None
    reasoning_active = False
    output_text_seen = False
    web_search_statuses_seen: set[tuple[str, str]] = set()
    citation_marker_cleaner = _WebSearchCitationMarkerCleaner()
    stream = client.responses.create(**request, stream=True)
    for event in stream:
        event_type = _get_item_value(event, "type")
        _emit_stream_event(on_stream_event, event_type, event)
        stream_citations = _web_search_citations_from_stream_event(event_type, event)
        if stream_citations and on_web_search_citations is not None:
            on_web_search_citations(stream_citations)
        web_search_event = _web_search_status_from_stream_event(event_type, event)
        if web_search_event and on_web_search_event is not None:
            status = str(web_search_event.get("status") or "searching")
            key = str(web_search_event.get("key") or "current")
            dedupe_key = (key, _normalize_web_search_status(status))
            if dedupe_key not in web_search_statuses_seen:
                web_search_statuses_seen.add(dedupe_key)
                on_web_search_event(status, web_search_event.get("metadata") or {})
        if _is_reasoning_start_event(event_type, event):
            if not reasoning_active:
                reasoning_active = True
                if on_reasoning_event is not None:
                    on_reasoning_event("started", {"source_event": event_type, "after_output_text": output_text_seen})
        if event_type == "response.output_text.delta":
            delta = _get_item_value(event, "delta")
            if isinstance(delta, str) and delta:
                clean_delta = citation_marker_cleaner.feed(delta)
                if clean_delta:
                    output_text_seen = True
                    on_text_delta(clean_delta)
        elif event_type == "response.output_item.added":
            item = _get_item_value(event, "item")
            if _is_function_call_item(item) and on_function_call_start is not None:
                on_function_call_start(_stream_function_call_from_event(event, item))
        elif event_type == "response.function_call_arguments.done":
            item = _get_item_value(event, "item")
            if _is_function_call_item(item) and on_function_call_start is not None:
                on_function_call_start(_stream_function_call_from_event(event, item))
        elif _is_reasoning_done_event(event_type, event):
            if reasoning_active and on_reasoning_event is not None:
                on_reasoning_event("completed", {"source_event": event_type})
            reasoning_active = False
        elif event_type == "response.completed":
            clean_delta = citation_marker_cleaner.flush()
            if clean_delta:
                output_text_seen = True
                on_text_delta(clean_delta)
            if reasoning_active and on_reasoning_event is not None:
                on_reasoning_event("completed", {"source_event": event_type})
            reasoning_active = False
            final_response = _get_item_value(event, "response")
        elif event_type == "response.failed":
            if reasoning_active and on_reasoning_event is not None:
                on_reasoning_event("failed", {"source_event": event_type})
            reasoning_active = False
            response = _get_item_value(event, "response")
            error = _get_item_value(response, "error") if response is not None else None
            message = _get_item_value(error, "message") or "Response stream failed."
            raise RuntimeError(message)

    if final_response is None:
        raise RuntimeError("Response stream ended without a completed response.")
    return final_response


class _WebSearchCitationMarkerCleaner:
    def __init__(self) -> None:
        self._mode = ""
        self._marker = ""

    def feed(self, delta: str) -> str:
        output: list[str] = []
        for char in delta:
            if self._mode == "private":
                self._marker += char
                if char == PRIVATE_USE_CITATION_END:
                    if not _private_use_marker_is_citation(self._marker):
                        output.append(self._marker)
                    self._reset_marker()
                elif len(self._marker) > MAX_INLINE_CITATION_MARKER_CHARS or char in "\n\r":
                    output.append(self._marker)
                    self._reset_marker()
                continue

            if self._mode == "bracket":
                self._marker += char
                if char == "】":
                    if not _bracket_marker_is_citation(self._marker):
                        output.append(self._marker)
                    self._reset_marker()
                elif len(self._marker) > MAX_INLINE_CITATION_MARKER_CHARS or char in "\n\r":
                    output.append(self._marker)
                    self._reset_marker()
                continue

            if char == PRIVATE_USE_CITATION_START:
                self._mode = "private"
                self._marker = char
            elif char == "【":
                self._mode = "bracket"
                self._marker = char
            else:
                output.append(char)
        return clean_web_search_citation_markers("".join(output))

    def flush(self) -> str:
        if not self._marker:
            return ""
        marker = self._marker
        self._reset_marker()
        if _private_use_marker_is_citation(marker) or _bracket_marker_is_citation(marker):
            return ""
        return clean_web_search_citation_markers(marker)

    def _reset_marker(self) -> None:
        self._mode = ""
        self._marker = ""


def clean_web_search_citation_markers(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\ue200cite\ue202[\s\S]{0,240}?\ue201", "", text)
    cleaned = re.sub(r"【[^】\n\r]{0,160}†[^】\n\r]{0,160}】", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([，。！？；：,.!?;:])", r"\1", cleaned)
    return cleaned


def _private_use_marker_is_citation(value: str) -> bool:
    return value.startswith(PRIVATE_USE_CITATION_START) and "cite" in value.lower()


def _bracket_marker_is_citation(value: str) -> bool:
    return value.startswith("【") and value.endswith("】") and "†" in value


def _emit_stream_event(handler: Any | None, event_type: Any, event: object) -> None:
    if handler is None or not isinstance(event_type, str):
        return
    handler(event_type, _safe_stream_event_metadata(event_type, event))


def _safe_stream_event_metadata(event_type: str, event: object) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if event_type == "response.output_text.delta":
        delta = _get_item_value(event, "delta")
        if isinstance(delta, str):
            metadata["delta_len"] = len(delta)
    response = _get_item_value(event, "response")
    response_id = _get_response_id(response)
    if response_id:
        metadata["response_id"] = response_id
    output_index = _get_item_value(event, "output_index")
    if isinstance(output_index, int):
        metadata["output_index"] = output_index
    item = _get_item_value(event, "item")
    item_type = _get_item_value(item, "type")
    if isinstance(item_type, str):
        metadata["item_type"] = item_type
    item_id = _get_item_value(item, "id")
    if isinstance(item_id, str):
        metadata["item_id"] = item_id
    return metadata


def _emit_thinking(on_ag_ui_event: AgUiEventHandler | None, status: str, metadata: dict[str, Any] | None = None) -> None:
    _emit_ag_ui_event(on_ag_ui_event, thinking_custom_event(status, metadata))


def _emit_web_search_status(on_ag_ui_event: AgUiEventHandler | None, status: str, metadata: dict[str, Any] | None = None) -> None:
    _emit_ag_ui_event(on_ag_ui_event, web_search_status_event(status, metadata))


def _is_reasoning_start_event(event_type: Any, event: object) -> bool:
    if not isinstance(event_type, str):
        return False
    if event_type.startswith("response.reasoning") and not event_type.endswith(".done"):
        return True
    if event_type == "response.output_item.added":
        item = _get_item_value(event, "item")
        return _get_item_value(item, "type") == "reasoning"
    return False


def _web_search_status_from_stream_event(event_type: Any, event: object) -> dict[str, Any] | None:
    if not isinstance(event_type, str):
        return None
    item = _get_item_value(event, "item")
    item_type = _get_item_value(item, "type")
    output_index = _get_item_value(event, "output_index")
    item_id = _get_item_value(item, "id") or _get_item_value(event, "item_id")
    key = str(item_id or output_index or "current")
    metadata = _safe_stream_event_metadata(event_type, event)

    if event_type == "response.output_item.added" and item_type == "web_search_call":
        return {"status": "searching", "key": key, "metadata": metadata}
    if event_type == "response.output_item.done" and item_type == "web_search_call":
        status = _get_item_value(item, "status")
        if str(status or "").strip().lower() in {"failed", "error"}:
            return {"status": "failed", "key": key, "metadata": metadata}
        return {"status": "completed", "key": key, "metadata": metadata}
    if event_type.startswith("response.web_search_call."):
        suffix = event_type.rsplit(".", 1)[-1]
        if suffix in {"completed", "done"}:
            return {"status": "completed", "key": key, "metadata": metadata}
        if suffix in {"failed", "error"}:
            return {"status": "failed", "key": key, "metadata": metadata}
        return {"status": "searching", "key": key, "metadata": metadata}
    return None


def _web_search_citations_from_stream_event(event_type: Any, event: object) -> list[dict[str, Any]]:
    if not isinstance(event_type, str):
        return []
    candidates = [
        _get_item_value(event, "item"),
        _get_item_value(event, "web_search_call"),
        event,
    ]
    citations: list[dict[str, Any]] = []
    for candidate in candidates:
        citations = _merge_web_search_citations(citations, _web_search_citations_from_web_search_item(candidate))
        if _get_item_value(candidate, "type") == "message":
            citations = _merge_web_search_citations(citations, _web_search_citations_from_response({"output": [candidate]}))
    return citations


def _is_reasoning_done_event(event_type: Any, event: object) -> bool:
    if not isinstance(event_type, str):
        return False
    if event_type.startswith("response.reasoning") and event_type.endswith(".done"):
        return True
    if event_type == "response.output_item.done":
        item = _get_item_value(event, "item")
        return _get_item_value(item, "type") == "reasoning"
    return False


def _user_input_item(request_context: str, user_message: str, images: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    content = [
        {"type": "input_text", "text": request_context},
        {"type": "input_text", "text": f"user_message:\n{user_message}"},
    ]
    for image in images or []:
        image_url = image.get("image_url")
        if not isinstance(image_url, str) or not image_url:
            continue
        content.append(
            {
                "type": "input_image",
                "image_url": image_url,
                "detail": _image_detail(image.get("detail")),
            }
        )
    return {
        "role": "user",
        "content": content,
    }


def _prior_tool_image_input_item(inputs: RuntimeInputs, options: BuildAgentRequestOptions) -> dict[str, Any] | None:
    context_state = options.get("context_state")
    if not isinstance(context_state, ContextState):
        return None
    if not context_state.available_tool_images and not context_state.last_displayed_tool_image:
        return None
    if inputs.get("images"):
        return None
    user_message = str(inputs.get("user_message") or "")
    if not _user_requests_prior_tool_image(user_message):
        return None
    images = _select_tool_images_for_message(context_state, user_message)
    return _tool_image_input_item_from_metadata(images)


def _tool_image_input_item_from_metadata(images: list[dict[str, str]]) -> dict[str, Any] | None:
    image_parts: list[dict[str, str]] = []
    image_labels: list[str] = []
    image_text_lines: list[str] = []
    total_bytes = 0
    for item in images:
        if len(image_parts) >= MAX_TOOL_IMAGE_INPUTS:
            break
        url = str(item.get("url") or "").strip()
        asset = _local_skill_asset_image(url)
        if asset is None:
            continue
        alt = str(item.get("alt") or item.get("module") or "官方步骤图").strip()
        image_text = str(item.get("image_text") or "").strip()
        if image_text:
            if alt:
                image_labels.append(alt)
            image_text_lines.append(f"- {alt or '官方步骤图'}：{image_text}")
            continue
        path, content_type, byte_count = asset
        if byte_count > MAX_TOOL_IMAGE_BYTES or total_bytes + byte_count > MAX_TOOL_IMAGE_TOTAL_BYTES:
            continue
        data_url = _image_file_data_url(path, content_type)
        if data_url is None:
            continue
        if alt:
            image_labels.append(alt)
        image_parts.append({"type": "input_image", "image_url": data_url, "detail": "auto"})
        total_bytes += byte_count

    if not image_parts and not image_text_lines:
        return None

    label_text = "、".join(image_labels[:MAX_TOOL_IMAGE_INPUTS]) or "官方步骤图"
    image_text = ""
    if image_text_lines:
        image_text = "\n可读文字/编号：\n" + "\n".join(image_text_lines)
    attachment_note = "下面附带官方步骤图。" if image_parts else "本轮只补充结构化图片文字，没有附带原图。"
    return {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": (
                    "系统按需补充：下面是前文 device_manual_search 返回的 Momcozy 官方步骤图，"
                    f"图片标题：{label_text}。这些图片来自本地官方 skill assets，不是用户上传照片；"
                    "可用于读取图中文字、标注和部件位置。若工具结果已经提供结构化字段，优先使用结构化字段。"
                    f"{image_text}\n{attachment_note}"
                ),
            },
            *image_parts,
        ],
    }


def _select_tool_images_for_message(context_state: ContextState, user_message: str) -> list[dict[str, str]]:
    if context_state.last_displayed_tool_image:
        return [dict(context_state.last_displayed_tool_image)]

    if context_state.active_device_module:
        module_images = [
            image
            for image in context_state.available_tool_images
            if image.get("module") == context_state.active_device_module
        ]
        if module_images:
            return module_images[-MAX_TOOL_IMAGE_INPUTS:]

    return context_state.available_tool_images[-MAX_TOOL_IMAGE_INPUTS:]


def _user_requests_prior_tool_image(user_message: str) -> bool:
    normalized = re.sub(r"\s+", "", user_message).lower()
    if not normalized:
        return False
    direct_terms = (
        "图上",
        "图中",
        "图内",
        "图里",
        "图里的",
        "图片上",
        "图片中",
        "图片内",
        "图片里",
        "图片里的",
        "这张图",
        "刚才的图",
        "上面的图",
        "前面的图",
        "对照图",
        "示意图",
        "步骤图",
        "图示",
        "标注",
        "看图",
        "看一下图",
        "picture",
        "image",
        "diagram",
        "figure",
    )
    if any(term in normalized for term in direct_terms):
        return True
    numbered_label_match = re.search(r"(?:编号|标号|序号|数字|#|no\.?)\d+", normalized)
    question_terms = ("是什么", "是啥", "哪个", "哪一个", "代表什么", "什么意思", "叫什么", "叫啥")
    image_terms = ("图", "图片", "示意图", "步骤图", "对照图")
    if numbered_label_match and any(term in normalized for term in image_terms + question_terms):
        return True
    object_terms = ("哪个是", "哪里是", "位置", "长什么样", "长啥样", "怎么对照", "怎么看", "是什么", "是啥", "代表什么")
    device_terms = ("部件", "配件", "法兰", "按钮", "指示灯", "充电", "尺寸", "测量尺", "编号", "标号", "序号")
    return any(term in normalized for term in object_terms) and any(term in normalized for term in device_terms)


def _local_skill_asset_image(url: str) -> tuple[Path, str, int] | None:
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if not path.startswith("/skill-assets/"):
        return None

    relative_path = path.removeprefix("/skill-assets/")
    skill_id, _, asset_name = relative_path.partition("/")
    if not skill_id or not asset_name:
        return None
    asset_root = (SKILLS_ROOT / skill_id / "assets").resolve()
    asset_path = (asset_root / asset_name).resolve()
    try:
        asset_path.relative_to(asset_root)
    except ValueError:
        return None
    content_type = TOOL_IMAGE_CONTENT_TYPES.get(asset_path.suffix.lower())
    if content_type is None or not asset_path.is_file():
        return None
    return asset_path, content_type, asset_path.stat().st_size


def _image_file_data_url(path: Path, content_type: str) -> str | None:
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:{content_type};base64,{encoded}"


def _image_detail(value: object) -> str:
    if value in {"low", "high", "auto"}:
        return str(value)
    return "auto"


def _build_request_context_for_request(
    inputs: RuntimeInputs,
    options: BuildAgentRequestOptions,
) -> str:
    context_state = options.get("context_state")
    loaded_skill_ids = options.get("loaded_skill_ids")
    if not isinstance(loaded_skill_ids, list):
        loaded_skill_ids = None
    if isinstance(context_state, ContextState):
        request_context = build_request_context(inputs, context_state, loaded_skill_ids)
    else:
        request_context = build_request_context(inputs, None, loaded_skill_ids)
    if options.get("enable_tools", True):
        extra_lines = health_guidance_request_context_lines(inputs, loaded_skill_ids)
        if extra_lines:
            request_context = "\n".join([request_context, *extra_lines])
    return request_context


def _record_loaded_reference(context_state: object, tool_name: str, result: dict[str, Any]) -> None:
    if not isinstance(context_state, ContextState):
        return
    if not result.get("ok"):
        return
    tool_result = result.get("result")
    if not isinstance(tool_result, dict):
        return
    if tool_name == "read_skill_file":
        skill_id = str(tool_result.get("skill_id") or "").strip()
        kind = str(tool_result.get("kind") or "").strip()
        path = str(tool_result.get("path") or "").strip()
        if skill_id and kind and path:
            _append_loaded_reference(
                context_state,
                f"{skill_id}/{path} 已在当前会话中读取过；连续同一子服务任务优先复用，不要重复调用 read_skill_file，除非用户切换到新 reference 或上下文不足。",
            )
        return
    if tool_name != "device_manual_search":
        return
    if tool_result.get("status") not in {"manual_loaded", "manual_loaded_with_faq"}:
        return
    model = str(tool_result.get("model") or "").strip()
    manual = tool_result.get("manual")
    if model != "Air1" or not isinstance(manual, dict):
        return
    source = str(manual.get("source") or "references/air1/manual.md")
    reference = f"device-guidance/{model}/{source} 已在当前会话中加载过；后续同型号连续任务可复用，除非上下文不足或用户提出新的资料需求。"
    _append_loaded_reference(context_state, reference)


def _record_loaded_business_tool(context_state: object, tool_name: str, result: dict[str, Any]) -> None:
    if not isinstance(context_state, ContextState):
        return
    if not result.get("ok"):
        return
    if str(tool_name or "").strip() not in _DEFERRED_TOOL_NAMES:
        return
    record_loaded_tool(context_state, tool_name)


def _record_tool_images(context_state: object, tool_name: str, result: dict[str, Any]) -> None:
    if not isinstance(context_state, ContextState):
        return
    if tool_name != "device_manual_search" or not result.get("ok"):
        return
    set_active_service_domain(context_state, "device_guidance")
    for image in _tool_image_metadata(result):
        existing = [item for item in context_state.available_tool_images if item.get("url") != image.get("url")]
        existing.append(image)
        context_state.available_tool_images = existing[-MAX_TOOL_IMAGE_METADATA:]


def _record_displayed_tool_images(context_state: object, response: object) -> None:
    if not isinstance(context_state, ContextState):
        return

    text = _response_output_text(response)
    if not text:
        return
    displayed_images = _displayed_tool_images_from_text(context_state, text)
    if not displayed_images:
        return

    for image in displayed_images:
        url = image.get("url")
        if url and url not in context_state.shown_step_image_urls:
            context_state.shown_step_image_urls.append(url)
    context_state.shown_step_image_urls = context_state.shown_step_image_urls[-12:]

    last = displayed_images[-1]
    context_state.last_displayed_tool_image = last
    context_state.active_device_module = str(last.get("module") or context_state.active_device_module or "").strip()


def _displayed_tool_images_from_text(context_state: ContextState, text: str) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for url in re.findall(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text):
        image = _tool_image_metadata_by_url(context_state, url)
        if image:
            images.append(image)
    return images


def _tool_image_metadata_by_url(context_state: ContextState, url: str) -> dict[str, str] | None:
    normalized_url = str(url or "").strip()
    if _local_skill_asset_image(normalized_url) is None:
        return None
    for image in reversed(context_state.available_tool_images):
        if image.get("url") == normalized_url:
            return dict(image)
    return {"url": normalized_url}


def _response_output_text(response: object) -> str:
    output_text = _get_item_value(response, "output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    parts: list[str] = []
    for item in _get_response_output(response):
        item_type = _get_item_value(item, "type")
        if item_type == "output_text":
            text = _get_item_value(item, "text")
            if isinstance(text, str):
                parts.append(text)
            continue
        if item_type != "message":
            continue
        content = _get_item_value(item, "content")
        if not isinstance(content, list):
            continue
        for content_part in content:
            part_type = _get_item_value(content_part, "type")
            if part_type not in {"output_text", "text"}:
                continue
            text = _get_item_value(content_part, "text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part for part in parts if part)


def _web_search_citations_from_response(response: object) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for item in _get_response_output(response):
        if _get_item_value(item, "type") != "message":
            continue
        content = _get_item_value(item, "content")
        if not isinstance(content, list):
            continue
        for content_part in content:
            annotations = _get_item_value(content_part, "annotations")
            if not isinstance(annotations, list):
                continue
            for annotation in annotations:
                citation = _web_search_citation_from_annotation(annotation)
                if not citation:
                    continue
                url = citation["url"]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                citation["index"] = len(citations) + 1
                citations.append(citation)

    if citations:
        return citations[:8]

    for item in _get_response_output(response):
        citations = _merge_web_search_citations(citations, _web_search_citations_from_web_search_item(item))
        if len(citations) >= 8:
            return citations[:8]
    return citations[:8]


def _merge_web_search_citations(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for group in groups:
        for item in group:
            url = _clean_url(item.get("url"))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            citation = dict(item)
            citation["url"] = url
            citation["title"] = _clean_citation_title(citation.get("title"), url)
            citation["index"] = len(merged) + 1
            merged.append(citation)
            if len(merged) >= 8:
                return merged
    return merged


def _compact_web_search_citations_for_display(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    host_counts: dict[str, int] = {}
    for item in citations:
        url = _clean_url(item.get("url"))
        if not url:
            continue
        host = _citation_host(url)
        title = _clean_citation_title(item.get("title"), url)
        title_key = _citation_title_key(title, host)
        dedupe_key = f"{host}:{title_key}"
        if dedupe_key in seen_keys:
            continue
        if host and host_counts.get(host, 0) >= 2:
            continue
        seen_keys.add(dedupe_key)
        if host:
            host_counts[host] = host_counts.get(host, 0) + 1
        citation = dict(item)
        citation["url"] = url
        citation["title"] = title
        citation["index"] = len(compact) + 1
        display_text = f"{_citation_display_topic(title, url)}：{_citation_short_url(url)}"
        citation["display_text"] = display_text
        citation["displayText"] = display_text
        compact.append(citation)
        if len(compact) >= 4:
            break
    return compact


def _citation_host(url: str) -> str:
    try:
        return urlsplit(url).netloc.removeprefix("www.").lower()
    except Exception:
        return ""


def _citation_title_key(title: str, host: str) -> str:
    normalized = re.sub(r"\s+", " ", str(title or "").strip().lower())
    normalized = normalized.removeprefix("www.")
    if not normalized or normalized in {"参考来源", host, f"www.{host}", "protocols"}:
        return host or normalized
    return normalized


def _citation_display_topic(title: str, url: str) -> str:
    host = _citation_host(url)
    title_text = re.sub(r"\s+", " ", str(title or "").strip())
    title_key = _citation_title_key(title_text, host)
    lower_title = title_text.lower()

    if title_text and title_key != (host or title_key) and _contains_cjk(title_text):
        return title_text[:48]
    if "mastitis" in lower_title:
        return "哺乳期乳腺炎资料"
    if "hand expression" in lower_title:
        return "手挤奶指导"
    if "breastfeeding medicine" in lower_title or "protocol" in lower_title:
        return "ABM 哺乳医学临床指南"
    if "breastfeeding" in lower_title:
        return "母乳喂养专业资料"
    if "infant and child feeding" in lower_title:
        return "婴幼儿喂养指导"
    if "pregnancy" in lower_title or "obstetric" in lower_title:
        return "孕产健康专业资料"
    if "postpartum" in lower_title:
        return "产后健康专业资料"

    if "bfmed.org" in host or "abm.memberclicks.net" in host:
        return "ABM 哺乳医学资料"
    if "ncbi.nlm.nih.gov" in host:
        return "NCBI 医学资料"
    if "cdc.gov" in host:
        return "CDC 健康指南"
    if "who.int" in host:
        return "WHO 健康指南"
    if "nice.org.uk" in host:
        return "NICE 临床指南"
    if "acog.org" in host:
        return "ACOG 妇产科指南"
    if "aap.org" in host:
        return "AAP 儿科资料"
    if "nhc.gov.cn" in host:
        return "国家卫健委资料"
    if "unicef.org" in host:
        return "UNICEF 母婴健康资料"
    if "yiigle.com" in host or "cmcha.org" in host or "jundaodsj.com" in host:
        return "中文医学资料"
    return "专业资料"


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _citation_short_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except Exception:
        return url
    host = parsed.netloc.removeprefix("www.")
    if not host:
        return url
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return host
    if len(segments) == 1:
        return f"{host}/{segments[0]}"
    return f"{host}/{segments[0]}/..."


def _web_search_citations_from_web_search_item(item: object) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    if item is None:
        return citations
    containers = [
        _get_item_value(item, "action"),
        item,
    ]
    for container in containers:
        for key in ("sources", "results"):
            values = _get_item_value(container, key)
            if not isinstance(values, list):
                continue
            for source in values:
                citation = _web_search_citation_from_source(source)
                if citation:
                    citations = _merge_web_search_citations(citations, [citation])
    return citations


def _web_search_citation_from_annotation(annotation: object) -> dict[str, Any] | None:
    annotation_type = _get_item_value(annotation, "type")
    if annotation_type not in {"url_citation", "citation"}:
        return None
    url = _clean_url(_get_item_value(annotation, "url"))
    if not url:
        return None
    citation: dict[str, Any] = {
        "url": url,
        "title": _clean_citation_title(_get_item_value(annotation, "title"), url),
    }
    start_index = _get_item_value(annotation, "start_index")
    end_index = _get_item_value(annotation, "end_index")
    if isinstance(start_index, int):
        citation["start_index"] = start_index
    if isinstance(end_index, int):
        citation["end_index"] = end_index
    return citation


def _web_search_citation_from_source(source: object) -> dict[str, Any] | None:
    url = _clean_url(_get_item_value(source, "url"))
    if not url:
        return None
    return {
        "url": url,
        "title": _clean_citation_title(_get_item_value(source, "title"), url),
    }


def _clean_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return ""


def _clean_citation_title(value: object, url: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()[:120]
    try:
        host = urlsplit(url).netloc
    except Exception:
        host = ""
    return host or "参考来源"


def _tool_image_metadata(result: dict[str, Any]) -> list[dict[str, str]]:
    tool_result = result.get("result")
    if not isinstance(tool_result, dict):
        return []
    relevant_images = tool_result.get("relevant_images")
    if not isinstance(relevant_images, list):
        return []

    images: list[dict[str, str]] = []
    for item in relevant_images:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if _local_skill_asset_image(url) is None:
            continue
        image: dict[str, str] = {"url": url}
        for key in ("alt", "module", "image_text", "voice_policy", "spoken_label", "priority"):
            value = str(item.get(key) or "").strip()
            if value:
                image[key] = value
        images.append(image)
        if len(images) >= MAX_TOOL_IMAGE_METADATA:
            break
    return images


def _media_voice_from_tool_result(tool_result: dict[str, Any]) -> list[dict[str, str]]:
    relevant_images = tool_result.get("relevant_images")
    if not isinstance(relevant_images, list):
        return []

    items: list[dict[str, str]] = []
    for image in relevant_images:
        if not isinstance(image, dict):
            continue
        url = str(image.get("url") or "").strip()
        if not url:
            continue
        spoken_label = str(image.get("spoken_label") or "").strip()
        voice_policy = str(image.get("voice_policy") or "").strip()
        if not spoken_label or voice_policy not in {"announce", "read_text"}:
            continue
        item: dict[str, str] = {
            "media_id": url,
            "kind": "image",
            "voice_policy": voice_policy,
            "spoken_label": spoken_label,
        }
        alt = str(image.get("alt") or "").strip()
        if alt:
            item["visual_label"] = alt
        priority = str(image.get("priority") or "").strip()
        if priority:
            item["priority"] = priority
        items.append(item)
    return items[:MAX_TOOL_IMAGE_INPUTS]


def _append_loaded_reference(context_state: ContextState, reference: str) -> None:
    if reference not in context_state.loaded_references:
        context_state.loaded_references.append(reference)
    context_state.loaded_references = context_state.loaded_references[-12:]


def _tool_inputs_for_call(inputs: RuntimeInputs, options: BuildAgentRequestOptions) -> RuntimeInputs:
    tool_inputs = dict(inputs)
    quick_reply_guidance = options.get("_quick_reply_guidance")
    if isinstance(quick_reply_guidance, list):
        tool_inputs["_quick_reply_guidance"] = quick_reply_guidance
    context_state = options.get("context_state")
    if isinstance(context_state, ContextState):
        tool_inputs["_loaded_references"] = list(context_state.loaded_references)
        tool_inputs["_birth_prep_hospital_bag_slots"] = hospital_bag_slots(context_state)
        tool_inputs["_birth_journey_intake_state"] = birth_journey_intake_state(context_state)
        tool_inputs["_milk_management_state"] = dict(context_state.milk_management_state)
    return tool_inputs


def _sync_runtime_profile_from_tool_inputs(inputs: RuntimeInputs, tool_inputs: RuntimeInputs) -> None:
    profile = tool_inputs.get("user_profile") if isinstance(tool_inputs.get("user_profile"), dict) else {}
    if not profile:
        return
    existing = inputs.get("user_profile") if isinstance(inputs.get("user_profile"), dict) else {}
    inputs["user_profile"] = {**existing, **profile}
    if tool_inputs.get(_PROFILE_LOADED_FROM_DB_FLAG) is True:
        inputs[_PROFILE_LOADED_FROM_DB_FLAG] = True


def _update_quick_reply_guidance(options: BuildAgentRequestOptions, result: dict[str, Any]) -> None:
    if str(result.get("tool_name") or "") != "birth_journey_intake_manage":
        return

    tool_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    status = str(tool_result.get("status") or "").strip()
    next_step = str(tool_result.get("next_step") or "").strip()
    guidance = birth_journey_intake_quick_reply_guidance(next_step) if status == "in_progress" else []
    if guidance:
        options["_quick_reply_guidance"] = guidance  # type: ignore[typeddict-unknown-key]
    else:
        options.pop("_quick_reply_guidance", None)  # type: ignore[typeddict-item]


def _birth_journey_intake_direct_quick_replies(result: dict[str, Any]) -> list[dict[str, str]] | None:
    if str(result.get("tool_name") or "") != "birth_journey_intake_manage":
        return None
    tool_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    if str(tool_result.get("status") or "").strip() != "in_progress":
        return None
    next_step = str(tool_result.get("next_step") or "").strip()
    guidance = birth_journey_intake_quick_reply_guidance(next_step)
    return guidance or None


def _birth_journey_auto_plan_arguments(result: dict[str, Any]) -> dict[str, Any] | None:
    if str(result.get("tool_name") or "") != "birth_journey_intake_manage":
        return None
    tool_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    if str(tool_result.get("status") or "").strip() != "ready_to_generate":
        return None
    plan_context = tool_result.get("plan_context")
    if not isinstance(plan_context, dict):
        return None
    return {"plan_context": plan_context}


def _with_auto_birth_journey_plan_result(
    result: dict[str, Any],
    auto_tool_result: dict[str, Any] | None,
) -> dict[str, Any]:
    if not auto_tool_result:
        return result
    if str(result.get("tool_name") or "") != "birth_journey_intake_manage":
        return result
    tool_result = result.get("result")
    if not isinstance(tool_result, dict):
        return result
    next_result = dict(result)
    next_tool_result = dict(tool_result)
    next_tool_result["auto_tool_result"] = auto_tool_result
    next_result["result"] = next_tool_result
    return next_result


def _record_birth_prep_tool_state(context_state: object, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
    if not isinstance(context_state, ContextState):
        return
    if tool_name in _BIRTH_PREP_TOOL_NAMES:
        set_active_service_domain(context_state, "birth_prep")

    if tool_name == "birth_journey_plan_card_create":
        _record_birth_journey_plan_state(context_state, arguments, result)
        return

    if tool_name == "birth_journey_intake_manage":
        tool_result = result.get("result")
        if isinstance(tool_result, dict) and isinstance(tool_result.get("intake_state"), dict):
            merge_birth_journey_intake_state(context_state, tool_result["intake_state"])
        return

    if tool_name != "hospital_bag_form_create":
        return

    tool_result = result.get("result")
    if not isinstance(tool_result, dict):
        return

    form = tool_result.get("form")
    if isinstance(form, dict):
        form_defaults = form.get("default_values")
        if isinstance(form_defaults, dict):
            merge_hospital_bag_slots(context_state, form_defaults)


def _record_birth_journey_plan_state(context_state: ContextState, arguments: dict[str, Any], result: dict[str, Any]) -> None:
    tool_result = result.get("result")
    if not isinstance(tool_result, dict) or tool_result.get("status") != "card_created":
        return

    plan_context = _object_argument(arguments.get("plan_context"))
    card = tool_result.get("card")
    card_json = card.get("card_json") if isinstance(card, dict) else None
    owner = card_json.get("owner") if isinstance(card_json, dict) else None
    overview = card_json.get("overview") if isinstance(card_json, dict) else None
    due_date_or_week = _first_tool_state_text(
        plan_context.get("due_date_or_week"),
        plan_context.get("due_date"),
        plan_context.get("current_week"),
        owner.get("due_date_or_week") if isinstance(owner, dict) else None,
        overview.get("due_date_or_week") if isinstance(overview, dict) else None,
    )
    known_values = {
        "due_date_or_week": due_date_or_week,
        "first_birth": _first_tool_state_text(plan_context.get("first_birth"), owner.get("first_birth") if isinstance(owner, dict) else None),
        "fetus_count": _first_tool_state_text(plan_context.get("fetus_count"), owner.get("fetus_count") if isinstance(owner, dict) else None),
        "birth_path": _first_tool_state_text(plan_context.get("birth_path"), owner.get("birth_path") if isinstance(owner, dict) else None),
        "feeding_intention": _first_tool_state_text(plan_context.get("feeding_intention"), owner.get("feeding_intention") if isinstance(owner, dict) else None),
        "support_person": _first_tool_state_text(plan_context.get("support_person"), owner.get("support_person") if isinstance(owner, dict) else None),
    }
    merge_hospital_bag_slots(context_state, known_values)


def _record_milk_tool_state(context_state: object, tool_name: str, result: dict[str, Any]) -> None:
    if isinstance(context_state, ContextState):
        record_milk_management_tool_state(context_state, tool_name, result)


def _first_tool_state_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _object_argument(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _execute_project_tool(name: str, arguments: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    try:
        result = execute_tool(name, arguments, inputs)
        return {"ok": True, "tool_name": name, "result": result}
    except Exception as exc:
        return {
            "ok": False,
            "tool_name": name,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }


def _extract_function_calls(response: object) -> list[dict[str, Any]]:
    calls = []
    response_id = _get_response_id(response)
    for output_index, item in enumerate(_get_response_output(response)):
        if not _is_function_call_item(item):
            continue

        name = _get_item_value(item, "name")
        call_id = _get_item_value(item, "call_id") or _get_item_value(item, "id")
        item_id = _get_item_value(item, "id")
        arguments = _parse_tool_arguments(_get_item_value(item, "arguments"))
        if name and call_id:
            calls.append(
                {
                    "name": name,
                    "call_id": call_id,
                    "item_id": item_id,
                    "arguments": arguments,
                    "response_id": response_id,
                    "output_index": output_index,
                }
            )
    return calls


def _stream_function_call_from_event(event: object, item: object) -> dict[str, Any]:
    item_id = _get_item_value(item, "id")
    call_id = _get_item_value(item, "call_id") or item_id
    return {
        "name": _get_item_value(item, "name") or "tool",
        "call_id": call_id or "tool_call",
        "item_id": item_id,
        "arguments": _parse_tool_arguments(_get_item_value(item, "arguments")),
        "response_id": _get_item_value(event, "response_id"),
        "output_index": _get_item_value(event, "output_index"),
    }


def _is_function_call_item(item: object) -> bool:
    if _get_item_value(item, "type") == "function_call":
        return True
    return bool(_get_item_value(item, "name") and (_get_item_value(item, "call_id") or _get_item_value(item, "id")))


def _tool_call_was_seen(seen_keys: set[str], tool_call: dict[str, Any]) -> bool:
    return any(key in seen_keys for key in _tool_call_keys(tool_call))


def _remember_tool_call(seen_keys: set[str], tool_call: dict[str, Any]) -> None:
    seen_keys.update(_tool_call_keys(tool_call))


def _tool_call_keys(tool_call: dict[str, Any]) -> set[str]:
    keys = set()
    response_id = tool_call.get("response_id")
    output_index = tool_call.get("output_index")
    if response_id and output_index is not None:
        keys.add(f"response:{response_id}:output:{output_index}")
    if tool_call.get("call_id"):
        keys.add(f"call:{tool_call['call_id']}")
    if tool_call.get("item_id"):
        keys.add(f"item:{tool_call['item_id']}")
    return keys


def _get_response_output(response: object) -> list[Any]:
    if isinstance(response, dict):
        output = response.get("output", [])
    else:
        output = getattr(response, "output", [])
    return output if isinstance(output, list) else []


def _get_response_id(response: object) -> str | None:
    if isinstance(response, dict):
        response_id = response.get("id")
    else:
        response_id = getattr(response, "id", None)
    return response_id if isinstance(response_id, str) else None


def _get_item_value(item: object, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str) and raw_arguments:
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _emit_event(
    on_event: AgentEventHandler | None,
    phase: AgentEventPhase,
    message: str,
    metadata: dict[str, Any],
    on_ag_ui_event: AgUiEventHandler | None = None,
    ag_ui_status_message_id: str | None = None,
) -> None:
    event: AgentEvent = {
        "type": "agent.status",
        "phase": phase,
        "message": message,
        "metadata": metadata,
    }
    if on_event is None:
        pass
    else:
        on_event(event)
    if on_ag_ui_event is not None and ag_ui_status_message_id:
        on_ag_ui_event(status_custom_event(event))


def _emit_ag_ui_event(on_ag_ui_event: AgUiEventHandler | None, event: AgUiEvent) -> None:
    if on_ag_ui_event is not None:
        on_ag_ui_event(event)


def _tool_execution_phase(tool_name: str) -> AgentEventPhase:
    if tool_name == "load_skill":
        return "loading_skill"
    if tool_name == "read_skill_file":
        return "reading_skill_file"
    if tool_name == "run_approved_skill_script":
        return "executing_script"
    return "executing_tool"


def _tool_call_message(tool_name: str) -> str:
    return "Selecting the next step."


def _tool_execution_message(tool_name: str) -> str:
    if tool_name == "load_skill":
        return "Loading relevant context."
    if tool_name == "read_skill_file":
        return "Reading relevant information."
    if tool_name == "run_approved_skill_script":
        return "Running a processing step."
    return "Processing relevant information."


def _tool_completed_message(tool_name: str, ok: bool) -> str:
    return "Step completed." if ok else "Step failed."


def _tool_result_metadata(round_index: int, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "round": round_index,
        "tool_name": tool_name,
        "ok": bool(result.get("ok")),
    }
    tool_result = result.get("result")
    if isinstance(tool_result, dict):
        if isinstance(tool_result.get("id"), str):
            metadata["skill_id"] = tool_result["id"]
        if isinstance(tool_result.get("skill_id"), str):
            metadata["skill_id"] = tool_result["skill_id"]
        if isinstance(tool_result.get("status"), str):
            metadata["status"] = tool_result["status"]
    return metadata


def _error_metadata(exc: Exception, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = {
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    }
    if extra:
        metadata.update(extra)
    return metadata
