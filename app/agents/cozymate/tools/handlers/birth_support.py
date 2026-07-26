from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

from app.core.errors import ApiError
from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.executor import DEFERRED_AGENT_EVENTS_KEY, ToolHandlerContext
from app.agents.cozymate.actions.hospital_bag import HOSPITAL_BAG_CART_UPDATE_ACTION
from app.agents.cozymate.tools.birth_preparation_artifacts import (
    artifact_record_from_birth_preparation_result,
    create_birth_preparation_artifact_result,
    hospital_bag_cart_update_result,
    invalid_hospital_bag_required_fields,
    missing_hospital_bag_required_fields,
)
from app.agents.cozymate.tools.hospital_bag_flow import (
    HOSPITAL_BAG_FORM_ID,
    HOSPITAL_BAG_URGENT_RESPONSE,
)
from app.agents.cozymate.tools.pump_models import PumpModelsReferenceService
from app.agents.cozymate.tools.pregnancy_plan_flow import (
    PREGNANCY_PLAN_INTAKE_FORM_ID,
    PregnancyPlanPhase,
    advance_pregnancy_plan_workflow,
    build_pregnancy_plan_intake_form,
    collecting_intake_snapshot,
    initialize_pregnancy_plan_workflow,
    invalid_pregnancy_plan_intake_fields,
    missing_pregnancy_plan_intake_fields,
    pregnancy_plan_current_followup,
    pregnancy_plan_urgent_signal_ids,
    pregnancy_plan_workflow_context,
)


from .base import (
    _StandardToolHandler,
    _ToolOperationOutput,
)
from .shared import (
    _deferred_artifact_created_event,
    _dict,
    _hospital_bag_cart_apply_payload,
    _hospital_bag_cart_idempotency_key,
    _hospital_bag_cart_preview_payload,
    _interrupt_pregnancy_plan_for_safety,
    _ibclc_consult_card_payload,
    _ibclc_consult_consent,
    _optional_int,
    _pregnancy_plan_urgent_result,
    _pregnancy_plan_workflow_result,
    _proposal_result,
    _require_hospital_bag_thread_id,
    _require_pregnancy_plan_thread_id,
    _support_ticket_confirmation_message,
    _support_ticket_creation_confirmed,
    _support_ticket_draft,
    _support_ticket_followup_message,
    _text,
    _upsert_hospital_bag_workflow,
    _upsert_pregnancy_plan_workflow,
)

class SupportTicketProposeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        ticket = _support_ticket_draft(context)
        issue_summary = _text(ticket, "issue_summary")
        if not issue_summary:
            raise ApiError(code="validation_failed", message="issue_summary is required.", status=422)

        if not _support_ticket_creation_confirmed(context.args):
            message = _support_ticket_confirmation_message(context.args)
            return {
                "tool_name": context.tool_name,
                "status": "needs_support_ticket_confirmation",
                "requires_confirmation": True,
                "confirmation_question": message,
                "assistant_followup": {"message": message},
            }

        followup = {"message": _support_ticket_followup_message(ticket)}
        payload = {
            "tool_name": context.tool_name,
            "ticket": ticket,
            "submit_label": "确认并提交",
            "assistant_followup": followup,
        }
        artifact, created = await self.runtime_service.create_artifact_once(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type="support_ticket_draft",
            schema_version="1.0",
            status="created",
            payload=payload,
            emit_event=False,
        )
        artifact_payload = artifact.payload if isinstance(artifact.payload, dict) else payload
        artifact_ticket = artifact_payload.get("ticket")
        result = {
            "tool_name": context.tool_name,
            "status": "ticket_draft_created",
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "schema_version": artifact.schema_version,
            "reused": not created,
            "ticket": dict(artifact_ticket) if isinstance(artifact_ticket, dict) else ticket,
            "submit_label": _text(artifact_payload, "submit_label") or "确认并提交",
            "assistant_followup": artifact_payload.get("assistant_followup", followup),
        }
        if created:
            result[DEFERRED_AGENT_EVENTS_KEY] = [_deferred_artifact_created_event(artifact)]
        return result


