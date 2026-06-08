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
    build_request_context,
    clear_pending_hospital_bag_slot,
    hospital_bag_slots,
    merge_hospital_bag_slots,
)
from .health_guidance import health_guidance_request_context_lines, health_guidance_required_web_search_tool_choice
from .static_context import STATIC_AGENT_INSTRUCTIONS
from .tool_registry import execute_tool, select_runtime_tools
from .types import AgUiEvent, AgUiEventHandler, AgentEvent, AgentEventHandler, AgentEventPhase, BuildAgentRequestOptions, ResponsesClientLike, ResponsesRequest, RuntimeInputs, TextDeltaHandler

AG_UI_STATUS_ACTIVITY_TYPE = "MOMCOZY_AGENT_STATUS"
AG_UI_STATUS_CUSTOM_NAME = "momcozy.agent.status"
AG_UI_THINKING_CUSTOM_NAME = "momcozy.agent.thinking"
AG_UI_WEB_SEARCH_CUSTOM_NAME = "momcozy.agent.web_search"
AG_UI_WEB_SEARCH_CITATIONS_CUSTOM_NAME = "momcozy.web_search.citations"
QUICK_REPLIES_TOOL_NAME = "ui_quick_replies_create"

AgUiSemantic = dict[str, Any]

MAX_TOOL_ROUNDS = 6
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "skills"
MAX_TOOL_IMAGE_INPUTS = 2
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
        "semantic": _semantic_payload("done", "我准备好几个下一步选项啦", "hidden", f"quick_replies:{message_id}", priority=80),
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
        label = "我先理解一下你的需求～" if state == "started" else "我判断好你的需求啦"
        phase = "thinking" if state == "started" else "done"
    else:
        label = "我先处理这一步～" if state == "started" else "这一步处理好啦"
        phase = "working" if state == "started" else "done"
    return _semantic_payload(phase, label, "status", f"step:{step_name}", priority=30)


def _status_event_semantic(event: AgentEvent) -> AgUiSemantic:
    message = str(event.get("message") or "").strip()
    phase = str(event.get("phase") or "").strip()
    if phase == "failed" or message == "Step failed.":
        return _semantic_payload("error", "这一步暂时没处理好", "status", f"status:{phase or 'failed'}", priority=95)
    if phase in {"requesting_model", "started", "model_tool_call", "tool_completed"}:
        return _semantic_payload("thinking", _status_label(message), "hidden", f"status:{phase or 'loop'}", priority=20)
    return _semantic_payload("working", _status_label(message), "status", f"status:{phase or 'loop'}", priority=20)


def _thinking_semantic(status: str, metadata: dict[str, Any]) -> AgUiSemantic:
    normalized_status = status.strip().lower()
    if normalized_status in {"started", "running"}:
        label = "我在准备下一步～" if metadata.get("after_output_text") is True else "我想一下"
        return _semantic_payload("thinking", label, "status", "thinking:current", priority=40)
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
        return "专业资料暂时没查好"
    return "我在查专业资料～"


def _artifact_semantic(artifact_type: str, artifact_id: str, tool_name: str) -> AgUiSemantic:
    normalized_artifact_type = str(artifact_type or "").strip()
    if normalized_artifact_type == "form":
        label = "我已经准备好确认内容啦"
    elif normalized_artifact_type in {"support_ticket", "support_ticket_draft"}:
        label = "我已经准备好售后工单草稿啦"
    elif normalized_artifact_type == "mom_baby_status_card":
        label = "我已经整理好母婴状态页啦"
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
    phase = _tool_semantic_phase(normalized)
    label = _tool_stage_label(normalized, stage, arguments or {}, result or {})
    visibility = "work_item"
    return _semantic_payload(phase, label, visibility, f"tool:{tool_call_id or normalized or 'current'}", priority=50)


def _normalize_tool_name(tool_name: str) -> str:
    token = str(tool_name or "").strip()
    if not token:
        return ""
    return token.split(".")[-1].removeprefix("milk_management__")


