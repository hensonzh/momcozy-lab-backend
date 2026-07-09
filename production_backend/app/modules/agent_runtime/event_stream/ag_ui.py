from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .transient import AgentTransientStreamEvent


AG_UI_STATUS_CUSTOM_NAME = "momcozy.agent.status"
QUICK_REPLIES_TOOL_NAME = "ui_quick_replies_create"


LEGACY_TOOL_NAME_ALIASES = {
    "load_service_skill": "load_skill",
    "profile.read": "profile_get",
    "business.context.read": "business_context_read",
    "records.milk_summary.read": "milk_snapshot_get",
    "records.milk_status.read": "milk_status_query",
    "records.milk_analysis.read": "milk_analysis_evaluate",
    "records.growth.read": "infant_growth_evaluate",
    "plans.current.read": "milk_plan_query",
    "plans.calendar.read": "milk_calendar_query",
    "diary.recent.read": "pregnancy_diary_manage",
    "diary.entry_upsert.propose": "pregnancy_diary_manage",
    "pregnancy.plan_context.read": "birth_journey_intake_manage",
    "pregnancy.plan_create.propose": "birth_journey_intake_manage",
    "memory.create.propose": "memory_create",
    "devices.pump_status.read": "device_manual_search",
    "devices.guidance_assets.read": "device_manual_search",
    "files.vision_summary.read": "vision_summary_read",
    "plans.milk_plan.propose": "milk_plan_mutate",
    "plans.milk_plan_preview.create": "milk_plan_preview_create",
    "plans.task_create.propose": "milk_calendar_mutate",
    "plans.task_update.propose": "milk_calendar_mutate",
    "plans.task_delete.propose": "milk_calendar_mutate",
    "plans.task_complete.propose": "milk_task_complete",
    "plans.plan_delete.propose": "birth_journey_plan_delete",
    "notifications.milk_reminder.propose": "reminder_create",
    "records.feeding_record.propose": "milk_record_mutate",
    "records.pumping_record.propose": "milk_record_mutate",
    "records.feeding_record_delete.propose": "milk_record_mutate",
    "records.pumping_record_delete.propose": "milk_record_mutate",
    "records.growth_record.propose": "infant_growth_mutate",
    "records.growth_record_update.propose": "infant_growth_mutate",
    "records.growth_record_delete.propose": "infant_growth_mutate",
    "support.ticket.propose": "support_ticket_draft_create",
    "support.ticket.create": "support_ticket_draft_create",
}