class HospitalBagCartUpdateProposeToolHandler(_StandardToolHandler):
    def __init__(
        self,
        *,
        runtime_service: AgentRuntimeService,
        pump_models_service: PumpModelsReferenceService | None = None,
    ) -> None:
        self.runtime_service = runtime_service
        self.pump_models_service = pump_models_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        operation = _text(context.args, "operation")
        handler_args = dict(context.args)
        artifact_result: dict[str, Any] = {}
        cart_update = handler_args.get("cart_update")
        if not isinstance(cart_update, dict) or not cart_update:
            if not operation:
                raise ApiError(code="validation_failed", message="operation is required.", status=422)
            pump_products = await _pump_products_for_cart_action(
                args=handler_args,
                service=self.pump_models_service,
            )
            artifact_result = hospital_bag_cart_update_result(
                handler_args,
                pump_products=pump_products,
            )
            if _text(artifact_result, "status") not in {"cart_updated"}:
                return artifact_result
            cart_update = artifact_result.get("cart_update")
            if not isinstance(cart_update, dict) or not cart_update:
                return artifact_result
        apply_payload = _hospital_bag_cart_apply_payload(
            {
                **handler_args,
                "cart_update": cart_update,
                "summary": _text(handler_args, "summary") or _text(artifact_result, "summary"),
            }
        )
        cart_update = apply_payload.get("cart_update")
        if not isinstance(cart_update, dict) or not cart_update:
            raise ApiError(code="validation_failed", message="cart_update is required.", status=422)

        preview_payload = _hospital_bag_cart_preview_payload(apply_payload)
        action_kwargs: dict[str, Any] = {
            "owner_user_id": context.actor.user_id,
            "run_id": context.run_id,
            "action_type": HOSPITAL_BAG_CART_UPDATE_ACTION,
            "target_type": "hospital_bag_cart",
            "side_effect_level": "low",
            "preview_payload": preview_payload,
            "apply_payload": apply_payload,
            "idempotency_key": _text(handler_args, "idempotency_key")
            or _hospital_bag_cart_idempotency_key(run_id=context.run_id, cart_update=cart_update),
        }
        propose_once = getattr(self.runtime_service, "propose_action_once", None)
        if callable(propose_once):
            action, _ = await propose_once(**action_kwargs)
        else:
            action = await self.runtime_service.propose_action(**action_kwargs)
        return {
            **artifact_result,
            **_proposal_result(action=action, preview_payload=preview_payload),
            "cart_update": cart_update,
        }


class IbclcConsultCardCreateToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        payload = _ibclc_consult_card_payload(context.args)
        if not _text(payload, "reason"):
            raise ApiError(code="validation_failed", message="reason is required.", status=422)
        consent = _ibclc_consult_consent(context.args)
        if not consent["allowed"]:
            return {
                "status": "ibclc_consult_blocked",
                "reason": consent["reason"],
                "requires_confirmation": True,
                "confirmation_question": "要我帮你打开 IBCLC 在线咨询入口吗？",
            }
        artifact, created = await self.runtime_service.create_artifact_once(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type="ibclc_consult_card",
            schema_version="v1",
            status="created",
            payload=payload,
            emit_event=False,
        )
        result = {
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "status": artifact.status,
            "reused": not created,
            "title": _text(payload, "title"),
            "reason": _text(payload, "reason"),
            "urgency": _text(payload, "urgency") or "routine",
        }
        if created:
            result[DEFERRED_AGENT_EVENTS_KEY] = [_deferred_artifact_created_event(artifact)]
        return result


class BirthPreparationArtifactToolHandler(_StandardToolHandler):
    def __init__(
        self,
        *,
        runtime_service: AgentRuntimeService,
        tool_name: str,
        pump_models_service: PumpModelsReferenceService | None = None,
    ) -> None:
        self.runtime_service = runtime_service
        self.tool_name = tool_name
        self.pump_models_service = pump_models_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        pump_products = (
            await _pump_products_for_cart_action(
                args=context.args,
                service=self.pump_models_service,
            )
            if self.tool_name == "hospital_bag_cart_mutate"
            else []
        )
        result = create_birth_preparation_artifact_result(
            self.tool_name,
            context.args,
            pump_products=pump_products,
        )
        artifact_record = artifact_record_from_birth_preparation_result(result)
        if artifact_record is None:
            return result

        artifact = await self.runtime_service.create_artifact(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type=str(artifact_record["artifact_type"]),
            schema_version=str(artifact_record["schema_version"]),
            status="created",
            payload=artifact_record["payload"],
            emit_event=False,
        )
        return {
            **result,
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "schema_version": artifact.schema_version,
            DEFERRED_AGENT_EVENTS_KEY: [_deferred_artifact_created_event(artifact)],
        }