def _tool_semantic_phase(tool_name: str) -> str:
    if tool_name in {"tool_search", "tool_search_call"}:
        return "thinking"
    if tool_name in {"milk_assessment_evaluate", "infant_growth_evaluate", "risk_evaluate"}:
        return "evaluating"
    if tool_name in {"milk_plan_preview", "milk_calendar_change_preview", "milk_calendar_reschedule_preview"}:
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
    }:
        return "saving"
    if tool_name in {
        "ui_form_create",
        "birth_plan_form_create",
        "hospital_bag_form_create",
        "labor_communication_card_create",
        "birth_journey_plan_card_create",
        "hospital_bag_card_create",
        "ibclc_consult_card_create",
        "support_ticket_draft_create",
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
        return "让我看看如何处理～"
    if tool_name == "load_skill":
        return _load_skill_label(arguments)
    if tool_name == "list_skills":
        return "我看看可以怎么帮你～"
    if tool_name == "search_skill_assets":
        return "我去找找相关资料～"
    if tool_name == "read_skill_file":
        return "我先看一下相关说明～"
    if tool_name == "profile_get":
        return "我先看一下你的基础信息～"
    if tool_name == "milk_snapshot_get":
        return "我先看看你的奶量情况～"
    if tool_name == "milk_status_query":
        return "我先看看今天的奶量状态～"
    if tool_name == "milk_records_query":
        return "我先看看吸奶和喂养记录～"
    if tool_name == "milk_plan_query":
        return "我先看看之前保存的奶量计划～"
    if tool_name == "milk_calendar_query":
        return "我先看看计划和日程任务～"
    if tool_name == "milk_assessment_evaluate":
        return "我来看看奶量趋势和执行情况～"
    if tool_name == "infant_growth_evaluate":
        return "我来看看宝宝的生长信号～"
    if tool_name == "risk_evaluate":
        return "我先确认一下安全边界～"
    if tool_name == "milk_plan_preview":
        return "我先帮你拟一版奶量计划～"
    if tool_name in {"milk_calendar_change_preview", "milk_calendar_reschedule_preview"}:
        return "我先帮你排一下日程调整～"
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
        return "我先帮你整理生产全过程计划～"
    if tool_name == "hospital_bag_card_create":
        return "我先帮你整理待产包清单～"
    if tool_name == "ibclc_consult_card_create":
        return "我先帮你准备 IBCLC 咨询入口～"
    if tool_name == "support_ticket_draft_create":
        return "我先帮你准备售后工单～"
    if tool_name == "hospital_bag_pump_recommend":
        return "我先看看适合你的吸奶器型号～"
    if tool_name == "hospital_bag_cart_update":
        return "我先帮你调整待产包购物车～"
    if tool_name == "device_manual_search":
        return "我先看看设备说明～"
    if tool_name == "knowledge_search":
        return "我去找找相关资料～"
    if tool_name == "memory_search":
        return "我去找一下之前的信息～"
    if tool_name == "reminder_list":
        return "我先看看你的提醒～"
    if tool_name == "run_approved_skill_script":
        return "我先处理这一步～"
    return "我先处理这一步～"


def _tool_end_label(tool_name: str) -> str:
    if tool_name in {"tool_search", "tool_search_call"}:
        return "我找到合适的方案啦"
    if tool_name in {"milk_records_query", "milk_status_query", "milk_snapshot_get", "milk_plan_query", "milk_calendar_query"}:
        return "我把奶量和日程信息整理一下～"
    if tool_name in {"milk_assessment_evaluate", "infant_growth_evaluate", "risk_evaluate"}:
        return "我把评估结果整理一下～"
    if tool_name == "milk_plan_preview":
        return "我再完善一下计划草稿～"
    if tool_name in {"milk_calendar_change_preview", "milk_calendar_reschedule_preview"}:
        return "我把调整后的安排整理一下～"
    if tool_name in {"milk_record_mutate", "milk_task_complete", "milk_plan_mutate", "milk_calendar_mutate", "infant_growth_mutate"}:
        return "我在保存这次修改～"
    if tool_name == "hospital_bag_pump_recommend":
        return "我把推荐结果整理一下～"
    if tool_name == "hospital_bag_cart_update":
        return "我在保存购物车修改～"
    if tool_name == "device_manual_search":
        return "我把设备内容整理一下～"
    if tool_name == "support_ticket_draft_create":
        return "我在整理工单草稿～"
    if tool_name in {"ui_form_create", "birth_plan_form_create", "hospital_bag_form_create", "labor_communication_card_create", "birth_journey_plan_card_create", "hospital_bag_card_create", "ibclc_consult_card_create"}:
        return "我在把结果整理出来～"
    return "我继续处理一下～"


def _tool_result_label(tool_name: str, result: dict[str, Any]) -> str:
    if result.get("ok") is False:
        return "这一步暂时没处理好"
    status = str(result.get("status") or "").strip()
    if status.startswith("needs_"):
        return "我还需要先确认几件事～"
    if status == "plan_preview_needs_revision":
        return "这版结果还需要再调一下"
    if status == "plan_preview_not_recommended":
        return "这版方案我不建议继续用"
    if status == "plan_preview_needs_medical_confirmation":
        return "我需要先确认健康边界～"
    if result.get("requires_confirmation") is True:
        return "我已经准备好预览，等你确认～"
    if tool_name in {"tool_search", "tool_search_call"}:
        return "我在执行这个方案啦～"
    if tool_name == "load_skill":
        return "我准备好继续处理啦"
    if tool_name == "profile_get":
        return "我看过你的基础信息啦"
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
    if tool_name == "milk_assessment_evaluate":
        return "我完成奶量评估啦"
    if tool_name == "infant_growth_evaluate":
        return "我完成宝宝生长评估啦"
    if tool_name == "risk_evaluate":
        return "我确认好安全边界啦"
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
    if tool_name == "milk_plan_preview":
        return "我拟好奶量计划草稿啦"
    if tool_name in {"milk_calendar_change_preview", "milk_calendar_reschedule_preview"}:
        return "我整理好日程调整预览啦"
    if tool_name in {"ui_form_create", "birth_plan_form_create", "hospital_bag_form_create"}:
        return "我已经准备好确认内容啦"
    if tool_name == "labor_communication_card_create":
        return "我已经帮你整理好分娩沟通单啦"
    if tool_name == "birth_journey_plan_card_create":
        return "我已经帮你整理好生产全过程计划啦"
    if tool_name == "hospital_bag_card_create":
        return "我已经帮你生成好待产包清单啦"
    if tool_name == "ibclc_consult_card_create":
        return "我已经准备好 IBCLC 咨询入口啦"
    if tool_name == "support_ticket_draft_create":
        return "我已经准备好售后工单草稿啦"
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
        "device-guidance": "我先切到设备指导这件事上～",
        "emotion-support": "我先切到情绪支持这件事上～",
    }
    return labels.get(skill_id, "我先准备一下这个场景～")


