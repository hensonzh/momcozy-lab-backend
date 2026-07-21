from __future__ import annotations

from typing import Any
from uuid import UUID

from app.agent_runtime.actions.executor import AgentActionExecutor
from app.agent_runtime.actions.policy import AgentActionPolicy, AgentActionPolicyRule
from app.agent_runtime.runs.repository import AgentRuntimeRepository
from app.agent_runtime.runs.service import AgentRuntimeService
from app.core.errors import ApiError
from app.modules.audit import request_hash
from app.modules.auth import CurrentUser
from app.modules.support.schemas import SupportTicketCreate
from app.modules.support.service import SupportTicketsService

from .support import SUPPORT_TICKET_CREATE_ACTION, SupportTicketCreateActionHandler


async def create_agent_form_ticket(
    *,
    payload: SupportTicketCreate,
    current_user: CurrentUser,
    service: SupportTicketsService,
    idempotency_key: str | None,
) -> Any:
    artifact_id = _artifact_id(dict(payload.payload or {}))
    session = getattr(service.repository, "session", None)
    if session is None:
        raise ApiError(code="agent_runtime_not_configured", message="Agent runtime session is unavailable.", status=500)
    repository = AgentRuntimeRepository(session)
    artifact = await repository.get_artifact_for_owner(
        artifact_id=artifact_id,
        owner_user_id=current_user.user_id,
    )
    if artifact is None or artifact.artifact_type != "support_ticket_draft" or artifact.status == "deleted":
        raise ApiError(code="invalid_support_ticket_artifact", message="Support ticket draft is unavailable.", status=409)
    run = await repository.get_run_for_owner(run_id=artifact.run_id, owner_user_id=current_user.user_id)
    if run is None:
        raise ApiError(code="invalid_support_ticket_artifact", message="Support ticket draft run is unavailable.", status=409)
    if payload.thread_id and payload.thread_id != str(run.thread_id):
        raise ApiError(code="support_ticket_thread_mismatch", message="Support ticket draft thread does not match.", status=409)

    apply_payload = {
        "issue_type": payload.issue_type or "other",
        "issue_summary": payload.issue_summary,
        "product_model": payload.product_model or "",
        "order_number": payload.order_number or "",
        "purchase_channel": payload.purchase_channel or "",
        "user_contact": payload.user_contact or "",
        "urgency": payload.urgency or "normal",
        "artifact_id": str(artifact.id),
        "metadata": _metadata_from_payload(payload),
    }
    action_idempotency_key = idempotency_key or f"support-form:{request_hash(apply_payload)}"
    action_policy = AgentActionPolicy(
        rules={
            SUPPORT_TICKET_CREATE_ACTION: AgentActionPolicyRule(
                action_type=SUPPORT_TICKET_CREATE_ACTION,
                target_type="support_ticket",
                side_effect_level="medium",
                requires_confirmation=False,
            )
        }
    )
    action_executor = AgentActionExecutor(
        repository=repository,
        handlers={SUPPORT_TICKET_CREATE_ACTION: SupportTicketCreateActionHandler(service=service)},
        action_policy=action_policy,
    )
    runtime_service = AgentRuntimeService(
        repository=repository,
        action_executor=action_executor,
        action_policy=action_policy,
    )
    outcome, _ = await runtime_service.propose_action_once_with_outcome(
        owner_user_id=current_user.user_id,
        run_id=run.id,
        action_type=SUPPORT_TICKET_CREATE_ACTION,
        target_type="support_ticket",
        side_effect_level="medium",
        preview_payload={
            "issue_type": apply_payload["issue_type"],
            "issue_summary": apply_payload["issue_summary"],
            "product_model": apply_payload["product_model"],
            "urgency": apply_payload["urgency"],
        },
        apply_payload=apply_payload,
        idempotency_key=action_idempotency_key,
        reuse_existing=True,
    )
    if outcome.action.status != "applied":
        raise ApiError(
            code=outcome.action.error_code or "support_ticket_action_failed",
            message="Support ticket could not be created.",
            status=409,
        )
    if outcome.apply_result is not None and outcome.apply_result.resource_id:
        return await service.get_ticket(
            owner_user_id=current_user.user_id,
            ticket_id=UUID(outcome.apply_result.resource_id),
        )
    return await _replay_agent_form_ticket(
        service=service,
        current_user=current_user,
        action=outcome.action,
        apply_payload=apply_payload,
    )


async def _replay_agent_form_ticket(
    *,
    service: SupportTicketsService,
    current_user: CurrentUser,
    action: Any,
    apply_payload: dict[str, Any],
) -> Any:
    return await service.create_ticket(
        owner_user_id=current_user.user_id,
        issue_type=str(apply_payload["issue_type"]),
        issue_summary=str(apply_payload["issue_summary"]),
        product_model=str(apply_payload["product_model"]),
        order_number=str(apply_payload["order_number"]),
        purchase_channel=str(apply_payload["purchase_channel"]),
        user_contact=str(apply_payload["user_contact"]),
        urgency=str(apply_payload["urgency"]),
        source="agent_action",
        payload={
            "agent_action_id": str(action.id),
            "agent_run_id": str(action.run_id),
            "raw_action_payload": apply_payload,
        },
        idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
    )


def _metadata_from_payload(payload: SupportTicketCreate) -> dict[str, Any]:
    metadata = {}
    if payload.thread_id:
        metadata["thread_id"] = payload.thread_id
    if payload.locale:
        metadata["locale"] = payload.locale
    if payload.timezone:
        metadata["timezone"] = payload.timezone
    if payload.message_sent_at:
        metadata["message_sent_at"] = payload.message_sent_at.isoformat()
    return metadata


def _artifact_id(payload: dict[str, Any]) -> UUID:
    raw = str(payload.get("artifact_id") or "").strip()
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ApiError(code="invalid_support_ticket_artifact", message="artifact_id is required.", status=422) from exc
