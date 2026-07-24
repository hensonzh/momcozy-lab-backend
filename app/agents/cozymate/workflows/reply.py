from __future__ import annotations

from typing import Any

from app.agent_runtime.runs.models import AgentWorkflowState


_PREGNANCY_REPLY_ACTIONS = frozenset(
    {
        "submit_personalized_followup",
        "finish_personalized_followups",
        "confirm_checkup_done",
        "confirm_no_checkup_yet",
        "confirm_checkup_unknown",
        "mark_checkup_records_uploaded",
        "skip_checkup_records",
        "confirm_ready_to_generate",
        "submit_final_additional_info",
        "abandon",
    }
)
_ACTIVE_WORKFLOW_STATUSES = frozenset({"collecting", "ready", "waiting", "paused"})
_PREGNANCY_REPLY_PHASES = frozenset(
    {
        "collecting_intake",
        "personalized_followup",
        "checkup_done_question",
        "checkup_records_upload",
        "final_plan_confirmation",
        "awaiting_additional_information",
    }
)


def guarded_workflow_type(tool_name: str, args: dict[str, Any]) -> str | None:
    command = str(args.get("command") or "").strip()
    if tool_name == "pregnancy_plan_workflow" and command in {
        "answer_current",
        "edit_answer",
        "pause",
        "abandon",
    }:
        return "pregnancy_plan"
    operation = str(args.get("operation") or "").strip()
    if tool_name == "milk_analysis" and operation == "answer":
        return "milk_analysis"
    if tool_name == "devices_guidance" and operation in {"complete_current", "cancel"}:
        return "device_unboxing"
    return None


def workflow_accepts_reply(workflow: AgentWorkflowState) -> bool:
    if workflow.status not in _ACTIVE_WORKFLOW_STATUSES or not workflow.active_step:
        return False
    if workflow.status == "paused":
        return False
    state = workflow.state if isinstance(workflow.state, dict) else {}
    if workflow.workflow_type == "pregnancy_plan":
        return str(state.get("phase") or workflow.active_step).strip() in _PREGNANCY_REPLY_PHASES
    if workflow.workflow_type == "milk_analysis":
        return str(state.get("phase") or "").strip() == "collecting_intake" and bool(
            str(state.get("current_field") or "").strip()
        )
    if workflow.workflow_type == "device_unboxing":
        return str(state.get("phase") or "").strip() == "guiding"
    return False