def _status_label(message: str) -> str:
    labels = {
        "Agent loop started.": "我在接收你的消息～",
        "Requesting model response.": "我想一下",
        "Requesting model response with tool outputs.": "我在准备下一步～",
        "Selecting the next step.": "我来判断下一步怎么做～",
        "Loading relevant context.": "我去看一下相关信息～",
        "Reading relevant information.": "我去看一下相关信息～",
        "Processing relevant information.": "我把刚看到的信息整理一下～",
        "Step completed.": "这一步处理好啦",
        "Step failed.": "这一步暂时没处理好",
    }
    return labels.get(message, message or "我先处理这一步～")


def safe_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name in {"load_skill", "read_skill_file", "search_skill_assets", "run_approved_skill_script"}:
        return {key: arguments[key] for key in ("skill_id", "kind", "path", "script_name", "query") if key in arguments}

    redacted: dict[str, Any] = {"argument_keys": sorted(arguments.keys())}
    if "idempotency_key" in arguments:
        redacted["has_idempotency_key"] = True
    return redacted


def safe_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "tool_name": result.get("tool_name"),
    }
    tool_result = result.get("result")
    if isinstance(tool_result, dict):
        for key in ("id", "skill_id", "status", "resource_id", "side_effect_performed", "summary", "missing_fields"):
            if key in tool_result:
                safe[key] = tool_result[key]
        tool_data = tool_result.get("data")
        if isinstance(tool_data, dict):
            for key in ("requires_confirmation", "requires_medical_confirmation", "confirmation_question"):
                if key in tool_data:
                    safe[key] = tool_data[key]
        if result.get("tool_name") in {"ui_form_create", "birth_plan_form_create", "hospital_bag_form_create"} and isinstance(tool_result.get("form"), dict):
            safe["form"] = tool_result["form"]
        if result.get("tool_name") in {"labor_communication_card_create", "birth_journey_plan_card_create", "hospital_bag_card_create"} and isinstance(tool_result.get("card"), dict):
            safe["card"] = tool_result["card"]
            if isinstance(tool_result.get("assistant_followup"), dict):
                safe["assistant_followup"] = tool_result["assistant_followup"]
        if result.get("tool_name") in {"milk_status_query", "milk_assessment_evaluate", "milk_plan_preview", "milk_plan_mutate"} and isinstance(tool_result.get("card"), dict):
            safe["card"] = tool_result["card"]
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
        if result.get("tool_name") == QUICK_REPLIES_TOOL_NAME and isinstance(tool_result.get("quick_replies"), list):
            safe["quick_replies"] = tool_result["quick_replies"]
    if isinstance(result.get("error"), dict):
        safe["error"] = result["error"]
    return safe


