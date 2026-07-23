from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from app.core.errors import ApiError
from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.executor import DEFERRED_AGENT_EVENTS_KEY, ToolHandlerContext
from app.agents.cozymate.actions.plans import (
    MILK_PLAN_CALENDAR_APPEND,
    MILK_PLAN_CALENDAR_REPLACE,
    MILK_PLAN_CREATE_ACTION,
    MILK_SCHEDULE_RESCHEDULE_ACTION,
)
from app.modules.plans.models import PlanTask
from app.modules.plans.milk_plan_builder import (
    MilkPlanDraftError,
    build_milk_plan_draft,
    summarize_pumping_rhythm,
)
from app.modules.plans.milk_schedule_calendar import (
    MilkScheduleCalendarEventError,
    normalize_milk_schedule_calendar_events,
)
from app.modules.plans.service import PlansService
from app.modules.profiles.service import ProfileService
from app.agents.cozymate.actions.records import (
    FEEDING_RECORD_CREATE_ACTION,
    FEEDING_RECORD_DELETE_ACTION,
    GROWTH_RECORD_CREATE_ACTION,
    GROWTH_RECORD_DELETE_ACTION,
    GROWTH_RECORD_UPDATE_ACTION,
    PUMPING_RECORD_CREATE_ACTION,
    PUMPING_RECORD_DELETE_ACTION,
)
from app.modules.records.service import RecordsService
from app.agents.cozymate.tools.milk_analysis_flow import (
    MILK_ANALYSIS_SCHEMA_VERSION,
    MILK_ANALYSIS_WORKFLOW_TYPE,
    MilkAnalysisFlowError,
    advance_milk_analysis_intake,
    build_milk_analysis_assessment,
    initialize_milk_analysis_intake,
    milk_analysis_context_fingerprint,
)

from app.agents.cozymate.tools.milk_schedule_adjustment import MilkScheduleAdjustmentError, build_milk_schedule_preview

from .base import (
    _MILK_ANALYSIS_PLAN_TTL,
    _StandardToolHandler,
    _ToolOperationOutput,
)
from .shared import (
    _datetime_value,
    _deferred_artifact_created_event,
    _feeding_payload,
    _feeding_record_apply_payload,
    _feeding_record_preview_payload,
    _grounded_milk_analysis_answers,
    _growth_payload,
    _growth_record_apply_payload,
    _growth_record_preview_payload,
    _growth_record_update_apply_payload,
    _growth_record_update_preview_payload,
    _growth_update_fields,
    _has_any_growth_measurement,
    _infant_payload,
    _limit,
    _milk_analysis_payload,
    _milk_analysis_window,
    _milk_plan_artifact_payload,
    _milk_plan_preview_payload,
    _milk_schedule_busy_windows,
    _milk_schedule_target_dates,
    _milk_status_payload,
    _milk_trend_payload,
    _optional_date_arg,
    _optional_int,
    _optional_number,
    _optional_uuid_arg,
    _proposal_result,
    _propose_action_reusing_idempotency,
    _pumping_payload,
    _pumping_record_apply_payload,
    _pumping_record_preview_payload,
    _record_delete_apply_payload,
    _record_delete_preview_payload,
    _record_volume_sum,
    _stable_payload_key,
    _string_list,
    _text,
)


class MilkSummaryReadToolHandler(_StandardToolHandler):
    def __init__(self, *, records_service: RecordsService, profile_service: ProfileService) -> None:
        self.records_service = records_service
        self.profile_service = profile_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        days = _limit(context.args.get("days"), default=7, max_limit=30)
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        feedings = await self.records_service.list_feedings(owner_user_id=owner_user_id, limit=limit)
        pumpings = await self.records_service.list_pumpings(owner_user_id=owner_user_id, limit=limit)
        trends = await self.records_service.get_milk_trends(owner_user_id=owner_user_id, days=days, include_today=True)
        infants = await self.profile_service.list_infants(owner_user_id=owner_user_id)
        trend_items = [_milk_trend_payload(item) for item in trends.items]
        output = {
            "window": {
                "days": days,
                "include_today": True,
            },
            "infants": [_infant_payload(infant) for infant in infants[:limit]],
            "recent_feedings": [_feeding_payload(record) for record in feedings],
            "recent_pumpings": [_pumping_payload(record) for record in pumpings],
            "pumping_trends": trend_items,
            "totals": {
                "recent_feeding_volume_ml": _record_volume_sum(feedings, "volume_ml"),
                "recent_pumped_volume_ml": _record_volume_sum(pumpings, "milk_volume_ml"),
                "trend_pumped_volume_ml": round(sum(float(item["pumped_milk_volume_ml"] or 0) for item in trend_items), 2),
                "trend_pumping_count": sum(int(item["pumping_count"] or 0) for item in trend_items),
            },
        }
        return output


