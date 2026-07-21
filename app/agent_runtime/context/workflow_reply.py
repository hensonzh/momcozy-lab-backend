from __future__ import annotations

import hmac
import secrets
from typing import Any
from uuid import UUID

from app.core.errors import ApiError
from app.agent_runtime.runs.models import AgentWorkflowState


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
        not workflow_type
        or len(workflow_type) > 120
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
    if not revision or not step_token or not workflow.workflow_type:
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


def _positive_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0