def model_tool_output(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the follow-up model turn small after UI artifacts are already streamed."""

    safe = safe_tool_result(result)
    tool_name = str(safe.get("tool_name") or "")
    if tool_name == "milk_assessment_evaluate" and isinstance(safe.get("card"), dict):
        return _compact_milk_analysis_card_output(safe)
    if tool_name == "milk_status_query" and isinstance(safe.get("card"), dict):
        return _compact_mom_baby_status_card_output(safe)
    if tool_name == "milk_plan_preview" and isinstance(safe.get("card"), dict):
        return _compact_milk_plan_card_output(safe, result)

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
        "labor_communication_card_create",
        "birth_journey_plan_card_create",
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
                "生产全过程计划还不能生成。最终回复只向用户补问 confirmation_question 中缺失的信息，"
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
        "assistant_followup",
        "message",
        "cart_update",
        "recommended_product",
        "alternatives",
        "cart_sync_suggestion",
        "source_urls",
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

    followup = compact.get("assistant_followup")
    if isinstance(followup, dict):
        message = str(followup.get("message") or "").strip()
        if message:
            compact["final_response_instruction"] = (
                "最终回复只能原样输出 assistant_followup.message，保留其中的段落换行；"
                "不要改写、扩写、重复交付语或再补充下一步。"
            )

    return compact


def _form_artifact_final_response_instruction(tool_name: str) -> str:
    if tool_name == "birth_plan_form_create":
        return (
            "分娩沟通单信息表已经展示。最终回复只输出下面两段中文，保留空行，"
            "不要改写、扩写，不要提表单里没有的字段、医院会额外确认什么或已经生成沟通单：\n\n"
            "好，我先帮你把分娩沟通单信息表打开了。\n\n"
            "你填完并提交后，我会按表单里确认的信息整理成一份给医生/护士看的沟通单。"
        )
    if tool_name == "hospital_bag_form_create":
        return (
            "待产包信息采集表已经展示。最终回复只输出下面两段中文，保留空行，"
            "不要改写、扩写，不要提医院、家里已有物品、购物或下单：\n\n"
            "好，我先帮你把待产包信息表打开了。\n\n"
            "你填完并提交后，我会按表单里确认的信息整理成一份清单。"
        )
    if tool_name == "ui_form_create":
        return (
            "信息确认表已经展示。最终回复只简短说明表单已打开，并请用户提交后继续处理；"
            "不要补充表单中没有的字段、不要承诺已经生成后续结果，也不要承诺任何外部提交。"
        )
    return ""


def _card_artifact_final_response_instruction(tool_name: str, card: dict[str, Any]) -> str:
    if tool_name != "ibclc_consult_card_create":
        return ""
    card_json = card.get("card_json")
    card_body = card_json if isinstance(card_json, dict) else card
    chat = card_body.get("chat") if isinstance(card_body.get("chat"), dict) else {}
    note = str(chat.get("note") or "启动咨询后，会自动将你的问题同步给顾问").strip()
    return (
        "IBCLC 咨询入口已经展示。最终回复只输出下面两段中文，保留空行，"
        "不要改写、扩写，不要承诺已经预约、已经接通、顾问正在处理或任何入口内容里没有的服务能力：\n\n"
        "IBCLC 咨询入口我准备好了。\n\n"
        f"你勾选隐私政策和服务协议后，就可以启动咨询；{note}。"
    )


def _ticket_artifact_final_response_instruction(tool_name: str) -> str:
    if tool_name != "support_ticket_draft_create":
        return ""
    return (
        "售后工单草稿已经展示。最终回复只输出下面两段中文，保留空行，"
        "不要改写、扩写，不要承诺已经提交、客服已经接手或会在具体时间联系用户：\n\n"
        "售后工单草稿我整理好了。\n\n"
        "你确认并提交后，才会进入售后处理；现在还没有对外提交。"
    )


def _compact_birth_journey_plan_card_output(safe: dict[str, Any]) -> dict[str, Any]:
    card = safe.get("card")
    card_json = card.get("card_json") if isinstance(card, dict) else None
    card_json_dict = card_json if isinstance(card_json, dict) else {}
    response = _birth_journey_plan_final_response(card_json_dict)

    return _compact_card_tool_output(
        safe,
        (
            "生产全过程计划已经展示完整路线图。最终回复按段落直接输出下面这段 1-3 句中文，"
            "保留空行，不要改写、扩写，语气要保持自然陪伴，"
            "不要复述计划里的所有阶段、日期或完整清单：\n\n"
            f"{response}"
        ),
    )


def _birth_journey_plan_final_response(card_json: dict[str, Any]) -> str:
    current_phase = _birth_journey_current_phase(card_json)
    phase_title = str(current_phase.get("title") or "").strip()
    watchout = _first_birth_journey_item(current_phase.get("watchouts"))
    action = _first_birth_journey_item(current_phase.get("actions"))
    goal = _clean_birth_journey_fragment(current_phase.get("goal"))
    next_action = card_json.get("next_action") if isinstance(card_json.get("next_action"), dict) else {}
    label = str(next_action.get("label") or "").strip()
    help_item = _first_birth_journey_item(current_phase.get("comate_help"))

    return "\n\n".join(
        [
            "生产全过程计划我整理好了。",
            _birth_journey_phase_summary_sentence(phase_title, watchout, action, goal),
            _birth_journey_service_sentence(label or help_item),
        ]
    )


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


def _compact_milk_analysis_card_output(safe: dict[str, Any]) -> dict[str, Any]:
    card = safe.get("card")
    card_json = card.get("card_json") if isinstance(card, dict) else None
    card_json_dict = card_json if isinstance(card_json, dict) else {}
    analysis_status = str(card_json_dict.get("status") or safe.get("status") or "").strip()
    status_label = str(card_json_dict.get("status_label") or "").strip()
    return {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
        "card": {
            "card_type": (card or {}).get("card_type") if isinstance(card, dict) else "milk_analysis_card",
            "schema_version": (card or {}).get("schema_version") if isinstance(card, dict) else "1.0",
            "created": True,
        },
        "analysis_status": analysis_status,
        "status_label": status_label,
        "next_actions": _milk_analysis_next_actions(analysis_status),
        "final_response_instruction": (
            "奶量分析卡片已经展示完整结果。最终回复只能引导用户选择下一步，"
            "不要复述卡片中的结论、数字、趋势、参考区间、原因推测或建议内容；"
            "不要输出“整体看/结果是/数据显示”等分析句。用一句自然的话给出 2-3 个可选动作。"
        ),
    }


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
            "母婴状态页已经按“妈妈数字分身”和“宝宝数字分身”两个顶部 tab 展示。"
            "最终回复只提示用户可以切换 tab 查看，不要把两个 tab 的指标和建议逐条复述到正文里。"
        ),
    }


def _compact_milk_plan_card_output(safe: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    card = safe.get("card")
    card_json = card.get("card_json") if isinstance(card, dict) else None
    card_json_dict = card_json if isinstance(card_json, dict) else {}
    compact: dict[str, Any] = {
        "ok": safe.get("ok"),
        "tool_name": safe.get("tool_name"),
        "card": {
            "card_type": (card or {}).get("card_type") if isinstance(card, dict) else "milk_plan_card",
            "schema_version": (card or {}).get("schema_version") if isinstance(card, dict) else "1.0",
            "created": True,
        },
        "plan_status": str(card_json_dict.get("status") or safe.get("status") or "").strip(),
        "next_actions": ["同步到日历", "调整计划", "展开具体时间表"],
        "final_response_instruction": (
            "奶量计划卡片已经展示完整计划。最终回复只能引导用户确认下一步，"
            "不要复述卡片中的计划方向、目标、安排、数字、周期、任务数或日期范围；"
            "可以用 calendar_sync_prompt 说明同步到日历的影响，但只保留一句短话；"
            "给出“同步到日历/先调整/展开时间表”这类选择。"
        ),
    }
    for key in ("requires_confirmation", "requires_medical_confirmation", "confirmation_question"):
        if key in safe:
            compact[key] = safe[key]

    data = _tool_result_data(result)
    calendar_sync_prompt = _milk_plan_calendar_sync_prompt(data)
    if calendar_sync_prompt:
        compact["calendar_sync_prompt"] = calendar_sync_prompt

    plan_preview = _compact_milk_plan_preview_for_model(result)
    if plan_preview:
        compact["plan_preview"] = plan_preview
    return compact


def _milk_analysis_next_actions(status: str) -> list[str]:
    if status == "under_supply_alert":
        return ["生成从明天开始的温和追奶计划", "看看今天怎么吸更合适", "展开可能原因"]
    if status == "over_supply_alert":
        return ["看看今天怎么安排更舒服", "展开偏高可能原因", "做一个温和调整方案"]
    if status == "normal":
        return ["看看今天怎么保持", "设置两三天后复盘", "展开数据说明"]
    return ["补充最近一两天记录", "看看需要补哪些数据", "稍后再分析一次"]


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
        compact["confirmed_plan_for_save"] = draft

    return compact


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
            title="我需要你确认售后工单",
            message="工单仍是草稿，确认后才会提交。",
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
    if tool_name == "milk_plan_preview":
        if safe_result.get("requires_medical_confirmation"):
            return "我需要先确认健康边界"
        return "我需要你确认奶量计划"
    if tool_name == "milk_calendar_change_preview":
        return "我需要你确认日程调整"
    return "我需要你确认一下，再继续处理"


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
        request["tools"] = select_runtime_tools(inputs)
        request["tool_choice"] = health_guidance_required_web_search_tool_choice(inputs) or "auto"
        request["include"] = ["web_search_call.action.sources"]

    max_output_tokens = options.get("max_output_tokens")
    if isinstance(max_output_tokens, int) and max_output_tokens > 0:
        request["max_output_tokens"] = max_output_tokens

    previous_response_id = inputs.get("previous_response_id")
    if previous_response_id:
        request["previous_response_id"] = previous_response_id

    return request


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
        for tool_call in tool_calls:
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
            result = _execute_project_tool(tool_call["name"], tool_call["arguments"], _tool_inputs_for_call(inputs, options))
            if tool_call["name"] == "load_skill" and result.get("ok") and result.get("result", {}).get("id"):
                skill_id = result["result"]["id"]
                if skill_id not in loaded_skill_ids:
                    loaded_skill_ids.append(skill_id)
            _record_loaded_reference(options.get("context_state"), tool_call["name"], result)
            _record_tool_images(options.get("context_state"), tool_call["name"], result)
            _record_birth_prep_tool_state(options.get("context_state"), tool_call["name"], tool_call["arguments"], result)
            safe_result = safe_tool_result(result)
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
                    "output": json.dumps(model_tool_output(result), ensure_ascii=False),
                }
            )

        options["loaded_skill_ids"] = loaded_skill_ids
        next_inputs = dict(inputs)
        response_id = _get_response_id(response)
        if response_id:
            next_inputs["previous_response_id"] = response_id

        request = _build_response_request(next_inputs, options, tool_outputs)
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
                output_text_seen = True
                on_text_delta(delta)
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
        extra_lines = health_guidance_request_context_lines(inputs)
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


def _record_tool_images(context_state: object, tool_name: str, result: dict[str, Any]) -> None:
    if not isinstance(context_state, ContextState):
        return
    if tool_name != "device_manual_search" or not result.get("ok"):
        return
    for image in _tool_image_metadata(result):
        existing = [item for item in context_state.available_tool_images if item.get("url") != image.get("url")]
        existing.append(image)
        context_state.available_tool_images = existing[-8:]


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
        for key in ("alt", "module", "image_text"):
            value = str(item.get(key) or "").strip()
            if value:
                image[key] = value
        images.append(image)
        if len(images) >= MAX_TOOL_IMAGE_INPUTS:
            break
    return images


def _append_loaded_reference(context_state: ContextState, reference: str) -> None:
    if reference not in context_state.loaded_references:
        context_state.loaded_references.append(reference)
    context_state.loaded_references = context_state.loaded_references[-12:]


def _tool_inputs_for_call(inputs: RuntimeInputs, options: BuildAgentRequestOptions) -> RuntimeInputs:
    tool_inputs = dict(inputs)
    context_state = options.get("context_state")
    if isinstance(context_state, ContextState):
        tool_inputs["_loaded_references"] = list(context_state.loaded_references)
        tool_inputs["_birth_prep_hospital_bag_slots"] = hospital_bag_slots(context_state)
    return tool_inputs


def _record_birth_prep_tool_state(context_state: object, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
    if not isinstance(context_state, ContextState):
        return

    if tool_name == "birth_journey_plan_card_create":
        _record_birth_journey_plan_state(context_state, arguments, result)
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

    status = str(tool_result.get("status") or "").strip()
    if status == "form_created":
        clear_pending_hospital_bag_slot(context_state)


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