class MilkStatusReadToolHandler(_StandardToolHandler):
    def __init__(self, *, records_service: RecordsService, profile_service: ProfileService) -> None:
        self.records_service = records_service
        self.profile_service = profile_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        days = _limit(context.args.get("days"), default=7, max_limit=30)
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        feedings = await self.records_service.list_feedings(owner_user_id=owner_user_id, limit=limit)
        pumpings = await self.records_service.list_pumpings(owner_user_id=owner_user_id, limit=limit)
        trends = await self.records_service.get_milk_trends(owner_user_id=owner_user_id, days=days, include_today=True)
        infants = await self.profile_service.list_infants(owner_user_id=owner_user_id)
        trend_items = [_milk_trend_payload(item) for item in trends.items]
        output = _milk_status_payload(
            days=days,
            limit=limit,
            feedings=feedings,
            pumpings=pumpings,
            trend_items=trend_items,
            infant_count=len(infants),
        )
        return output


class MilkAnalysisReadToolHandler(_StandardToolHandler):
    def __init__(self, *, records_service: RecordsService, profile_service: ProfileService) -> None:
        self.records_service = records_service
        self.profile_service = profile_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        days = _limit(context.args.get("days"), default=7, max_limit=30)
        detail_limit = _limit(context.args.get("limit"), default=8, max_limit=20)
        start_at, end_at = _milk_analysis_window(days=days)
        feedings = await self.records_service.list_feedings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=100,
        )
        pumpings = await self.records_service.list_pumpings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=100,
        )
        growth = await self.records_service.list_growth(owner_user_id=owner_user_id, limit=detail_limit)
        trends = await self.records_service.get_milk_trends(owner_user_id=owner_user_id, days=days, include_today=True)
        infants = await self.profile_service.list_infants(owner_user_id=owner_user_id)
        trend_items = [_milk_trend_payload(item) for item in trends.items]
        status = _milk_status_payload(
            days=days,
            limit=detail_limit,
            feedings=feedings,
            pumpings=pumpings,
            trend_items=trend_items,
            infant_count=len(infants),
        )
        pumping_payloads = [_pumping_payload(record) for record in pumpings]
        output = {
            "window": status["window"],
            "status": status["status"],
            "counts": status["counts"] | {"recent_growth": len(growth)},
            "volumes": status["volumes"],
            "latest": status["latest"],
            "observation_flags": status["observation_flags"],
            "recent_feedings": [_feeding_payload(record) for record in feedings[:detail_limit]],
            "recent_pumpings": pumping_payloads[:detail_limit],
            "pumping_rhythm": summarize_pumping_rhythm(
                pumping_payloads,
                timezone_name=_text(context.args, "runtime_timezone") or "UTC",
            ),
            "recent_growth": [_growth_payload(record) for record in growth],
            "pumping_trends": trend_items,
            "analysis": _milk_analysis_payload(status=status, growth=growth),
        }
        return output


