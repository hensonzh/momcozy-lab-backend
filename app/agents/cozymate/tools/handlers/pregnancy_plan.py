from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.executor import ToolHandlerContext
from app.core.errors import ApiError

from ..pregnancy_plan_flow import (
    PregnancyPlanPhase,
    pause_pregnancy_plan_workflow,
    pregnancy_plan_urgent_signal_ids,
    resolve_pregnancy_plan_answer,
    resume_pregnancy_plan_workflow,
    revise_pregnancy_plan_workflow,
)
from .base import _StandardToolHandler, _ToolOperationOutput
from .birth_support import (
    PregnancyPlanIntakeAdvanceToolHandler,
    PregnancyPlanIntakeAnalyzeToolHandler,
    PregnancyPlanIntakeStartToolHandler,
)
from .plans_diary import PregnancyPlanProposeToolHandler
from .shared import (
    _interrupt_pregnancy_plan_for_safety,
    _pregnancy_plan_urgent_result,
    _pregnancy_plan_workflow_result,
    _upsert_pregnancy_plan_workflow,
)


class PregnancyPlanWorkflowToolHandler(_StandardToolHandler):
    """One model-facing facade over the deterministic pregnancy-plan workflow.

    The legacy operation handlers stay internal so plan creation continues to
    cross the audited ``pregnancy.plan.create`` action boundary.
    """

    def __init__(
        self,
        *,
        runtime_service: AgentRuntimeService,
        start_handler: Any | None = None,
        analyze_handler: Any | None = None,
        advance_handler: Any | None = None,
        generate_handler: Any | None = None,
    ) -> None:
        self.runtime_service = runtime_service
        self.start_handler = start_handler or PregnancyPlanIntakeStartToolHandler(runtime_service=runtime_service)
        self.analyze_handler = analyze_handler or PregnancyPlanIntakeAnalyzeToolHandler(runtime_service=runtime_service)
        self.advance_handler = advance_handler or PregnancyPlanIntakeAdvanceToolHandler(runtime_service=runtime_service)
        self.generate_handler = generate_handler or PregnancyPlanProposeToolHandler(runtime_service=runtime_service)

    async def execute(self, context: ToolHandlerContext) -> _ToolOperationOutput:
        command = _text(context.args, "command")
        delegated_args = {key: value for key, value in context.args.items() if key != "command"}
        delegated_args["runtime_workflow_command"] = command

        if command == "start_or_resume":
            workflow = _optional_workflow(context.args)
            reusable = (
                bool(workflow)
                and not str(workflow.get("consumed_by_action_id") or "").strip()
                and workflow.get("abandoned") is not True
                and context.args.get("restart") is not True
            )
            if reusable:
                status_override = "pregnancy_plan_workflow_active"
                if workflow.get("paused") is True:
                    workflow = resume_pregnancy_plan_workflow(workflow)
                    status_override = "pregnancy_plan_workflow_resumed"
                if _text(workflow, "phase") == PregnancyPlanPhase.COLLECTING_INTAKE.value:
                    delegated_args["runtime_workflow_context"] = workflow
                    return await self.start_handler.execute(
                        replace(context, args=delegated_args)
                    )
                return await self._persist_transition(
                    context,
                    workflow,
                    status_override=status_override,
                )
            return await self.start_handler.execute(replace(context, args=delegated_args))
        if command == "submit_form":
            return await self.analyze_handler.execute(replace(context, args=delegated_args))
        if command == "answer_current":
            try:
                action, payload = resolve_pregnancy_plan_answer(
                    _workflow(context.args),
                    choice_id=_text(context.args, "choice_id"),
                    free_text=_text(context.args, "answer"),
                    checkup_attachment_count=_attachment_count(context.args),
                )
            except ValueError as exc:
                raise _workflow_api_error(exc) from exc
            delegated_args.update(payload)
            delegated_args["action"] = action
            return await self.advance_handler.execute(replace(context, args=delegated_args))
        if command == "abandon":
            delegated_args["action"] = "abandon"
            return await self.advance_handler.execute(replace(context, args=delegated_args))
        if command == "generate_plan":
            return await self.generate_handler.execute(replace(context, args=delegated_args))
        if command == "pause":
            try:
                paused = pause_pregnancy_plan_workflow(_workflow(context.args))
            except ValueError as exc:
                raise _workflow_api_error(exc) from exc
            return await self._persist_transition(
                context,
                paused,
                status_override="pregnancy_plan_workflow_paused",
            )
        if command == "resume":
            return await self._persist_transition(
                context,
                resume_pregnancy_plan_workflow(_workflow(context.args)),
                status_override="pregnancy_plan_workflow_resumed",
            )
        if command == "edit_answer":
            if _text(context.args, "step_id") == "basic_intake":
                prior = _workflow(context.args)
                edit_args = {
                    **delegated_args,
                    "restart": True,
                    "default_values": dict(prior.get("plan_context") or {}),
                    "edit_step_id": "basic_intake",
                    "prior_workflow_context": prior,
                }
                return await self.start_handler.execute(replace(context, args=edit_args))
            workflow = _workflow(context.args)
            urgent_signal_ids = pregnancy_plan_urgent_signal_ids(
                {"additional_info": _text(context.args, "answer")}
            )
            if urgent_signal_ids:
                await _interrupt_pregnancy_plan_for_safety(
                    runtime_service=self.runtime_service,
                    context=context,
                    workflow=workflow,
                    signal_ids=urgent_signal_ids,
                )
                return _pregnancy_plan_urgent_result(urgent_signal_ids)
            try:
                revised = revise_pregnancy_plan_workflow(
                    workflow,
                    step_id=_text(context.args, "step_id"),
                    choice_id=_text(context.args, "choice_id"),
                    answer=_text(context.args, "answer"),
                    checkup_attachment_count=_attachment_count(context.args),
                )
            except ValueError as exc:
                raise _workflow_api_error(exc) from exc
            return await self._persist_transition(
                context,
                revised,
                status_override="pregnancy_plan_answer_revised",
            )
        raise ApiError(code="invalid_pregnancy_plan_command", message="Unsupported pregnancy-plan workflow command.", status=422)

    async def _persist_transition(
        self,
        context: ToolHandlerContext,
        workflow: dict[str, Any],
        *,
        status_override: str,
    ) -> _ToolOperationOutput:
        await _upsert_pregnancy_plan_workflow(
            runtime_service=self.runtime_service,
            context=context,
            workflow=workflow,
        )
        return _pregnancy_plan_workflow_result(
            workflow,
            status_override=status_override,
            checkup_attachment_count=_attachment_count(context.args),
        )


def _workflow(args: dict[str, Any]) -> dict[str, Any]:
    workflow = _optional_workflow(args)
    if not workflow:
        raise ApiError(
            code="pregnancy_plan_workflow_not_active",
            message="An active pregnancy-plan workflow is required.",
            status=409,
        )
    return workflow


def _optional_workflow(args: dict[str, Any]) -> dict[str, Any]:
    workflow = args.get("runtime_workflow_context")
    return dict(workflow) if isinstance(workflow, dict) else {}


def _attachment_count(args: dict[str, Any]) -> int:
    value = args.get("runtime_checkup_attachment_count")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _workflow_api_error(exc: ValueError) -> ApiError:
    code = str(exc) or "invalid_pregnancy_plan_workflow_command"
    return ApiError(
        code=code,
        message="The pregnancy-plan command is not valid for the current workflow state.",
        status=409,
    )


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()
