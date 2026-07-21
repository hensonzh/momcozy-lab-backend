from __future__ import annotations

from typing import Any

from app.agent_runtime.runs.models import AgentWorkflowState


def project_cozymate_workflow_replay_state(workflow: AgentWorkflowState) -> dict[str, Any] | None:
    if workflow.workflow_type != "pregnancy_plan":
        return None
    state = workflow.state if isinstance(workflow.state, dict) else {}
    projected = {
        key: state.get(key)
        for key in (
            "phase",
            "form_id",
            "source_form_artifact_id",
            "source_form_submission_id",
            "analysis_run_id",
            "consumed_by_action_id",
            "interrupted_by_safety_signal",
        )
        if state.get(key) not in (None, "")
    }
    analysis = state.get("analysis")
    focuses = analysis.get("focuses") if isinstance(analysis, dict) else None
    if isinstance(focuses, list):
        focus_count = sum(
            1
            for focus in focuses
            if isinstance(focus, dict) and str(focus.get("id") or "").strip()
        )
        projected["focus_count"] = focus_count
        projected["personalized"] = focus_count > 1
    return projected