class MilkAnalysisIntakeToolHandler(_StandardToolHandler):
    def __init__(
        self,
        *,
        records_service: RecordsService,
        profile_service: ProfileService,
        runtime_service: AgentRuntimeService,
    ) -> None:
        self.analysis_reader = MilkAnalysisReadToolHandler(
            records_service=records_service,
            profile_service=profile_service,
        )
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        if context.thread_id is None:
            raise ApiError(code="validation_failed", message="A thread is required for milk analysis.", status=422)
        action = _text(context.args, "action") or "start"
        existing = await self.runtime_service.get_latest_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=context.thread_id,
            workflow_type=MILK_ANALYSIS_WORKFLOW_TYPE,
        )
        existing_state = dict(existing.state) if existing is not None and isinstance(existing.state, dict) else {}
        starts_new_analysis = action == "reset" or (
            action == "start" and (existing is None or _text(existing_state, "phase") == "assessment_complete")
        )
        if starts_new_analysis:
            snapshot_result = await self.analysis_reader.execute(context)
            workflow = initialize_milk_analysis_intake(records_snapshot=dict(snapshot_result))
        elif existing is None:
            raise ApiError(code="milk_analysis_not_active", message="Start milk analysis before answering intake questions.", status=409)
        else:
            workflow = existing_state
            if action == "answer":
                answer = _text(context.args, "trusted_current_user_text")
                raw_observed_answers = context.args.get("observed_answers")
                observed_answers = _grounded_milk_analysis_answers(
                    raw_observed_answers,
                    trusted_current_user_text=answer,
                )
                turn_key = _stable_payload_key(
                    "milk-analysis-answer",
                    {"run_id": str(context.run_id), "answer": answer, "observed_answers": observed_answers},
                )
                processed_turn_keys = list(workflow.get("processed_turn_keys") or [])
                if turn_key in processed_turn_keys:
                    action = "resume"
                else:
                    processed_turn_keys.append(turn_key)
                try:
                    if action == "answer":
                        workflow = advance_milk_analysis_intake(
                            workflow,
                            answer="" if observed_answers else answer,
                            answers=observed_answers,
                        )
                except MilkAnalysisFlowError as exc:
                    raise ApiError(code=str(exc), message="Milk analysis intake could not advance.", status=409) from exc
                workflow["processed_turn_keys"] = processed_turn_keys[-16:]
            elif action not in {"start", "resume"}:
                raise ApiError(code="validation_failed", message="Unsupported milk analysis intake action.", status=422)

        current_field = _text(workflow, "current_field")
        phase = _text(workflow, "phase")
        persisted = await self.runtime_service.upsert_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=context.thread_id,
            run_id=context.run_id,
            workflow_type=MILK_ANALYSIS_WORKFLOW_TYPE,
            status="collecting" if phase == "collecting_intake" else "ready",
            schema_version=MILK_ANALYSIS_SCHEMA_VERSION,
            state=workflow,
            active_step=current_field or "ready_to_evaluate",
        )
        progress_value = workflow.get("progress")
        progress = dict(cast(dict[str, Any], progress_value)) if isinstance(progress_value, dict) else {}
        output = {
            "status": "milk_analysis_intake_collecting" if current_field else "milk_analysis_ready_to_evaluate",
            "workflow_state_id": str(persisted.id),
            "workflow_phase": phase,
            "current_field": current_field or None,
            "next_question": _text(workflow, "next_question"),
            "progress": progress,
            "can_evaluate": not current_field,
        }
        return output


class MilkAnalysisEvaluateToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        if context.thread_id is None:
            raise ApiError(code="validation_failed", message="A thread is required for milk analysis.", status=422)
        existing = await self.runtime_service.get_latest_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=context.thread_id,
            workflow_type=MILK_ANALYSIS_WORKFLOW_TYPE,
        )
        if existing is None:
            raise ApiError(code="milk_analysis_not_active", message="Complete milk analysis intake first.", status=409)
        existing_state = dict(existing.state) if isinstance(existing.state, dict) else {}
        replay_artifact_id = _text(existing_state, "evaluation_artifact_id")
        raw_existing_assessment = existing_state.get("assessment")
        existing_assessment: dict[str, Any] = (
            dict(cast(dict[str, Any], raw_existing_assessment)) if isinstance(raw_existing_assessment, dict) else {}
        )
        if _text(existing_state, "phase") == "assessment_complete" and replay_artifact_id and existing_assessment:
            raw_replay_decision = existing_assessment.get("plan_decision")
            replay_decision: dict[str, Any] = (
                dict(cast(dict[str, Any], raw_replay_decision)) if isinstance(raw_replay_decision, dict) else {}
            )
            return {
                    "status": "milk_analysis_completed",
                    "replayed": True,
                    "workflow_state_id": str(existing.id),
                    "artifact_id": replay_artifact_id,
                    "artifact_type": "milk_analysis_card",
                    "can_start_plan": replay_decision.get("can_start_plan") is True,
                    "recommended_direction": replay_decision.get("recommended_direction"),
                    "reason": replay_decision.get("reason"),
                }
        try:
            assessment = build_milk_analysis_assessment(existing_state)
        except MilkAnalysisFlowError as exc:
            raise ApiError(code=str(exc), message="Complete milk analysis intake first.", status=409) from exc
        assessment = {
            **assessment,
            "valid_until": (datetime.now(timezone.utc) + _MILK_ANALYSIS_PLAN_TTL).isoformat(),
        }
        card = dict(assessment["card"])
        artifact = await self.runtime_service.create_artifact(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type="milk_analysis_card",
            schema_version="v1",
            status="created",
            payload=card,
            emit_event=False,
        )
        state = {
            **existing_state,
            "phase": "assessment_complete",
            "assessment": assessment,
            "evaluation_artifact_id": str(artifact.id),
        }
        persisted = await self.runtime_service.upsert_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=context.thread_id,
            run_id=context.run_id,
            workflow_type=MILK_ANALYSIS_WORKFLOW_TYPE,
            status="ready",
            schema_version=MILK_ANALYSIS_SCHEMA_VERSION,
            state=state,
            active_step="assessment_complete",
        )
        decision = assessment["plan_decision"]
        output = {
            "status": "milk_analysis_completed",
            "workflow_state_id": str(persisted.id),
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "can_start_plan": decision["can_start_plan"],
            "recommended_direction": decision["recommended_direction"],
            "reason": decision["reason"],
            DEFERRED_AGENT_EVENTS_KEY: [_deferred_artifact_created_event(artifact)],
        }
        return output


class GrowthRecordsReadToolHandler(_StandardToolHandler):
    def __init__(self, *, records_service: RecordsService) -> None:
        self.records_service = records_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        infant_id = _optional_uuid_arg(context.args, "infant_id")
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        growth = await self.records_service.list_growth(
            owner_user_id=context.actor.user_id,
            infant_id=infant_id,
            limit=limit,
        )
        output: dict[str, Any] = {
            "growth": [_growth_payload(record) for record in growth],
            "count": len(growth),
            "infant_id": str(infant_id) if infant_id is not None else "",
        }
        return output


class FeedingRecordProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _feeding_record_apply_payload(context.args)
        if apply_payload.get("volume_ml") is None and apply_payload.get("duration_seconds") is None:
            raise ApiError(code="validation_failed", message="volume_ml or duration_seconds is required.", status=422)
        preview_payload = _feeding_record_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=FEEDING_RECORD_CREATE_ACTION,
            target_type="feeding_record",
            side_effect_level="low",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:feeding-record",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PumpingRecordProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _pumping_record_apply_payload(context.args)
        if apply_payload.get("milk_volume_ml") is None and apply_payload.get("duration_seconds") is None:
            raise ApiError(code="validation_failed", message="milk_volume_ml or duration_seconds is required.", status=422)
        preview_payload = _pumping_record_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PUMPING_RECORD_CREATE_ACTION,
            target_type="pumping_record",
            side_effect_level="low",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:pumping-record",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class FeedingRecordDeleteProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _record_delete_apply_payload(context.args)
        record_id = _text(apply_payload, "record_id")
        if not record_id:
            raise ApiError(code="validation_failed", message="record_id is required.", status=422)
        preview_payload = _record_delete_preview_payload(apply_payload, record_type="feeding_record")
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=FEEDING_RECORD_DELETE_ACTION,
            target_type="feeding_record",
            target_id=record_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:feeding-record-delete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PumpingRecordDeleteProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _record_delete_apply_payload(context.args)
        record_id = _text(apply_payload, "record_id")
        if not record_id:
            raise ApiError(code="validation_failed", message="record_id is required.", status=422)
        preview_payload = _record_delete_preview_payload(apply_payload, record_type="pumping_record")
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PUMPING_RECORD_DELETE_ACTION,
            target_type="pumping_record",
            target_id=record_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:pumping-record-delete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class GrowthRecordProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _growth_record_apply_payload(context.args)
        if not _text(apply_payload, "measured_at"):
            raise ApiError(code="validation_failed", message="measured_at is required.", status=422)
        if not _has_any_growth_measurement(apply_payload):
            raise ApiError(code="validation_failed", message="height_cm, weight_kg, or head_cm is required.", status=422)
        preview_payload = _growth_record_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=GROWTH_RECORD_CREATE_ACTION,
            target_type="growth_record",
            side_effect_level="low",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:growth-record",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class GrowthRecordUpdateProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _growth_record_update_apply_payload(context.args)
        record_id = _text(apply_payload, "record_id")
        if not record_id:
            raise ApiError(code="validation_failed", message="record_id is required.", status=422)
        if not _growth_update_fields(apply_payload):
            raise ApiError(code="validation_failed", message="At least one growth update field is required.", status=422)
        preview_payload = _growth_record_update_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=GROWTH_RECORD_UPDATE_ACTION,
            target_type="growth_record",
            target_id=record_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:growth-record-update",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class GrowthRecordDeleteProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _record_delete_apply_payload(context.args)
        record_id = _text(apply_payload, "record_id")
        if not record_id:
            raise ApiError(code="validation_failed", message="record_id is required.", status=422)
        preview_payload = _record_delete_preview_payload(apply_payload, record_type="growth_record")
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=GROWTH_RECORD_DELETE_ACTION,
            target_type="growth_record",
            target_id=record_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:growth-record-delete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class MilkPlanProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService, plans_service: PlansService) -> None:
        self.runtime_service = runtime_service
        self.plans_service = plans_service

    async def execute(self, context: ToolHandlerContext) -> _ToolOperationOutput:
        requested_direction = _text(context.args, "direction")
        if not requested_direction:
            raise ApiError(code="validation_failed", message="direction is required.", status=422)
        if context.thread_id is None:
            raise ApiError(code="validation_failed", message="A thread is required for a milk plan.", status=422)
        workflow = await self.runtime_service.get_latest_workflow_state(
            owner_user_id=context.actor.user_id,
            thread_id=context.thread_id,
            workflow_type=MILK_ANALYSIS_WORKFLOW_TYPE,
        )
        if workflow is None:
            raise ApiError(
                code="milk_analysis_required_before_plan",
                message="Complete the durable milk analysis before creating a plan.",
                status=409,
            )
        state = dict(workflow.state) if workflow is not None and isinstance(workflow.state, dict) else {}
        raw_assessment = state.get("assessment")
        assessment: dict[str, Any] = dict(cast(dict[str, Any], raw_assessment)) if isinstance(raw_assessment, dict) else {}
        raw_decision = assessment.get("plan_decision")
        decision: dict[str, Any] = dict(cast(dict[str, Any], raw_decision)) if isinstance(raw_decision, dict) else {}
        fingerprint = _text(assessment, "analysis_context_fingerprint")
        raw_analysis_context = assessment.get("analysis_context")
        analysis_context: dict[str, Any] = (
            dict(cast(dict[str, Any], raw_analysis_context)) if isinstance(raw_analysis_context, dict) else {}
        )
        expected_fingerprint = milk_analysis_context_fingerprint(analysis_context) if analysis_context else ""
        if _text(state, "phase") != "assessment_complete" or not fingerprint:
            raise ApiError(
                code="milk_analysis_required_before_plan",
                message="Complete the durable milk analysis before creating a plan.",
                status=409,
            )
        if not expected_fingerprint or fingerprint != expected_fingerprint:
            raise ApiError(
                code="milk_analysis_fingerprint_mismatch",
                message="The latest milk analysis context changed. Evaluate it again before creating a plan.",
                status=409,
            )
        valid_until = _datetime_value(assessment.get("valid_until"))
        if valid_until is None or valid_until <= datetime.now(timezone.utc):
            raise ApiError(
                code="milk_analysis_expired_before_plan",
                message="The latest milk analysis expired. Refresh it before creating a plan.",
                status=409,
            )
        if decision.get("can_start_plan") is not True:
            raise ApiError(code="milk_plan_not_eligible", message="The latest milk analysis does not allow a plan.", status=409)
        recommended_direction = _text(decision, "recommended_direction")
        if requested_direction and requested_direction != recommended_direction:
            raise ApiError(
                code="milk_plan_direction_mismatch",
                message="The proposed direction does not match the latest milk analysis.",
                status=409,
            )
        try:
            draft = build_milk_plan_draft(
                analysis_context=analysis_context,
                direction=requested_direction,
                timezone_name=_text(context.args, "runtime_timezone") or "UTC",
                start_date=_text(context.args, "start_date"),
                days=_optional_int(context.args, "days") or 7,
                target_daily_ml=_optional_number(context.args, "target_daily_ml"),
                preferred_pumping_times=_string_list(context.args.get("preferred_pumping_times")),
                today=_optional_date_arg(context.args, "runtime_local_date"),
            )
        except MilkPlanDraftError as exc:
            raise ApiError(code="validation_failed", message=str(exc), status=422) from exc
        plan_payload = dict(cast(dict[str, Any], draft["payload"]))
        start_date = date.fromisoformat(str(plan_payload["start_date"]))
        end_date = start_date + timedelta(days=int(plan_payload["days"]) - 1)
        existing_tasks = await self.plans_service.list_future_milk_plan_tasks(
            owner_user_id=context.actor.user_id,
            start_date=start_date,
            end_date=end_date,
        )
        calendar_write_strategy = _text(context.args, "calendar_write_strategy")
        allowed_strategies = [MILK_PLAN_CALENDAR_APPEND, MILK_PLAN_CALENDAR_REPLACE]
        if calendar_write_strategy and calendar_write_strategy not in allowed_strategies:
            raise ApiError(code="validation_failed", message="calendar_write_strategy is invalid.", status=422)
        if existing_tasks and not calendar_write_strategy:
            return {
                "status": "milk_plan_calendar_strategy_required",
                "existing_future_task_count": len(existing_tasks),
                "allowed_strategies": allowed_strategies,
                "question": "未来日程已有奶量计划任务。你希望把新计划追加进去，还是替换这些未来未完成任务？",
            }
        calendar_write_strategy = calendar_write_strategy or MILK_PLAN_CALENDAR_APPEND
        apply_payload = {
            "title": _text(draft, "title"),
            "summary": _text(draft, "summary"),
            "calendar_write_strategy": calendar_write_strategy,
            "expected_replaced_task_ids": [str(task.id) for task in existing_tasks],
            "payload": {
                **plan_payload,
                "analysis_context_fingerprint": fingerprint,
                "analysis_workflow_state_id": str(workflow.id),
            },
        }
        preview_payload = {
            **_milk_plan_preview_payload(apply_payload),
            "existing_future_task_count": len(existing_tasks),
        }
        action = await _propose_action_reusing_idempotency(
            self.runtime_service,
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=MILK_PLAN_CREATE_ACTION,
            target_type="plan",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            expires_at=valid_until,
            idempotency_key=_text(context.args, "idempotency_key")
            or _stable_payload_key(
                "milk-plan",
                {"owner": str(context.actor.user_id), "thread": str(context.thread_id), "payload": apply_payload},
            ),
        )
        payload = {**_milk_plan_artifact_payload(apply_payload), "action_id": str(action.id)}
        artifact = await self.runtime_service.create_artifact(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type="milk_plan_preview",
            schema_version="v1",
            status="created",
            payload=payload,
            emit_event=False,
        )
        reminders = payload.get("reminders")
        return {
            **_proposal_result(action=action, preview_payload=preview_payload),
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "status": artifact.status,
            "title": _text(payload, "title"),
            "summary": _text(payload, "summary"),
            "task_count": int(payload.get("scheduled_task_count") or 0),
            "reminder_count": len(reminders) if isinstance(reminders, list) else 0,
            DEFERRED_AGENT_EVENTS_KEY: [_deferred_artifact_created_event(artifact)],
        }


class MilkScheduleRescheduleProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService, plans_service: PlansService) -> None:
        self.runtime_service = runtime_service
        self.plans_service = plans_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        plan_id = _optional_uuid_arg(context.args, "plan_id")
        if plan_id is None:
            raise ApiError(code="validation_failed", message="plan_id is required.", status=422)
        target_dates = _milk_schedule_target_dates(context.args)
        try:
            calendar_events = normalize_milk_schedule_calendar_events(
                context.args.get("calendar_events"),
                allowed_dates=target_dates,
            )
        except MilkScheduleCalendarEventError as exc:
            raise ApiError(code=str(exc), message="A calendar event could not be created.", status=422) from exc
        plan = await self.plans_service.get_plan(owner_user_id=context.actor.user_id, plan_id=plan_id)
        if plan.plan_type != "milk_management":
            raise ApiError(code="validation_failed", message="Plan is not a milk-management plan.", status=422)
        tasks = await self.plans_service.list_tasks_for_plan(
            owner_user_id=context.actor.user_id,
            plan_id=plan_id,
            task_dates=target_dates,
            status="pending",
            limit=200,
        )
        fixed_tasks: list[PlanTask] = []
        for target_date in target_dates:
            fixed_tasks.extend(
                await self.plans_service.list_tasks(
                    owner_user_id=context.actor.user_id,
                    task_date=target_date,
                    status="pending",
                    limit=100,
                )
            )
        try:
            preview = build_milk_schedule_preview(
                plan_id=plan_id,
                tasks=tasks,
                fixed_tasks=fixed_tasks,
                target_dates=target_dates,
                busy_windows=_milk_schedule_busy_windows(
                    busy_windows=context.args.get("busy_windows"),
                    calendar_events=calendar_events,
                ),
                min_gap_minutes=int(context.args.get("min_gap_minutes") or 90),
                default_duration_minutes=int(context.args.get("default_duration_minutes") or 30),
            )
        except (MilkScheduleAdjustmentError, TypeError, ValueError) as exc:
            code = str(exc) if isinstance(exc, MilkScheduleAdjustmentError) else "invalid_milk_schedule_adjustment"
            raise ApiError(code=code, message="A milk schedule preview could not be created.", status=422) from exc
        preview = {
            **preview,
            "affected_dates": sorted(
                set(preview["affected_dates"]) | {str(event["date"]) for event in calendar_events}
            ),
            "calendar_events": calendar_events,
            "calendar_event_count": len(calendar_events),
        }
        if not preview["updates"] and not calendar_events:
            return {
                "status": "milk_schedule_no_changes",
                "plan_id": str(plan_id),
                "conflict_count": preview["conflict_count"],
                "updated_count": 0,
                "affected_dates": [],
            }
        action = await _propose_action_reusing_idempotency(
            self.runtime_service,
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=MILK_SCHEDULE_RESCHEDULE_ACTION,
            target_type="plan",
            target_id=str(plan_id),
            side_effect_level="medium",
            preview_payload=preview,
            apply_payload={
                "plan_id": str(plan_id),
                "updates": preview["updates"],
                "affected_dates": preview["affected_dates"],
                "calendar_events": calendar_events,
            },
            idempotency_key=_text(context.args, "idempotency_key")
            or _stable_payload_key(
                "milk-schedule-reschedule",
                {
                    "owner": str(context.actor.user_id),
                    "plan_id": str(plan_id),
                    "updates": preview["updates"],
                    "calendar_events": calendar_events,
                },
            ),
        )
        artifact_payload = {**preview, "action_id": str(action.id), "title": "奶量计划日程调整预览"}
        artifact = await self.runtime_service.create_artifact(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type="milk_schedule_reschedule_preview",
            schema_version="v1",
            status="created",
            payload=artifact_payload,
            emit_event=False,
        )
        return {
            **_proposal_result(action=action, preview_payload=preview),
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "plan_id": str(plan_id),
            "conflict_count": preview["conflict_count"],
            "updated_count": preview["updated_count"],
            "calendar_event_count": len(calendar_events),
            "affected_dates": preview["affected_dates"],
            DEFERRED_AGENT_EVENTS_KEY: [_deferred_artifact_created_event(artifact)],
        }
