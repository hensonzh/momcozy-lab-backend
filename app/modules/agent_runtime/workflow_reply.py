from __future__ import annotations

import hmac
import secrets
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from .models import AgentWorkflowState


SUPPORTED_REPLY_WORKFLOW_TYPES = frozenset({"pregnancy_plan", "milk_analysis", "device_unboxing"})
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


def new_workflow_step_token() -> str:
    return secrets.token_urlsafe(24)


def normalize_workflow_reply_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        workflow_state_id = str(UUID(str(value.get("workflow_state_id") or "").strip()))
    except (TypeError, ValueError, AttributeError):
        return {}
    workflow_type = str(value.get("workflow_type") or "").strip()
    revision = value.get("revision")
    step_token = str(value.get("step_token") or "").strip()
    if (
        workflow_type not in SUPPORTED_REPLY_WORKFLOW_TYPES
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or not step_token
        or len(step_token) > 128
    ):
        return {}
    return {
        "workflow_state_id": workflow_state_id,
        "workflow_type": workflow_type,
        "revision": revision,
        "step_token": step_token,
    }


def build_workflow_reply_context(workflow: AgentWorkflowState) -> dict[str, Any]:
    revision = _positive_revision(getattr(workflow, "revision", None))
    step_token = str(getattr(workflow, "step_token", "") or "").strip()
    if not revision or not step_token or workflow.workflow_type not in SUPPORTED_REPLY_WORKFLOW_TYPES:
        return {}
    return {
        "workflow_state_id": str(workflow.id),
        "workflow_type": workflow.workflow_type,
        "revision": revision,
        "step_token": step_token,
    }


def validate_workflow_reply_context(workflow: AgentWorkflowState, value: Any) -> None:
    expected = build_workflow_reply_context(workflow)
    if not expected:
        return
    supplied = normalize_workflow_reply_context(value)
    if not supplied:
        raise ApiError(
            code="missing_workflow_reply_context",
            message="The workflow reply is not linked to the current step.",
            status=409,
        )
    token_matches = hmac.compare_digest(str(supplied["step_token"]), str(expected["step_token"]))
    if (
        supplied["workflow_state_id"] != expected["workflow_state_id"]
        or supplied["workflow_type"] != expected["workflow_type"]
        or supplied["revision"] != expected["revision"]
        or not token_matches
    ):
        raise ApiError(
            code="stale_workflow_step",
            message="The workflow step has changed since this question was shown.",
            status=409,
        )


def guarded_workflow_type(tool_name: str, args: dict[str, Any]) -> str | None:
    action = str(args.get("action") or "").strip()
    if tool_name == "pregnancy.plan_intake.advance" and action in _PREGNANCY_REPLY_ACTIONS:
        return "pregnancy_plan"
    if tool_name == "records.milk_analysis.intake" and action == "answer":
        return "milk_analysis"
    if tool_name == "devices.unboxing.advance" and action in {"complete_current", "cancel"}:
        return "device_unboxing"
    return None


def workflow_accepts_reply(workflow: AgentWorkflowState) -> bool:
    if workflow.status not in _ACTIVE_WORKFLOW_STATUSES or not workflow.active_step:
        return False
    state = workflow.state if isinstance(workflow.state, dict) else {}
    if workflow.workflow_type == "pregnancy_plan":
        return str(state.get("phase") or workflow.active_step).strip() in _PREGNANCY_REPLY_PHASES
    if workflow.workflow_type == "milk_analysis":
        return str(state.get("phase") or "").strip() == "collecting_intake" and bool(str(state.get("current_field") or "").strip())
    if workflow.workflow_type == "device_unboxing":
        return str(state.get("phase") or "").strip() == "guiding"
    return False


def _positive_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0
