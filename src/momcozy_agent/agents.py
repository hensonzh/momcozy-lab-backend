from __future__ import annotations

import json
import time
from typing import Any
from uuid import uuid4

from .contexts import ContextState, build_request_context
from .static_context import STATIC_AGENT_INSTRUCTIONS
from .tool_registry import execute_tool, select_runtime_tools
from .types import AgUiEvent, AgUiEventHandler, AgentEvent, AgentEventHandler, AgentEventPhase, BuildAgentRequestOptions, ResponsesClientLike, ResponsesRequest, RuntimeInputs, TextDeltaHandler

AG_UI_STATUS_ACTIVITY_TYPE = "MOMCOZY_AGENT_STATUS"
AG_UI_STATUS_CUSTOM_NAME = "momcozy.agent.status"
AG_UI_THINKING_CUSTOM_NAME = "momcozy.agent.thinking"

MAX_TOOL_ROUNDS = 6


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
    }
    if result is not None:
        event["result"] = result
    return event


def run_error_event(message: str, code: str | None = None, *, thread_id: str | None = None, run_id: str | None = None) -> AgUiEvent:
    event: AgUiEvent = {
        "type": "RUN_ERROR",
        "timestamp": _timestamp_ms(),
        "message": message,
    }
    if thread_id:
        event["thread_id"] = thread_id
    if run_id:
        event["run_id"] = run_id
    if code:
        event["code"] = code
    return event


def step_started_event(step_name: str) -> AgUiEvent:
    return {
        "type": "STEP_STARTED",
        "timestamp": _timestamp_ms(),
        "step_name": step_name,
    }


def step_finished_event(step_name: str) -> AgUiEvent:
    return {
        "type": "STEP_FINISHED",
        "timestamp": _timestamp_ms(),
        "step_name": step_name,
    }


def tool_call_start_event(
    tool_call_id: str,
    tool_call_name: str,
    parent_message_id: str | None = None,
    *,
    response_id: str | None = None,
    output_index: int | None = None,
    item_id: str | None = None,
) -> AgUiEvent:
    event: AgUiEvent = {
        "type": "TOOL_CALL_START",
        "timestamp": _timestamp_ms(),
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_call_name,
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
    }


def status_custom_event(event: AgentEvent) -> AgUiEvent:
    return {
        "type": "CUSTOM",
        "timestamp": _timestamp_ms(),
        "name": AG_UI_STATUS_CUSTOM_NAME,
        "value": event,
    }


def thinking_custom_event(status: str, metadata: dict[str, Any] | None = None) -> AgUiEvent:
    return {
        "type": "CUSTOM",
        "timestamp": _timestamp_ms(),
        "name": AG_UI_THINKING_CUSTOM_NAME,
        "value": {
            "type": "agent.thinking",
            "status": status,
            "metadata": metadata or {},
        },
    }


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
        for key in ("id", "skill_id", "status", "resource_id", "side_effect_performed", "summary"):
            if key in tool_result:
                safe[key] = tool_result[key]
        tool_data = tool_result.get("data")
        if isinstance(tool_data, dict):
            for key in ("requires_confirmation", "requires_medical_confirmation", "confirmation_question"):
                if key in tool_data:
                    safe[key] = tool_data[key]
        if result.get("tool_name") in {"ui_form_create", "birth_plan_form_create", "hospital_bag_form_create"} and isinstance(tool_result.get("form"), dict):
            safe["form"] = tool_result["form"]
        if result.get("tool_name") in {"ui_card_create", "birth_plan_card_create", "hospital_bag_card_create"} and isinstance(tool_result.get("card"), dict):
            safe["card"] = tool_result["card"]
            if isinstance(tool_result.get("assistant_followup"), dict):
                safe["assistant_followup"] = tool_result["assistant_followup"]
        if result.get("tool_name") in {"milk_assessment_evaluate", "milk_plan_preview", "milk_plan_mutate"} and isinstance(tool_result.get("card"), dict):
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
    if isinstance(result.get("error"), dict):
        safe["error"] = result["error"]
    return safe


