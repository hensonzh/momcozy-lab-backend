from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.modules.agent_runtime.models import AgentWorkflowState
from app.modules.agent_runtime.workflow_reply import (
    build_workflow_reply_context,
    guarded_workflow_type,
    validate_workflow_reply_context,
)


def test_workflow_reply_context_contains_only_the_opaque_cursor() -> None:
    workflow = _workflow(workflow_type="pregnancy_plan", revision=4, step_token="current-token")

    assert build_workflow_reply_context(workflow) == {
        "workflow_state_id": str(workflow.id),
        "workflow_type": "pregnancy_plan",
        "revision": 4,
        "step_token": "current-token",
    }


@pytest.mark.parametrize(
    ("reply_context", "error_code"),
    [
        ({}, "missing_workflow_reply_context"),
        (
            {
                "workflow_state_id": "00000000-0000-0000-0000-000000000001",
                "workflow_type": "pregnancy_plan",
                "revision": 4,
                "step_token": "current-token",
            },
            "stale_workflow_step",
        ),
        (
            {
                "workflow_state_id": "WORKFLOW_ID",
                "workflow_type": "pregnancy_plan",
                "revision": 3,
                "step_token": "old-token",
            },
            "stale_workflow_step",
        ),
    ],
)
def test_workflow_reply_guard_rejects_missing_or_stale_cursors(
    reply_context: dict[str, object],
    error_code: str,
) -> None:
    workflow = _workflow(workflow_type="pregnancy_plan", revision=4, step_token="current-token")
    reply_context = {key: str(workflow.id) if value == "WORKFLOW_ID" else value for key, value in reply_context.items()}

    with pytest.raises(ApiError) as exc_info:
        validate_workflow_reply_context(workflow, reply_context)

    assert exc_info.value.code == error_code


def test_workflow_reply_guard_accepts_the_current_cursor() -> None:
    workflow = _workflow(workflow_type="milk_analysis", revision=6, step_token="current-token")

    validate_workflow_reply_context(workflow, build_workflow_reply_context(workflow))


@pytest.mark.parametrize(
    ("tool_name", "action", "workflow_type"),
    [
        ("pregnancy.plan_intake.advance", "submit_personalized_followup", "pregnancy_plan"),
        ("pregnancy.plan_intake.advance", "skip_checkup_records", "pregnancy_plan"),
        ("records.milk_analysis.intake", "answer", "milk_analysis"),
        ("devices.unboxing.advance", "complete_current", "device_unboxing"),
        ("devices.unboxing.advance", "cancel", "device_unboxing"),
        ("records.milk_analysis.intake", "start", None),
        ("records.milk_analysis.intake", "resume", None),
        ("devices.unboxing.advance", "start", None),
        ("devices.unboxing.advance", "resume", None),
    ],
)
def test_only_reply_driven_workflow_actions_require_a_cursor(
    tool_name: str,
    action: str,
    workflow_type: str | None,
) -> None:
    assert guarded_workflow_type(tool_name, {"action": action}) == workflow_type


def _workflow(*, workflow_type: str, revision: int, step_token: str) -> AgentWorkflowState:
    return AgentWorkflowState(
        id=uuid4(),
        thread_id=uuid4(),
        owner_user_id=uuid4(),
        run_id=uuid4(),
        workflow_type=workflow_type,
        status="waiting",
        schema_version="v1",
        state={"phase": "collecting"},
        active_step="collecting",
        revision=revision,
        step_token=step_token,
    )