async def _pump_products_for_cart_action(
    *,
    args: dict[str, Any],
    service: PumpModelsReferenceService | None,
) -> list[dict[str, Any]]:
    if not _cart_action_requires_pump_models(args):
        return []
    reference_service = service or PumpModelsReferenceService(object_storage=None)
    reference = await reference_service.read()
    return cast(list[dict[str, Any]], reference["products"])


def _cart_action_requires_pump_models(args: dict[str, Any]) -> bool:
    action = _text(args, "operation")
    if action in {"replace_pump_model", "add_pump_model"}:
        return bool(_text(args, "product_sku_id"))
    if action != "restore_items":
        return False
    item_ids = args.get("item_ids")
    return isinstance(item_ids, list) and any(
        isinstance(item_id, str) and item_id.strip().startswith("pump-")
        for item_id in item_ids
    )


class HospitalBagFormCreateToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service
        self.artifact_handler = BirthPreparationArtifactToolHandler(
            runtime_service=runtime_service,
            tool_name="hospital_bag_form_create",
        )

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        _require_hospital_bag_thread_id(context)
        workflow = _dict(context.args, "runtime_workflow_context")
        generation_mode = _hospital_bag_generation_mode(context.args, workflow=workflow)
        if (
            _text(workflow, "phase") == "collecting_intake"
            and context.args.get("runtime_force_new_form") is not True
        ):
            raw_artifact_id = _text(workflow, "source_form_artifact_id")
            try:
                artifact_id = UUID(raw_artifact_id)
            except ValueError:
                artifact_id = None
            form_artifact = (
                await self.runtime_service.get_artifact_for_owner(
                    owner_user_id=context.actor.user_id,
                    artifact_id=artifact_id,
                )
                if artifact_id is not None
                else None
            )
            artifact_payload = (
                form_artifact.payload
                if form_artifact is not None and isinstance(form_artifact.payload, dict)
                else {}
            )
            form = artifact_payload.get("form")
            if (
                form_artifact is not None
                and form_artifact.status != "deleted"
                and form_artifact.artifact_type == "form"
                and isinstance(form, dict)
                and form.get("id") == HOSPITAL_BAG_FORM_ID
            ):
                await _upsert_hospital_bag_workflow(
                    runtime_service=self.runtime_service,
                    context=context,
                    status="collecting",
                    state={
                        **workflow,
                        "generation_mode": generation_mode,
                    },
                    active_step="collecting_intake",
                )
                return {
                    "tool_name": "ui_form_create",
                    "status": "hospital_bag_intake_already_started",
                    "form": form,
                    "artifact_id": str(form_artifact.id),
                    "artifact_type": form_artifact.artifact_type,
                    "schema_version": form_artifact.schema_version,
                    DEFERRED_AGENT_EVENTS_KEY: [
                        _deferred_artifact_created_event(form_artifact)
                    ],
                }
        result = await self.artifact_handler.execute(context)
        if _text(result, "status") != "form_created":
            return result
        await _upsert_hospital_bag_workflow(
            runtime_service=self.runtime_service,
            context=context,
            status="collecting",
            state={
                "phase": "collecting_intake",
                "form_id": HOSPITAL_BAG_FORM_ID,
                "source_form_artifact_id": _text(result, "artifact_id"),
                "generation_mode": generation_mode,
            },
            active_step="collecting_intake",
        )
        return result


class HospitalBagCardCreateToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service
        self.artifact_handler = BirthPreparationArtifactToolHandler(
            runtime_service=runtime_service,
            tool_name="hospital_bag_card_create",
        )

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        _require_hospital_bag_thread_id(context)
        workflow = _dict(context.args, "runtime_workflow_context")
        form_artifact_id = _text(context.args, "form_artifact_id")
        form_submission_id = _text(context.args, "form_submission_id")
        verified_fact_generation = (
            context.args.get("runtime_verified_fact_generation") is True
        )
        generation_mode = _hospital_bag_generation_mode(context.args, workflow=workflow)
        if (
            _text(workflow, "phase") == "completed"
            and (
                verified_fact_generation
                or (
                    _text(workflow, "source_form_artifact_id") == form_artifact_id
                    and _text(workflow, "source_form_submission_id") == form_submission_id
                )
            )
        ):
            return {
                "status": "hospital_bag_card_already_created",
                "artifact_id": _text(workflow, "result_artifact_id"),
                "artifact_type": "hospital_bag_card",
            }
        if (
            not verified_fact_generation
            and (
                _text(workflow, "phase") != "collecting_intake"
                or not form_artifact_id
                or _text(workflow, "source_form_artifact_id") != form_artifact_id
            )
        ):
            raise ApiError(
                code="stale_hospital_bag_intake",
                message="This hospital bag form is no longer the active intake.",
                status=409,
            )
        artifact_context = _hospital_bag_context_with_args(
            context,
            {
                **context.args,
                "generation_mode": generation_mode,
            },
        )
        result = await self.artifact_handler.execute(artifact_context)
        if _text(result, "status") != "card_created":
            return result
        state = {
            "phase": "completed",
            "form_id": HOSPITAL_BAG_FORM_ID,
            "result_artifact_id": _text(result, "artifact_id"),
            "generation_mode": generation_mode,
            "data_source": (
                "verified_facts"
                if verified_fact_generation
                else "verified_form_submission"
            ),
        }
        if form_artifact_id:
            state["source_form_artifact_id"] = form_artifact_id
        if form_submission_id:
            state["source_form_submission_id"] = form_submission_id
        await _upsert_hospital_bag_workflow(
            runtime_service=self.runtime_service,
            context=context,
            status="completed",
            state=state,
            active_step="",
        )
        return result