class AgUiSseEncoder:
    """Project internal runtime events to the legacy web AG-UI event shape."""

    def __init__(self) -> None:
        self._started_run_ids: set[str] = set()
        self._started_message_ids: set[str] = set()
        self._ended_message_ids: set[str] = set()
        self._tool_names_by_call_id: dict[str, str] = {}
        self._emitted_dedupe_keys: set[str] = set()

    def encode_persisted(self, events: list[object]) -> str:
        return "".join(self._encode_many(event, cursor_id=_sequence(event)) for event in events)

    def encode_transient(self, events: list[AgentTransientStreamEvent]) -> str:
        return "".join(self._encode_many(event, cursor_id=event.event_id) for event in events)

    def _encode_many(self, event: object, *, cursor_id: str) -> str:
        return "".join(_sse_data(payload, cursor_id=cursor_id) for payload in self._to_ag_ui(event))

    def _to_ag_ui(self, event: object) -> list[dict[str, Any]]:
        event_type = _event_type(event)
        payload = _payload(event)
        thread_id = _thread_id(event)
        run_id = _run_id(event)
        live_semantic = _dict(payload, "_live_semantic")
        dedupe_key = _event_dedupe_key(event_type=event_type, payload=payload, run_id=run_id) or _text(live_semantic, "dedupe_key")
        if dedupe_key:
            if dedupe_key in self._emitted_dedupe_keys:
                return []
            self._emitted_dedupe_keys.add(dedupe_key)
        events: list[dict[str, Any]]
        if event_type in {"run.queued", "run.started"}:
            if run_id in self._started_run_ids:
                return []
            self._started_run_ids.add(run_id)
            events = [_run_started_event(thread_id=thread_id, run_id=run_id)]
        elif event_type == "run.completed":
            events = [_run_finished_event(thread_id=thread_id, run_id=run_id)]
        elif event_type in {"run.failed", "run.cancelled"}:
            code = _text(payload, "code") or event_type.removeprefix("run.")
            events = [_run_error_event(thread_id=thread_id, run_id=run_id, code=code)]
        elif event_type == "run.waiting_for_confirmation":
            events = [self._confirmation_required_event(payload)]
        elif event_type == "message.delta":
            events = self._message_delta_events(run_id=run_id, payload=payload)
        elif event_type == "message.completed":
            events = self._message_completed_events(run_id=run_id, payload=payload)
        elif event_type in {"run.progress", "progress"}:
            events = [_status_event(payload=payload)]
        elif event_type == "tool.started":
            events = self._tool_started_events(payload)
        elif event_type == "tool.completed":
            events = self._tool_completed_events(payload)
        elif event_type == "tool.failed":
            events = self._tool_failed_events(payload)
        elif event_type == "artifact.created":
            events = [self._artifact_created_event(payload)]
        elif event_type == "action.confirmation_required":
            events = [self._confirmation_required_event(payload)]
        elif event_type in {"action.applied", "action.failed", "action.rejected", "action.expired"}:
            events = [_status_event(payload={"phase": event_type, "label": _action_status_label(event_type), **payload})]
        else:
            events = []
        return _with_live_semantic(events, live_semantic=live_semantic, dedupe_key=dedupe_key)

    def _message_delta_events(self, *, run_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        message_id = _text(payload, "message_stream_id") or f"assistant:{run_id}"
        delta = _text(payload, "delta")
        if not delta:
            return []
        events: list[dict[str, Any]] = []
        if message_id not in self._started_message_ids:
            self._started_message_ids.add(message_id)
            events.append(_text_message_start_event(message_id))
        events.append(_text_message_content_event(message_id, delta))
        return events

    def _message_completed_events(self, *, run_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        role = _text(payload, "role") or "assistant"
        if role != "assistant":
            return []
        message_id = _text(payload, "message_id") or f"assistant:{run_id}"
        text = _text(payload, "text")
        events: list[dict[str, Any]] = []
        if message_id not in self._started_message_ids:
            self._started_message_ids.add(message_id)
            events.append(_text_message_start_event(message_id))
            if text:
                events.append(_text_message_content_event(message_id, text))
        if message_id not in self._ended_message_ids:
            self._ended_message_ids.add(message_id)
            events.append(_text_message_end_event(message_id))
        quick_replies = _quick_replies(payload.get("quick_replies"))
        if quick_replies:
            events.append(_quick_replies_event(message_id=message_id, replies=quick_replies))
        return events

    def _tool_started_events(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        tool_call_id = _tool_call_id(payload)
        tool_name = _legacy_tool_name(_text(payload, "tool_name"))
        if tool_call_id:
            self._tool_names_by_call_id[tool_call_id] = tool_name
        events = [_tool_call_start_event(tool_call_id=tool_call_id, tool_call_name=tool_name, arguments=_dict(payload, "safe_args"))]
        safe_args = _dict(payload, "safe_args")
        if safe_args:
            events.append(_tool_call_args_event(tool_call_id=tool_call_id, tool_call_name=tool_name, arguments=safe_args))
        return events

    def _tool_completed_events(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        tool_call_id = _tool_call_id(payload)
        tool_name = _legacy_tool_name(_text(payload, "tool_name"))
        if tool_call_id:
            self._tool_names_by_call_id[tool_call_id] = tool_name
        result = _dict(payload, "safe_output") or {"status": "completed"}
        return [
            _tool_call_end_event(tool_call_id=tool_call_id, tool_call_name=tool_name),
            _tool_call_result_event(tool_call_id=tool_call_id, tool_call_name=tool_name, result=result),
        ]

    def _tool_failed_events(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        tool_call_id = _tool_call_id(payload)
        tool_name = _legacy_tool_name(_text(payload, "tool_name"))
        result = {"ok": False, "status": "failed", "error_code": _text(payload, "error_code") or "tool_failed"}
        return [
            _tool_call_end_event(tool_call_id=tool_call_id, tool_call_name=tool_name),
            _tool_call_result_event(tool_call_id=tool_call_id, tool_call_name=tool_name, result=result),
        ]

    def _artifact_created_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool_call_id = _text(payload, "tool_call_id")
        tool_name = self._tool_names_by_call_id.get(tool_call_id, _legacy_tool_name(_text(payload, "tool_name")))
        artifact_id = _text(payload, "artifact_id")
        artifact_type = _text(payload, "artifact_type")
        return {
            "type": "ARTIFACT_CREATED",
            "timestamp": _timestamp_ms(),
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "tool_call_id": tool_call_id,
            "tool_call_name": tool_name,
            "status": _text(payload, "status") or "ready",
            "artifact": payload.get("artifact") if isinstance(payload.get("artifact"), dict) else {},
            "semantic": _artifact_semantic(artifact_type=artifact_type, artifact_id=artifact_id, tool_name=tool_name),
        }

    def _confirmation_required_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        confirmation_id = _text(payload, "action_id") or _text(payload, "confirmation_id")
        tool_call_id = _text(payload, "tool_call_id") or confirmation_id
        tool_name = self._tool_names_by_call_id.get(tool_call_id, _legacy_tool_name(_text(payload, "action_type")))
        title = _confirmation_title(payload)
        event = {
            "type": "CONFIRMATION_REQUIRED",
            "timestamp": _timestamp_ms(),
            "confirmation_id": confirmation_id,
            "tool_call_id": tool_call_id,
            "tool_call_name": tool_name,
            "title": title,
            "message": _text(payload, "message"),
            "semantic": _semantic("confirming", title or "我需要你确认一下，再继续处理", "action", f"confirmation:{confirmation_id}", priority=90),
        }
        artifact_id = _text(payload, "artifact_id")
        if artifact_id:
            event["artifact_id"] = artifact_id
        return event


def _sse_data(event: dict[str, Any], *, cursor_id: str) -> str:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    prefix = f"id: {cursor_id}\n" if cursor_id else ""
    return f"{prefix}data: {data}\n\n"


def _run_started_event(*, thread_id: str, run_id: str) -> dict[str, Any]:
    return {
        "type": "RUN_STARTED",
        "timestamp": _timestamp_ms(),
        "thread_id": thread_id,
        "run_id": run_id,
        "semantic": _semantic("thinking", "我已经收到你的消息啦～", "status", f"run:{run_id}", priority=10),
    }


def _run_finished_event(*, thread_id: str, run_id: str) -> dict[str, Any]:
    return {
        "type": "RUN_FINISHED",
        "timestamp": _timestamp_ms(),
        "thread_id": thread_id,
        "run_id": run_id,
        "semantic": _semantic("done", "我处理好啦", "hidden", f"run:{run_id}", priority=100),
    }


def _run_error_event(*, thread_id: str, run_id: str, code: str) -> dict[str, Any]:
    return {
        "type": "RUN_ERROR",
        "timestamp": _timestamp_ms(),
        "thread_id": thread_id,
        "run_id": run_id,
        "code": code,
        "message": "Agent run failed.",
        "semantic": _semantic("error", "这轮暂时没处理好", "status", f"run_error:{code or 'unknown'}", priority=100),
    }


def _text_message_start_event(message_id: str) -> dict[str, Any]:
    return {
        "type": "TEXT_MESSAGE_START",
        "timestamp": _timestamp_ms(),
        "message_id": message_id,
        "role": "assistant",
        "semantic": _text_message_semantic("start", message_id),
    }


def _text_message_content_event(message_id: str, delta: str) -> dict[str, Any]:
    return {
        "type": "TEXT_MESSAGE_CONTENT",
        "timestamp": _timestamp_ms(),
        "message_id": message_id,
        "delta": delta,
        "semantic": _text_message_semantic("content", message_id),
    }


def _text_message_end_event(message_id: str) -> dict[str, Any]:
    return {
        "type": "TEXT_MESSAGE_END",
        "timestamp": _timestamp_ms(),
        "message_id": message_id,
        "semantic": _text_message_semantic("end", message_id),
    }


def _quick_replies_event(*, message_id: str, replies: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "type": "QUICK_REPLIES",
        "timestamp": _timestamp_ms(),
        "message_id": message_id,
        "replies": replies,
        "semantic": _semantic("done", "我帮你准备好下一轮的快捷输入啦", "hidden", f"quick_replies:{message_id}", priority=80),
    }


def _tool_call_start_event(*, tool_call_id: str, tool_call_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "TOOL_CALL_START",
        "timestamp": _timestamp_ms(),
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_call_name,
        "semantic": _tool_semantic(tool_call_name, "start", tool_call_id=tool_call_id, arguments=arguments),
    }


def _tool_call_args_event(*, tool_call_id: str, tool_call_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "TOOL_CALL_ARGS",
        "timestamp": _timestamp_ms(),
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_call_name,
        "delta": json.dumps(arguments, ensure_ascii=False),
        "semantic": _tool_semantic(tool_call_name, "args", tool_call_id=tool_call_id, arguments=arguments),
    }


def _tool_call_end_event(*, tool_call_id: str, tool_call_name: str) -> dict[str, Any]:
    return {
        "type": "TOOL_CALL_END",
        "timestamp": _timestamp_ms(),
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_call_name,
        "semantic": _tool_semantic(tool_call_name, "end", tool_call_id=tool_call_id),
    }


def _tool_call_result_event(*, tool_call_id: str, tool_call_name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "TOOL_CALL_RESULT",
        "timestamp": _timestamp_ms(),
        "message_id": f"tool:{tool_call_id}",
        "tool_call_id": tool_call_id,
        "tool_call_name": tool_call_name,
        "content": json.dumps(result, ensure_ascii=False),
        "role": "tool",
        "semantic": _tool_semantic(tool_call_name, "result", tool_call_id=tool_call_id, result=result),
    }


def _status_event(*, payload: dict[str, Any]) -> dict[str, Any]:
    phase = _text(payload, "phase") or "working"
    label = _text(payload, "label") or _text(payload, "message") or "我继续处理一下～"
    return {
        "type": "CUSTOM",
        "timestamp": _timestamp_ms(),
        "name": AG_UI_STATUS_CUSTOM_NAME,
        "value": {
            "type": "agent.status",
            "phase": phase,
            "message": label,
            "metadata": dict(payload),
        },
        "semantic": _status_semantic(phase=phase, label=label),
    }


def _text_message_semantic(stage: str, message_id: str) -> dict[str, Any]:
    if stage == "start":
        return _semantic("replying", "我在组织回复～", "hidden", f"text:{message_id}", priority=40)
    if stage == "content":
        return _semantic("replying", "我在回复你～", "hidden", f"text:{message_id}", priority=40)
    if stage == "end":
        return _semantic("done", "我整理好回复啦", "hidden", f"text:{message_id}", priority=80)
    return _semantic("replying", "我在处理回复～", "hidden", f"text:{message_id}", priority=40)


def _status_semantic(*, phase: str, label: str) -> dict[str, Any]:
    if phase == "failed":
        return _semantic("error", "这一步暂时没处理好", "status", f"status:{phase}", priority=95)
    if phase in {"context_loading", "context_ready", "model_reasoning", "requesting_model", "started"}:
        return _semantic("thinking", label, "status", f"status:{phase or 'loop'}", priority=20)
    if phase == "response_finalizing":
        return _semantic("replying", label, "hidden", f"status:{phase}", priority=30)
    return _semantic("working", label, "status", f"status:{phase or 'loop'}", priority=20)


def _artifact_semantic(*, artifact_type: str, artifact_id: str, tool_name: str) -> dict[str, Any]:
    if artifact_type == "form":
        label = "我已经准备好确认内容啦"
    elif artifact_type in {"support_ticket", "support_ticket_draft"}:
        label = "请确认售后信息"
    elif artifact_type == "mom_baby_status_card":
        label = "我已经整理好宝宝和我页面啦"
    elif artifact_type == "milk_analysis_card":
        label = "我已经整理好奶量分析结果啦"
    elif artifact_type == "milk_plan_card":
        label = "我已经整理好奶量计划啦"
    else:
        label = "我已经整理好结果啦"
    return _semantic("done", label, "artifact", f"artifact:{artifact_id or tool_name or artifact_type or 'current'}", priority=70)


def _tool_semantic(
    tool_name: str,
    stage: str,
    *,
    tool_call_id: str,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _legacy_tool_name(tool_name)
    if normalized == QUICK_REPLIES_TOOL_NAME:
        return _quick_replies_tool_semantic(stage, tool_call_id)
    return _semantic(
        _tool_semantic_phase(normalized),
        _tool_stage_label(normalized, stage, arguments or {}, result or {}),
        "work_item",
        f"tool:{tool_call_id or normalized or 'current'}",
        priority=50,
    )


def _quick_replies_tool_semantic(stage: str, tool_call_id: str) -> dict[str, Any]:
    if stage == "result":
        return _semantic(
            "done",
            "我帮你准备好下一轮的快捷输入啦",
            "status",
            f"quick_replies:{tool_call_id or 'current'}",
            priority=60,
        )
    return _semantic(
        "planning",
        "我在帮你准备下一轮的快捷输入～",
        "status",
        f"quick_replies:{tool_call_id or 'current'}",
        priority=60,
    )


def _tool_semantic_phase(tool_name: str) -> str:
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
        "memory_create",
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
    return "reading"


def _tool_stage_label(tool_name: str, stage: str, arguments: dict[str, Any], result: dict[str, Any]) -> str:
    if stage == "result":
        return _tool_result_label(tool_name, result)
    if stage == "end":
        return _tool_end_label(tool_name)
    return _tool_start_label(tool_name, arguments)


def _tool_start_label(tool_name: str, arguments: dict[str, Any]) -> str:
    labels = {
        "load_skill": "我按场景说明处理这一步～",
        "profile_get": "我先看一下你的基础信息～",
        "profile_update": "我先帮你记一下基础信息～",
        "business_context_read": "我先看一下相关上下文～",
        "milk_snapshot_get": "我先看看你的奶量情况～",
        "milk_status_query": "我先看看今天的奶量状态～",
        "milk_records_query": "我先看看吸奶和喂养记录～",
        "milk_plan_query": "我先看看之前保存的奶量计划～",
        "milk_calendar_query": "我先看看计划和日程任务～",
        "milk_analysis_evaluate": "我来综合评估一下奶量问题～",
        "infant_growth_evaluate": "我先看看宝宝的生长信号～",
        "milk_plan_preview_create": "我先帮你拟一版奶量计划～",
        "milk_record_mutate": "我先帮你处理这条记录～",
        "milk_task_complete": "我先帮你记录任务完成情况～",
        "milk_plan_mutate": "我先帮你保存奶量计划～",
        "milk_calendar_mutate": "我先帮你保存日程调整～",
        "infant_growth_mutate": "我先帮你保存宝宝成长记录～",
        "birth_plan_form_create": "我先帮你准备确认内容～",
        "hospital_bag_form_create": "我先帮你准备确认内容～",
        "labor_communication_card_create": "我先帮你整理分娩沟通单～",
        "birth_journey_plan_card_create": "我先帮你整理孕期计划～",
        "birth_journey_plan_delete": "我先帮你删除孕期计划～",
        "birth_journey_plan_todo_update": "我先帮你同步计划完成状态～",
        "hospital_bag_card_create": "我先帮你整理待产包清单～",
        "hospital_bag_pump_recommend": "我先看看适合你的吸奶器型号～",
        "hospital_bag_cart_update": "我先帮你调整待产包购物车～",
        "ibclc_consult_card_create": "我先帮你准备 IBCLC 咨询入口～",
        "support_ticket_draft_create": "我先帮你准备售后信息表～",
        "device_manual_search": "我先确认设备这一步～",
        "vision_summary_read": "我先看一下图片内容～",
        "memory_create": "我先帮你记住这个偏好～",
        "reminder_create": "我先帮你设置提醒～",
        "birth_journey_intake_manage": "我先整理孕期计划信息～",
        "ui_quick_replies_create": "我在帮你准备下一轮的快捷输入～",
    }
    if tool_name == "pregnancy_diary_manage":
        action = str(arguments.get("action") or "").strip()
        if action in {"write", "update", "create"}:
            return "我先帮你保存孕期日记～"
        if action == "delete":
            return "我先帮你删除孕期日记～"
        return "我先看看孕期日记～"
    return labels.get(tool_name, "我按当前场景继续处理～")


def _tool_end_label(tool_name: str) -> str:
    if tool_name in {"milk_records_query", "milk_status_query", "milk_snapshot_get", "milk_plan_query", "milk_calendar_query"}:
        return "我把奶量和日程信息整理一下～"
    if tool_name in {"business_context_read", "vision_summary_read", "device_manual_search"}:
        return "我把这一步整理好了～"
    if tool_name in {"milk_analysis_evaluate", "infant_growth_evaluate", "risk_evaluate"}:
        return "我把评估结果整理一下～"
    if tool_name == "milk_plan_preview_create":
        return "我再完善一下计划草稿～"
    if tool_name in {"milk_record_mutate", "milk_task_complete", "milk_plan_mutate", "milk_calendar_mutate", "infant_growth_mutate"}:
        return "我在保存这次修改～"
    if tool_name == "hospital_bag_pump_recommend":
        return "我把推荐结果整理一下～"
    if tool_name == "hospital_bag_cart_update":
        return "我在保存购物车修改～"
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
    if tool_name in {"load_skill", "memory_create", "reminder_create"}:
        return "我继续处理一下～"
    return "我继续处理一下～"


def _tool_result_label(tool_name: str, result: dict[str, Any]) -> str:
    status = str(result.get("status") or "").strip()
    if result.get("result_ok") is False or result.get("ok") is False:
        return "这一步暂时没处理好"
    if status.startswith("needs_"):
        return "我还需要先确认几件事～"
    if result.get("requires_confirmation") is True:
        return "我已经准备好预览～"
    labels = {
        "load_skill": "我准备好继续处理啦",
        "profile_get": "我看过你的基础信息啦",
        "profile_update": "我已经记好了",
        "business_context_read": "我看好相关上下文啦",
        "milk_records_query": "我把吸奶和喂养记录整理好啦",
        "milk_status_query": "我看好今天的奶量状态啦",
        "milk_snapshot_get": "我把奶量情况整理好啦",
        "milk_calendar_query": "我把计划和日程整理好啦",
        "milk_plan_query": "我看好之前的奶量计划啦",
        "milk_analysis_evaluate": "我完成奶量分析啦",
        "infant_growth_evaluate": "我完成宝宝生长评估啦",
        "milk_record_mutate": "我已经准备好记录草稿～",
        "milk_plan_mutate": "我已经准备好奶量计划草稿～",
        "milk_calendar_mutate": "我已经准备好日程调整草稿～",
        "milk_task_complete": "我已经准备好任务更新啦",
        "infant_growth_mutate": "我已经准备好宝宝成长记录草稿～",
        "milk_plan_preview_create": "我拟好奶量计划草稿啦",
        "birth_plan_form_create": "我已经准备好确认内容啦",
        "hospital_bag_form_create": "我已经准备好确认内容啦",
        "labor_communication_card_create": "我已经帮你整理好分娩沟通单啦",
        "birth_journey_intake_manage": "我整理好这一步信息啦",
        "birth_journey_plan_card_create": "我已经帮你整理好孕期计划啦",
        "birth_journey_plan_delete": "我已经准备好删除预览啦",
        "birth_journey_plan_todo_update": "我已经同步计划完成状态啦",
        "pregnancy_diary_manage": "孕期日记这一步处理好了",
        "hospital_bag_card_create": "我已经帮你生成好待产包清单啦",
        "ibclc_consult_card_create": "我已经准备好 IBCLC 咨询入口啦",
        "support_ticket_draft_create": "请确认售后信息",
        "hospital_bag_pump_recommend": "我已经帮你整理好吸奶器推荐啦",
        "hospital_bag_cart_update": "我已经帮你更新好待产包购物车啦",
        "device_manual_search": "我把设备资料整理好啦",
        "vision_summary_read": "我把图片信息整理好了～",
        "memory_create": "我已经记好了",
        "reminder_create": "我已经准备好提醒草稿～",
    }
    return labels.get(tool_name, "这一步处理好啦")


def _semantic(phase: str, label: str, visibility: str, merge_key: str, *, priority: int = 0) -> dict[str, Any]:
    return {
        "phase": phase,
        "label": label,
        "visibility": visibility,
        "merge_key": merge_key,
        "priority": priority,
    }


def _legacy_tool_name(tool_name: str) -> str:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return ""
    return LEGACY_TOOL_NAME_ALIASES.get(normalized, normalized.split(".")[-1].removeprefix("milk_management__"))


def _tool_call_id(payload: dict[str, Any]) -> str:
    return _text(payload, "tool_call_id") or _text(payload, "call_id")


def _event_dedupe_key(*, event_type: str, payload: dict[str, Any], run_id: str) -> str:
    if event_type not in {"tool.started", "tool.completed", "tool.failed"}:
        return ""
    tool_call_id = _tool_call_id(payload)
    if not run_id or not tool_call_id:
        return ""
    return f"{run_id}:{event_type}:{tool_call_id}"


def _with_live_semantic(events: list[dict[str, Any]], *, live_semantic: dict[str, Any], dedupe_key: str) -> list[dict[str, Any]]:
    if not events or not live_semantic:
        return events
    decorated: list[dict[str, Any]] = []
    for event in events:
        decorated.append(
            {
                **event,
                "dedupe_key": dedupe_key or _text(live_semantic, "dedupe_key"),
                "optimistic": bool(live_semantic.get("optimistic")),
                "durable": bool(live_semantic.get("durable")),
            }
        )
    return decorated


def _quick_replies(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    replies: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            replies.append({"text": text})
    return replies


def _confirmation_title(payload: dict[str, Any]) -> str:
    preview = payload.get("preview_payload")
    if isinstance(preview, dict):
        title = str(preview.get("title") or preview.get("issue_summary") or preview.get("summary") or "").strip()
        if title:
            return title
    action_type = _text(payload, "action_type")
    if action_type == "support.ticket.create":
        return "请确认售后信息"
    return "我需要你确认一下，再继续处理"


def _action_status_label(event_type: str) -> str:
    return {
        "action.applied": "这次修改已经完成啦",
        "action.failed": "这次修改暂时没处理好",
        "action.rejected": "这次修改已取消",
        "action.expired": "这次确认已过期",
    }.get(event_type, "我继续处理一下～")


def _event_type(event: object) -> str:
    return str(getattr(event, "event_type", None) or getattr(event, "type", "") or "").strip()


def _payload(event: object) -> dict[str, Any]:
    value = getattr(event, "payload", {})
    return dict(value) if isinstance(value, dict) else {}


def _thread_id(event: object) -> str:
    return str(getattr(event, "thread_id", "") or "")


def _run_id(event: object) -> str:
    return str(getattr(event, "run_id", "") or "")


def _sequence(event: object) -> str:
    return str(getattr(event, "sequence", "") or "")


def _dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _timestamp_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
