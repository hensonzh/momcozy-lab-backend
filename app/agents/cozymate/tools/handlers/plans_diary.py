from __future__ import annotations

from typing import Any

from app.core.errors import ApiError
from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.executor import DEFERRED_AGENT_EVENTS_KEY, ToolHandlerContext
from app.agents.cozymate.actions.diary import PREGNANCY_DIARY_DELETE_ACTION, PREGNANCY_DIARY_SAVE_ACTION
from app.modules.diary.service import DiaryService
from app.agents.cozymate.actions.notifications import MILK_REMINDER_CREATE_ACTION
from app.agents.cozymate.actions.plans import (
    PLAN_TASK_COMPLETE_ACTION,
    PLAN_TASK_CREATE_ACTION,
    PLAN_TASK_DELETE_ACTION,
    PLAN_TASK_UPDATE_ACTION,
    PLAN_DELETE_ACTION,
    PREGNANCY_PLAN_CREATE_ACTION,
    PREGNANCY_PLAN_TODO_UPDATE_ACTION,
)
from app.modules.plans.service import PlansService
from app.modules.profiles.service import ProfileService
from app.agents.cozymate.tools.birth_preparation_artifacts import (
    artifact_record_from_birth_preparation_result,
)
from app.agents.cozymate.tools.pregnancy_plan_flow import (
    PREGNANCY_PLAN_INTAKE_FORM_ID,
    PregnancyPlanPhase,
    build_pregnancy_plan_result,
    pregnancy_plan_urgent_signal_ids,
)


from .base import (
    _StandardToolHandler,
    _ToolOperationOutput,
)
from .shared import (
    _date_iso,
    _deferred_artifact_created_event,
    _diary_delete_confirmation_evidence_is_trusted,
    _diary_entry_date,
    _diary_payload,
    _dict,
    _existing_pregnancy_plan_result,
    _failed_action_result,
    _limit,
    _milk_reminder_apply_payload,
    _milk_reminder_preview_payload,
    _optional_date_arg,
    _plan_delete_apply_payload,
    _plan_delete_preview_payload,
    _plan_payload,
    _plan_task_complete_apply_payload,
    _plan_task_complete_preview_payload,
    _plan_task_create_apply_payload,
    _plan_task_create_preview_payload,
    _plan_task_delete_apply_payload,
    _plan_task_delete_preview_payload,
    _plan_task_update_apply_payload,
    _plan_task_update_fields,
    _plan_task_update_preview_payload,
    _pregnancy_plan_action_idempotency_key,
    _pregnancy_plan_apply_payload,
    _pregnancy_plan_context_payload,
    _pregnancy_plan_preview_payload,
    _pregnancy_plan_todo_apply_payload,
    _pregnancy_plan_todo_preview_payload,
    _pregnancy_plan_urgent_result,
    _profile_payload,
    _proposal_result,
    _propose_action_reusing_idempotency,
    _require_pregnancy_plan_thread_id,
    _required_diary_content,
    _task_payload,
    _text,
    _upsert_pregnancy_plan_workflow,
)


class PlansCurrentReadToolHandler(_StandardToolHandler):
    def __init__(self, *, plans_service: PlansService) -> None:
        self.plans_service = plans_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        plans = await self.plans_service.list_plans(owner_user_id=owner_user_id, status="active", limit=limit)
        tasks = await self.plans_service.list_tasks(owner_user_id=owner_user_id, limit=limit)
        output: dict[str, Any] = {
            "plans": [_plan_payload(plan) for plan in plans],
            "tasks": [_task_payload(task) for task in tasks],
            "counts": {
                "plans": len(plans),
                "tasks": len(tasks),
            },
        }
        return output