class HospitalBagWorkflowToolHandler(_StandardToolHandler):
    """Single model-facing entry point backed by the existing form/card operations."""

    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service
        self.form_handler = HospitalBagFormCreateToolHandler(
            runtime_service=runtime_service
        )
        self.card_handler = HospitalBagCardCreateToolHandler(
            runtime_service=runtime_service
        )

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        _require_hospital_bag_thread_id(context)
        workflow = _dict(context.args, "runtime_workflow_context")
        phase = _text(workflow, "phase")
        generation_mode = _hospital_bag_generation_mode(
            context.args,
            workflow=workflow,
        )
        restart = context.args.get("restart") is True
        current_submission = _dict(context.args, "confirmed_form_data")
        verified_facts = _dict(context.args, "runtime_verified_form_data")
        default_values = {
            **verified_facts,
            **_dict(context.args, "default_values"),
            **current_submission,
        }

        if phase == "completed" and not restart:
            return {
                "tool_name": "hospital_bag_manage",
                "status": "hospital_bag_card_already_created",
                "artifact_id": _text(workflow, "result_artifact_id"),
                "artifact_type": "hospital_bag_card",
                "workflow_context": _hospital_bag_public_workflow_context(
                    phase="completed",
                    generation_mode=generation_mode,
                    information_complete=True,
                    data_source=_text(workflow, "data_source"),
                ),
            }

        if _has_hospital_bag_form_submission(context.args):
            _require_active_hospital_bag_form_submission(
                args=context.args,
                workflow=workflow,
            )

        if phase == "collecting_intake" and not restart:
            if current_submission:
                urgent_signal_ids = _hospital_bag_urgent_signal_ids(
                    current_submission
                )
                if urgent_signal_ids:
                    return await self._interrupt_for_safety(
                        context,
                        workflow=workflow,
                        signal_ids=urgent_signal_ids,
                    )
                missing = missing_hospital_bag_required_fields(current_submission)
                invalid = invalid_hospital_bag_required_fields(
                    current_submission
                )
                if not missing and not invalid:
                    return await self._generate_card(
                        context,
                        form_data=current_submission,
                        generation_mode=generation_mode,
                        verified_fact_generation=False,
                    )
                return await self._create_form(
                    context,
                    default_values=_without_hospital_bag_fields(
                        default_values,
                        invalid,
                    ),
                    generation_mode=generation_mode,
                    missing_fields=missing,
                    invalid_fields=invalid,
                    force_new=True,
                )
            missing = missing_hospital_bag_required_fields(verified_facts)
            invalid = invalid_hospital_bag_required_fields(verified_facts)
            return await self._create_form(
                context,
                default_values=_without_hospital_bag_fields(
                    default_values,
                    invalid,
                ),
                generation_mode=generation_mode,
                missing_fields=missing,
                invalid_fields=invalid,
                force_new=False,
            )

        if restart:
            invalid = invalid_hospital_bag_required_fields(verified_facts)
            return await self._create_form(
                context,
                default_values=_without_hospital_bag_fields(
                    default_values,
                    invalid,
                ),
                generation_mode=generation_mode,
                missing_fields=missing_hospital_bag_required_fields(
                    verified_facts
                ),
                invalid_fields=invalid,
                force_new=True,
            )

        missing = missing_hospital_bag_required_fields(verified_facts)
        invalid = invalid_hospital_bag_required_fields(verified_facts)
        if verified_facts and not missing and not invalid:
            urgent_signal_ids = _hospital_bag_urgent_signal_ids(
                verified_facts
            )
            if urgent_signal_ids:
                return await self._interrupt_for_safety(
                    context,
                    workflow=workflow,
                    signal_ids=urgent_signal_ids,
                )
            return await self._generate_card(
                context,
                form_data=verified_facts,
                generation_mode=generation_mode,
                verified_fact_generation=True,
            )
        return await self._create_form(
            context,
            default_values=_without_hospital_bag_fields(
                default_values,
                invalid,
            ),
            generation_mode=generation_mode,
            missing_fields=missing,
            invalid_fields=invalid,
            force_new=False,
        )

    async def _interrupt_for_safety(
        self,
        context: ToolHandlerContext,
        *,
        workflow: dict[str, Any],
        signal_ids: list[str],
    ) -> dict[str, Any]:
        interrupted = {
            **workflow,
            "phase": "safety_interruption",
            "form_id": HOSPITAL_BAG_FORM_ID,
            "interrupted_by_safety_signal": True,
            "paused": True,
            "resume_phase": "collecting_intake",
            "safety_signal_ids": list(dict.fromkeys(signal_ids)),
            "interrupted_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
        }
        await _upsert_hospital_bag_workflow(
            runtime_service=self.runtime_service,
            context=context,
            status="paused",
            state=interrupted,
            active_step="safety_interruption",
        )
        return {
            "tool_name": "hospital_bag_manage",
            "status": "urgent_care_required",
            "signal_ids": list(dict.fromkeys(signal_ids)),
            "blocks_hospital_bag_flow": True,
            "required_response": HOSPITAL_BAG_URGENT_RESPONSE,
            "workflow_context": _hospital_bag_public_workflow_context(
                phase="safety_interruption",
                generation_mode=_hospital_bag_generation_mode(
                    context.args,
                    workflow=workflow,
                ),
                information_complete=False,
            ),
        }

    async def _create_form(
        self,
        context: ToolHandlerContext,
        *,
        default_values: dict[str, Any],
        generation_mode: str,
        missing_fields: list[str],
        force_new: bool,
        invalid_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        result = await self.form_handler.execute(
            _hospital_bag_context_with_args(
                context,
                {
                    **context.args,
                    "default_values": default_values,
                    "generation_mode": generation_mode,
                    "runtime_force_new_form": force_new,
                },
            )
        )
        return {
            **result,
            "tool_name": "hospital_bag_manage",
            "workflow_context": _hospital_bag_public_workflow_context(
                phase="collecting_intake",
                generation_mode=generation_mode,
                information_complete=False,
                missing_fields=missing_fields,
                invalid_fields=invalid_fields,
            ),
        }

    async def _generate_card(
        self,
        context: ToolHandlerContext,
        *,
        form_data: dict[str, Any],
        generation_mode: str,
        verified_fact_generation: bool,
    ) -> dict[str, Any]:
        result = await self.card_handler.execute(
            _hospital_bag_context_with_args(
                context,
                {
                    **context.args,
                    "confirmed_form_data": form_data,
                    "generation_mode": generation_mode,
                    "runtime_result_tool_name": "hospital_bag_manage",
                    "runtime_verified_fact_generation": verified_fact_generation,
                },
            )
        )
        if _text(result, "status") != "card_created":
            return result
        return {
            **result,
            "workflow_context": _hospital_bag_public_workflow_context(
                phase="completed",
                generation_mode=generation_mode,
                information_complete=True,
                data_source=(
                    "verified_facts"
                    if verified_fact_generation
                    else "verified_form_submission"
                ),
            ),
        }


def _hospital_bag_generation_mode(
    args: dict[str, Any],
    *,
    workflow: dict[str, Any],
) -> str:
    mode = _text(args, "generation_mode") or _text(workflow, "generation_mode")
    return mode if mode in {"standard", "quick", "immediate"} else "standard"


def _hospital_bag_context_with_args(
    context: ToolHandlerContext,
    args: dict[str, Any],
) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=context.actor,
        run_id=context.run_id,
        tool_name=context.tool_name,
        call_id=context.call_id,
        args=args,
        thread_id=context.thread_id,
    )