def model_tool_output(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the follow-up model turn small after UI artifacts are already streamed."""

    safe = safe_tool_result(result)
    tool_name = str(safe.get("tool_name") or "")
    if tool_name == "milk_assessment_evaluate" and isinstance(safe.get("card"), dict):
        return _compact_milk_analysis_card_output(safe)
    if tool_name == "milk_plan_preview" and isinstance(safe.get("card"), dict):
        return _compact_milk_plan_card_output(safe, result)

    if tool_name not in {
        "ui_form_create",
        "ui_card_create",
        "birth_plan_form_create",
        "birth_plan_card_create",
        "hospital_bag_form_create",
        "hospital_bag_card_create",
        "hospital_bag_cart_update",
        "hospital_bag_pump_recommend",
        "ibclc_consult_card_create",
        "support_ticket_draft_create",
    }:
        return result

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

    card = safe.get("card")
    if isinstance(card, dict):
        card_json = card.get("card_json")
        card_json_dict = card_json if isinstance(card_json, dict) else {}
        compact["card"] = {
            "card_type": card.get("card_type") or card_json_dict.get("card_type"),
            "schema_version": card.get("schema_version") or card_json_dict.get("schema_version"),
            "created": True,
        }

    ticket = safe.get("ticket")
    if isinstance(ticket, dict):
        compact["ticket"] = {
            "draft_id": ticket.get("draft_id"),
            "status": ticket.get("status"),
            "created": True,
        }

    return compact


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
            title="请确认售后工单",
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
            return "需要先确认健康边界"
        return "请确认奶量计划"
    if tool_name == "milk_calendar_change_preview":
        return "请确认日程调整"
    return "请确认后继续"


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def build_agent_request(inputs: RuntimeInputs, options: BuildAgentRequestOptions | None = None) -> ResponsesRequest:
    options = options or {}
    request_context = _build_request_context_for_request(inputs, options)
    return _build_response_request(inputs, options, [_user_input_item(request_context, inputs["user_message"], inputs.get("images", []))])


def _build_response_request(
    inputs: RuntimeInputs,
    options: BuildAgentRequestOptions,
    input_items: list[dict[str, Any]],
) -> ResponsesRequest:
    tools = select_runtime_tools()

    request: ResponsesRequest = {
        "model": options.get("model", "gpt-5.5"),
        "instructions": STATIC_AGENT_INSTRUCTIONS,
        "input": input_items,
        "tools": tools,
        "tool_choice": "auto",
        "reasoning": {"effort": "low"},
        "text": {
            "format": {"type": "text"},
            "verbosity": "medium",
        },
        "store": options.get("store", True),
        "prompt_cache_key": options.get("prompt_cache_key", "momcozy-agent-v2"),
    }

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
    on_text_delta: TextDeltaHandler | None = None,
    on_response_stream_event: Any | None = None,
) -> object:
    options = dict(options or {})
    loaded_skill_ids = list(options.get("loaded_skill_ids", []))
    ag_ui_thread_id = ag_ui_thread_id or default_ag_ui_thread_id(inputs)
    ag_ui_run_id = ag_ui_run_id or new_ag_ui_run_id()
    ag_ui_status_message_id = f"{ag_ui_run_id}:status"
    ag_ui_tool_result_message_id = f"{ag_ui_run_id}:tool-results"
    streamed_tool_call_keys: set[str] = set()

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
            ),
        )

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
            on_response_stream_event,
        )
    except Exception as exc:
        _emit_event(on_event, "failed", "Model request failed.", _error_metadata(exc), on_ag_ui_event, ag_ui_status_message_id)
        _emit_ag_ui_event(on_ag_ui_event, run_error_event(str(exc), type(exc).__name__, thread_id=ag_ui_thread_id, run_id=ag_ui_run_id))
        raise

    for round_index in range(max_tool_rounds):
        tool_calls = _extract_function_calls(response)
        if not tool_calls:
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
    on_stream_event: Any | None = None,
) -> object:
    if on_text_delta is None:
        return client.responses.create(**request)

    final_response = None
    reasoning_active = False
    output_text_seen = False
    stream = client.responses.create(**request, stream=True)
    for event in stream:
        event_type = _get_item_value(event, "type")
        _emit_stream_event(on_stream_event, event_type, event)
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


def _is_reasoning_start_event(event_type: Any, event: object) -> bool:
    if not isinstance(event_type, str):
        return False
    if event_type.startswith("response.reasoning") and not event_type.endswith(".done"):
        return True
    if event_type == "response.output_item.added":
        item = _get_item_value(event, "item")
        return _get_item_value(item, "type") == "reasoning"
    return False


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
        return build_request_context(inputs, context_state, loaded_skill_ids)
    return build_request_context(inputs, None, loaded_skill_ids)


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


def _append_loaded_reference(context_state: ContextState, reference: str) -> None:
    if reference not in context_state.loaded_references:
        context_state.loaded_references.append(reference)
    context_state.loaded_references = context_state.loaded_references[-12:]


def _tool_inputs_for_call(inputs: RuntimeInputs, options: BuildAgentRequestOptions) -> RuntimeInputs:
    tool_inputs = dict(inputs)
    context_state = options.get("context_state")
    if isinstance(context_state, ContextState):
        tool_inputs["_loaded_references"] = list(context_state.loaded_references)
    return tool_inputs


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