class PlansCalendarReadToolHandler(_StandardToolHandler):
    def __init__(self, *, plans_service: PlansService) -> None:
        self.plans_service = plans_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        limit = _limit(context.args.get("limit"), default=10, max_limit=50)
        task_date = _optional_date_arg(context.args, "task_date")
        status = _text(context.args, "status") or None
        tasks = await self.plans_service.list_tasks(
            owner_user_id=context.actor.user_id,
            task_date=task_date,
            status=status,
            limit=limit,
        )
        output: dict[str, Any] = {
            "tasks": [_task_payload(task) for task in tasks],
            "count": len(tasks),
            "filters": {
                "task_date": _date_iso(task_date),
                "status": status or "",
                "limit": limit,
            },
        }
        return output


class PregnancyDiaryQueryToolHandler(_StandardToolHandler):
    def __init__(self, *, diary_service: DiaryService) -> None:
        self.diary_service = diary_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        if _text(context.args, "entry_date"):
            return await self._read(context)
        return await self._list(context)

    async def _read(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        entry_date = _diary_entry_date(context.args)
        try:
            entry = await self.diary_service.get_entry(owner_user_id=owner_user_id, entry_date=entry_date)
        except ApiError as exc:
            if exc.code != "not_found":
                raise
            output = {
                "status": "entry_not_found",
                "side_effect_performed": False,
                "entry_date": entry_date.isoformat(),
                "entry": None,
            }
            return output
        output = {
            "status": "entry_read",
            "side_effect_performed": False,
            "entry_date": entry_date.isoformat(),
            "entry": _diary_payload(entry, include_content=True),
        }
        return output

    async def _list(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        start_date = _optional_date_arg(context.args, "start_date")
        end_date = _optional_date_arg(context.args, "end_date")
        limit = _limit(context.args.get("limit"), default=7, max_limit=30)
        entries = await self.diary_service.list_entries(
            owner_user_id=owner_user_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        output = {
            "status": "entries_read",
            "side_effect_performed": False,
            "entries": [_diary_payload(entry, include_content=False) for entry in entries],
            "count": len(entries),
            "filters": {
                "start_date": _date_iso(start_date),
                "end_date": _date_iso(end_date),
                "limit": limit,
            },
        }
        return output


class PregnancyDiarySaveToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        operation = _text(context.args, "operation")
        if operation not in {"create", "update"}:
            raise ApiError(code="validation_failed", message="operation must be create or update.", status=422)
        entry_date = _diary_entry_date(context.args)
        content = _required_diary_content(context.args)
        apply_payload = {
            "operation": operation,
            "entry_date": entry_date.isoformat(),
            "content": content,
        }
        preview_payload = {
            "operation": operation,
            "entry_date": entry_date.isoformat(),
            "content_length": len(content),
        }
        action = await _propose_action_reusing_idempotency(
            self.runtime_service,
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PREGNANCY_DIARY_SAVE_ACTION,
            target_type="pregnancy_diary_entry",
            target_id=entry_date.isoformat(),
            side_effect_level="low",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key")
            or f"{context.run_id}:{context.call_id}:pregnancy-diary-save",
        )
        output = _proposal_result(action=action, preview_payload=preview_payload)
        output.update({"operation": operation, "entry_date": entry_date.isoformat()})
        if output["write_succeeded"]:
            output.update({"status": "entry_saved", "side_effect_performed": True})
        return output


class PregnancyDiaryDeleteToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        entry_date = _diary_entry_date(context.args)
        if not _diary_delete_confirmation_evidence_is_trusted(context.args):
            return {
                "status": "needs_delete_confirmation",
                "side_effect_performed": False,
                "entry_date": entry_date.isoformat(),
            }
        apply_payload = {"entry_date": entry_date.isoformat()}
        preview_payload = {
            "operation": "delete",
            "entry_date": entry_date.isoformat(),
        }
        action = await _propose_action_reusing_idempotency(
            self.runtime_service,
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PREGNANCY_DIARY_DELETE_ACTION,
            target_type="pregnancy_diary_entry",
            target_id=entry_date.isoformat(),
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key")
            or f"{context.run_id}:{context.call_id}:pregnancy-diary-delete",
        )
        output = _proposal_result(action=action, preview_payload=preview_payload)
        output.update({"operation": "delete", "entry_date": entry_date.isoformat()})
        if output["write_succeeded"]:
            output.update({"status": "entry_deleted", "side_effect_performed": True})
        return output


class PregnancyPlanContextReadToolHandler(_StandardToolHandler):
    def __init__(
        self,
        *,
        profile_service: ProfileService,
        plans_service: PlansService,
    ) -> None:
        self.profile_service = profile_service
        self.plans_service = plans_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        profile = await self.profile_service.get_user_profile(user_id=owner_user_id)
        plans = await self.plans_service.list_plans(
            owner_user_id=owner_user_id,
            plan_type="pregnancy",
            status="active",
            limit=limit,
        )
        tasks = await self.plans_service.list_tasks(owner_user_id=owner_user_id, limit=limit)
        output: dict[str, Any] = {
            "profile": _profile_payload(profile=profile, actor_user_id=owner_user_id),
            "plans": [_pregnancy_plan_context_payload(plan) for plan in plans],
            "tasks": [_task_payload(task) for task in tasks],
            "counts": {
                "plans": len(plans),
                "tasks": len(tasks),
            },
        }
        return output


class PregnancyPlanProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> _ToolOperationOutput:
        _require_pregnancy_plan_thread_id(context)
        urgent_signal_ids = pregnancy_plan_urgent_signal_ids(
            {
                "additional_info": "\n".join(
                    value
                    for value in (
                        _text(context.args, "trusted_current_user_text"),
                        _text(context.args, "additional_info"),
                    )
                    if value
                )
            }
        )
        if urgent_signal_ids:
            return _pregnancy_plan_urgent_result(urgent_signal_ids)
        runtime_plan_context = _dict(context.args, "runtime_plan_context")
        existing = _existing_pregnancy_plan_result(runtime_plan_context)
        if existing is not None:
            return existing
        workflow_phase = _text(runtime_plan_context, "workflow_phase")
        if workflow_phase == PregnancyPlanPhase.COLLECTING_INTAKE.value or not workflow_phase:
            return {"status": "needs_pregnancy_plan_intake"}
        if workflow_phase != PregnancyPlanPhase.READY_TO_GENERATE.value:
            return {"status": "pregnancy_plan_intake_in_progress", "next_step": workflow_phase}
        apply_payload = _pregnancy_plan_apply_payload(context.args)
        title = _text(apply_payload, "title")
        if not title:
            raise ApiError(code="validation_failed", message="title is required.", status=422)
        plan_payload = _dict(apply_payload, "payload")
        plan_result = build_pregnancy_plan_result(_dict(plan_payload, "plan_context"))
        artifact_record = artifact_record_from_birth_preparation_result(plan_result)
        if artifact_record is None:
            raise ApiError(code="tool_failed", message="Pregnancy plan preview could not be created.", status=500)
        plan_payload["card"] = _dict(plan_result, "card")
        apply_payload["payload"] = plan_payload
        preview_payload = _pregnancy_plan_preview_payload(apply_payload)
        action_kwargs: dict[str, Any] = {
            "owner_user_id": context.actor.user_id,
            "run_id": context.run_id,
            "action_type": PREGNANCY_PLAN_CREATE_ACTION,
            "target_type": "plan",
            "side_effect_level": "medium",
            "preview_payload": preview_payload,
            "apply_payload": apply_payload,
            "idempotency_key": _pregnancy_plan_action_idempotency_key(
                apply_payload=apply_payload,
                run_id=context.run_id,
            ),
        }
        propose_once = getattr(self.runtime_service, "propose_action_once", None)
        if callable(propose_once):
            action, action_created = await propose_once(**action_kwargs)
        else:
            action = await self.runtime_service.propose_action(**action_kwargs)
            action_created = True
        if str(getattr(action, "status", "") or "") == "failed":
            return _failed_action_result(action=action, preview_payload=preview_payload)
        if not action_created:
            return _proposal_result(action=action, preview_payload=dict(action.preview_payload or preview_payload))
        artifact_payload = {**dict(artifact_record["payload"]), "action_id": str(action.id)}
        artifact = await self.runtime_service.create_artifact(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type=str(artifact_record["artifact_type"]),
            schema_version=str(artifact_record["schema_version"]),
            status="created",
            payload=artifact_payload,
            emit_event=False,
        )
        workflow = _dict(context.args, "runtime_workflow_context")
        workflow.update(
            {
                "phase": PregnancyPlanPhase.READY_TO_GENERATE.value,
                "consumed_by_action_id": str(action.id),
                "source_form_artifact_id": _text(runtime_plan_context, "source_form_artifact_id"),
                "source_form_submission_id": _text(runtime_plan_context, "source_form_submission_id"),
                "form_id": PREGNANCY_PLAN_INTAKE_FORM_ID,
            }
        )
        await _upsert_pregnancy_plan_workflow(
            runtime_service=self.runtime_service,
            context=context,
            workflow=workflow,
        )
        return {
            **_proposal_result(action=action, preview_payload=preview_payload),
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "status": artifact.status,
            DEFERRED_AGENT_EVENTS_KEY: [_deferred_artifact_created_event(artifact)],
        }


class PlanTaskCreateProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _plan_task_create_apply_payload(context.args)
        title = _text(apply_payload, "title")
        if not title:
            raise ApiError(code="validation_failed", message="title is required.", status=422)
        preview_payload = _plan_task_create_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_TASK_CREATE_ACTION,
            target_type="plan_task",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:plan-task-create",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PlanTaskCompleteProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _plan_task_complete_apply_payload(context.args)
        task_id = _text(apply_payload, "task_id")
        if not task_id:
            raise ApiError(code="validation_failed", message="task_id is required.", status=422)
        preview_payload = _plan_task_complete_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_TASK_COMPLETE_ACTION,
            target_type="plan_task",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:plan-task-complete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PregnancyPlanTodoUpdateProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _pregnancy_plan_todo_apply_payload(context.args)
        for key in ("plan_id", "item_id"):
            if not _text(apply_payload, key):
                raise ApiError(code="validation_failed", message=f"{key} is required.", status=422)
        expected_version = apply_payload.get("expected_version")
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise ApiError(code="validation_failed", message="expected_version must be a positive integer.", status=422)
        preview_payload = _pregnancy_plan_todo_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PREGNANCY_PLAN_TODO_UPDATE_ACTION,
            target_type="plan",
            target_id=_text(apply_payload, "plan_id"),
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key")
            or f"{context.run_id}:{context.call_id}:pregnancy-plan-todo",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PlanTaskUpdateProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _plan_task_update_apply_payload(context.args)
        task_id = _text(apply_payload, "task_id")
        if not task_id:
            raise ApiError(code="validation_failed", message="task_id is required.", status=422)
        if not _plan_task_update_fields(apply_payload):
            raise ApiError(code="validation_failed", message="At least one task update field is required.", status=422)
        preview_payload = _plan_task_update_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_TASK_UPDATE_ACTION,
            target_type="plan_task",
            target_id=task_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:plan-task-update",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PlanTaskDeleteProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _plan_task_delete_apply_payload(context.args)
        task_id = _text(apply_payload, "task_id")
        if not task_id:
            raise ApiError(code="validation_failed", message="task_id is required.", status=422)
        preview_payload = _plan_task_delete_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_TASK_DELETE_ACTION,
            target_type="plan_task",
            target_id=task_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:plan-task-delete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PlanDeleteProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _plan_delete_apply_payload(context.args)
        plan_id = _text(apply_payload, "plan_id")
        if not plan_id:
            raise ApiError(code="validation_failed", message="plan_id is required.", status=422)
        preview_payload = _plan_delete_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_DELETE_ACTION,
            target_type="plan",
            target_id=plan_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:plan-delete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class MilkReminderProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _milk_reminder_apply_payload(context.args)
        title = _text(apply_payload, "title")
        if not title:
            raise ApiError(code="validation_failed", message="title is required.", status=422)
        preview_payload = _milk_reminder_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=MILK_REMINDER_CREATE_ACTION,
            target_type="notification",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:milk-reminder",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)