def _hospital_bag_public_workflow_context(
    *,
    phase: str,
    generation_mode: str,
    information_complete: bool,
    missing_fields: list[str] | None = None,
    invalid_fields: list[str] | None = None,
    data_source: str = "",
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "workflow_type": "hospital_bag",
        "phase": phase,
        "information_complete": information_complete,
        "generation_mode": generation_mode,
        "next_action": (
            "seek_urgent_care"
            if phase == "safety_interruption"
            else (
                "none"
                if information_complete
                else "submit_hospital_bag_intake"
            )
        ),
    }
    if missing_fields:
        context["missing_fields"] = list(missing_fields)
    if invalid_fields:
        context["invalid_fields"] = list(invalid_fields)
    if data_source:
        context["data_source"] = data_source
    return context


def _without_hospital_bag_fields(
    values: dict[str, Any],
    field_ids: list[str],
) -> dict[str, Any]:
    excluded = set(field_ids)
    return {
        key: value
        for key, value in values.items()
        if key not in excluded
    }


def _has_hospital_bag_form_submission(args: dict[str, Any]) -> bool:
    return any(
        key in args
        for key in (
            "confirmed_form_data",
            "form_artifact_id",
            "form_submission_id",
        )
    )


def _require_active_hospital_bag_form_submission(
    *,
    args: dict[str, Any],
    workflow: dict[str, Any],
) -> None:
    form_artifact_id = _text(args, "form_artifact_id")
    if (
        _text(workflow, "phase") != "collecting_intake"
        or not form_artifact_id
        or not _text(args, "form_submission_id")
        or _text(workflow, "source_form_artifact_id") != form_artifact_id
    ):
        raise ApiError(
            code="stale_hospital_bag_intake",
            message="This hospital bag form is no longer the active intake.",
            status=409,
        )


_HOSPITAL_BAG_CURRENT_SYMPTOM_CUES = (
    "现在",
    "目前",
    "刚刚",
    "今天",
    "此刻",
    "正在",
)
_HOSPITAL_BAG_WORRY_OR_RISK_CUES = (
    "担心",
    "害怕",
    "怕",
    "风险",
    "可能",
    "生产时",
    "分娩时",
    "产后",
    "以后",
    "将来",
    "预防",
)


def _hospital_bag_urgent_signal_ids(values: dict[str, Any]) -> list[str]:
    safety_text = "；".join(
        clause.strip()
        for raw_value in (
            values.get("pregnancy_history_or_notes"),
            values.get("top_worries"),
        )
        for item in (raw_value if isinstance(raw_value, list) else [raw_value])
        for clause in re.split(
            r"[，,。；;！？!?]|但|不过",
            str(item or ""),
        )
        if any(cue in clause for cue in _HOSPITAL_BAG_CURRENT_SYMPTOM_CUES)
        and not any(cue in clause for cue in _HOSPITAL_BAG_WORRY_OR_RISK_CUES)
    )
    return pregnancy_plan_urgent_signal_ids(
        {"medical_notes": safety_text}
    )


class PregnancyPlanIntakeStartToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        _require_pregnancy_plan_thread_id(context)
        workflow = _dict(context.args, "runtime_workflow_context")
        restart = context.args.get("restart") is True
        if (
            restart
            or workflow.get("abandoned") is True
            or _text(workflow, "consumed_by_action_id")
            or workflow.get("interrupted_by_safety_signal") is True
        ):
            workflow = {}
        phase = _text(workflow, "phase")
        if phase == PregnancyPlanPhase.COLLECTING_INTAKE.value:
            raw_artifact_id = _text(workflow, "source_form_artifact_id")
            try:
                artifact_id = UUID(raw_artifact_id)
            except ValueError:
                artifact_id = None
            form_artifact = (
                await self.runtime_service.get_artifact_for_owner(
                    owner_user_id=context.actor.user_id,
                    artifact_id=artifact_id,
                )
                if artifact_id is not None
                else None
            )
            artifact_payload = (
                form_artifact.payload
                if form_artifact is not None and isinstance(form_artifact.payload, dict)
                else {}
            )
            form = artifact_payload.get("form")
            if (
                form_artifact is not None
                and form_artifact.status != "deleted"
                and form_artifact.artifact_type == "form"
                and artifact_payload.get("tool_name") == "pregnancy_intake_manage"
                and isinstance(form, dict)
                and form.get("id") == PREGNANCY_PLAN_INTAKE_FORM_ID
            ):
                await _upsert_pregnancy_plan_workflow(
                    runtime_service=self.runtime_service,
                    context=context,
                    workflow=workflow,
                )
                return {
                    "tool_name": "ui_form_create",
                    "status": "pregnancy_plan_intake_already_started",
                    "form": form,
                    "artifact_id": str(form_artifact.id),
                    "artifact_type": form_artifact.artifact_type,
                    "schema_version": form_artifact.schema_version,
                    "workflow_context": pregnancy_plan_workflow_context(workflow),
                    DEFERRED_AGENT_EVENTS_KEY: [
                        _deferred_artifact_created_event(form_artifact)
                    ],
                }
        if phase == "awaiting_additional_information" or phase in {
            item.value for item in PregnancyPlanPhase if item is not PregnancyPlanPhase.COLLECTING_INTAKE
        }:
            return {
                "status": "pregnancy_plan_intake_already_analyzed",
                "requires_user_reply": True,
            }

        form = build_pregnancy_plan_intake_form(default_values=_dict(context.args, "default_values"))
        form_artifact = await self.runtime_service.create_artifact(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type="form",
            schema_version="1.0",
            status="created",
            payload={"tool_name": "pregnancy_intake_manage", "form": form},
            emit_event=False,
        )
        snapshot: dict[str, Any] = collecting_intake_snapshot(form_artifact_id=str(form_artifact.id))
        prior_workflow = _dict(context.args, "prior_workflow_context")
        if _text(context.args, "edit_step_id") == "basic_intake" and prior_workflow:
            revisions = prior_workflow.get("answer_revisions")
            revision_items = (
                [dict(item) for item in revisions if isinstance(item, dict)]
                if isinstance(revisions, list)
                else []
            )
            revision_items.append(
                {
                    "revision": len(revision_items) + 1,
                    "step_id": "basic_intake",
                    "previous_answer": "已提交",
                    "answer": "等待重新提交",
                    "choice_id": "",
                    "invalidated_step_ids": [
                        "personalized_followups",
                        "checkup_done",
                        "checkup_records",
                        "final_confirmation",
                        "generate_plan",
                    ],
                }
            )
            snapshot["answer_revisions"] = revision_items[-20:]
            snapshot["editing_step_id"] = "basic_intake"
        await _upsert_pregnancy_plan_workflow(
            runtime_service=self.runtime_service,
            context=context,
            workflow=snapshot,
        )
        return {
            "tool_name": "ui_form_create",
            "status": "form_created",
            "form": form,
            "artifact_id": str(form_artifact.id),
            "artifact_type": form_artifact.artifact_type,
            "schema_version": form_artifact.schema_version,
            "workflow_context": pregnancy_plan_workflow_context(snapshot),
            DEFERRED_AGENT_EVENTS_KEY: [_deferred_artifact_created_event(form_artifact)],
        }


class PregnancyPlanIntakeAnalyzeToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> _ToolOperationOutput:
        _require_pregnancy_plan_thread_id(context)
        form_values = _dict(context.args, "confirmed_form_data")
        form_artifact_id = _text(context.args, "form_artifact_id")
        submission_id = _text(context.args, "form_submission_id")
        workflow = _dict(context.args, "runtime_workflow_context")
        if not form_values or not form_artifact_id or not submission_id:
            raise ApiError(
                code="validation_failed",
                message="A verified pregnancy plan intake submission is required.",
                status=422,
            )

        urgent_signal_ids = pregnancy_plan_urgent_signal_ids(form_values)
        if urgent_signal_ids:
            await _interrupt_pregnancy_plan_for_safety(
                runtime_service=self.runtime_service,
                context=context,
                workflow=workflow,
                signal_ids=urgent_signal_ids,
            )
            return _pregnancy_plan_urgent_result(urgent_signal_ids)

        if _text(workflow, "consumed_by_action_id"):
            return {
                "status": "pregnancy_plan_intake_consumed",
                "action_id": _text(workflow, "consumed_by_action_id"),
            }
        if workflow.get("interrupted_by_safety_signal") is True:
            return {
                "status": "pregnancy_plan_intake_interrupted_for_safety",
                "requires_fresh_intake": True,
            }

        if (
            _text(workflow, "phase") != PregnancyPlanPhase.COLLECTING_INTAKE.value
            and _text(workflow, "source_form_submission_id") == submission_id
        ):
            return _pregnancy_plan_workflow_result(workflow)
        if (
            _text(workflow, "phase") != PregnancyPlanPhase.COLLECTING_INTAKE.value
            or _text(workflow, "source_form_artifact_id") != form_artifact_id
        ):
            raise ApiError(
                code="stale_pregnancy_plan_intake",
                message="This pregnancy plan form is no longer the active intake.",
                status=409,
            )

        missing_fields = missing_pregnancy_plan_intake_fields(form_values)
        if missing_fields:
            raise ApiError(
                code="validation_failed",
                message="Pregnancy plan intake is missing required fields.",
                status=422,
                details={"missing_fields": missing_fields},
            )
        invalid_fields = invalid_pregnancy_plan_intake_fields(form_values)
        if invalid_fields:
            raise ApiError(
                code="validation_failed",
                message="Pregnancy plan intake contains invalid fields.",
                status=422,
                details={"invalid_fields": invalid_fields},
            )

        snapshot = initialize_pregnancy_plan_workflow(
            form_values,
            form_artifact_id=form_artifact_id,
            form_submission_id=submission_id,
            analysis_run_id=str(context.run_id),
        )
        await _upsert_pregnancy_plan_workflow(
            runtime_service=self.runtime_service,
            context=context,
            workflow=snapshot,
        )
        return _pregnancy_plan_workflow_result(
            snapshot,
            initial_analysis=True,
        )


class PregnancyPlanIntakeAdvanceToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def execute(self, context: ToolHandlerContext) -> _ToolOperationOutput:
        _require_pregnancy_plan_thread_id(context)
        workflow = _dict(context.args, "runtime_workflow_context")
        action = _text(context.args, "action")
        if not workflow or not action:
            raise ApiError(code="validation_failed", message="An active pregnancy plan intake workflow is required.", status=422)
        if _text(workflow, "consumed_by_action_id"):
            return {
                "status": "pregnancy_plan_intake_consumed",
                "action_id": _text(workflow, "consumed_by_action_id"),
            }
        if workflow.get("interrupted_by_safety_signal") is True:
            return {
                "status": "pregnancy_plan_intake_interrupted_for_safety",
                "requires_fresh_intake": True,
            }
        if action == "abandon":
            abandoned = {
                **workflow,
                "abandoned": True,
                "abandoned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            await _upsert_pregnancy_plan_workflow(
                runtime_service=self.runtime_service,
                context=context,
                workflow=abandoned,
            )
            return {
                "status": "pregnancy_plan_intake_abandoned",
                "requires_fresh_intake": True,
            }

        urgent_signal_ids = pregnancy_plan_urgent_signal_ids({"additional_info": _text(context.args, "trusted_current_user_text")})
        if urgent_signal_ids:
            await _interrupt_pregnancy_plan_for_safety(
                runtime_service=self.runtime_service,
                context=context,
                workflow=workflow,
                signal_ids=urgent_signal_ids,
            )
            return _pregnancy_plan_urgent_result(urgent_signal_ids)

        completed_followup = pregnancy_plan_current_followup(workflow)
        if action == "mark_checkup_records_uploaded" and (_optional_int(context.args, "runtime_checkup_attachment_count") or 0) < 1:
            return _pregnancy_plan_workflow_result(
                workflow,
                status_override="checkup_attachment_required",
            )

        payload: dict[str, Any] = {
            key: value
            for key, value in context.args.items()
            if key
            in {
                "answer",
                "summary",
                "additional_info",
                "final_additional_info",
            }
        }
        trusted_current_user_text = _text(context.args, "trusted_current_user_text")
        structured_workflow_command = (
            context.args.get("runtime_structured_workflow_command") is True
        )
        if action == "submit_personalized_followup":
            if completed_followup is not None:
                payload["topic"] = _text(completed_followup, "id")
            if trusted_current_user_text and (
                not structured_workflow_command or not _text(payload, "answer")
            ):
                payload["answer"] = trusted_current_user_text
        elif (
            action == "submit_final_additional_info"
            and trusted_current_user_text
            and (
                not structured_workflow_command
                or not _text(payload, "additional_info")
            )
        ):
            payload["additional_info"] = trusted_current_user_text
        try:
            advanced = advance_pregnancy_plan_workflow(workflow, action=action, payload=payload)
        except ValueError as exc:
            raise ApiError(
                code=str(exc) or "invalid_pregnancy_plan_workflow_action",
                message="Pregnancy plan intake action is not valid for the current step.",
                status=409,
            ) from exc
        await _upsert_pregnancy_plan_workflow(
            runtime_service=self.runtime_service,
            context=context,
            workflow=advanced,
        )
        return _pregnancy_plan_workflow_result(
            advanced,
            completed_followup=completed_followup,
            checkup_attachment_count=_optional_int(context.args, "runtime_checkup_attachment_count") or 0,
        )
