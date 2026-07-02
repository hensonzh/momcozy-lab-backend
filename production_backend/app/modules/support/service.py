from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyKey, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import SupportTicket
from .repository import SupportTicketsRepository


SUPPORT_TICKET_CREATE_IDEMPOTENCY_SCOPE = "support.tickets.create"


class SupportTicketsService:
    def __init__(
        self,
        *,
        repository: SupportTicketsRepository,
        audit_service: AuditService | None = None,
        idempotency_service: IdempotencyService | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.idempotency_service = idempotency_service

    async def create_ticket(
        self,
        *,
        owner_user_id: UUID,
        issue_type: str = "other",
        issue_summary: str,
        product_model: str = "",
        order_number: str = "",
        purchase_channel: str = "",
        user_contact: str = "",
        urgency: str = "normal",
        source: str = "agent",
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> SupportTicket:
        normalized_issue_type = _normalize_text(issue_type, field_name="issue_type", max_length=120) or "other"
        normalized_summary = _normalize_text(issue_summary, field_name="issue_summary", max_length=2000, required=True)
        normalized_product_model = _normalize_text(product_model, field_name="product_model", max_length=120)
        normalized_order_number = _normalize_text(order_number, field_name="order_number", max_length=120)
        normalized_purchase_channel = _normalize_text(purchase_channel, field_name="purchase_channel", max_length=120)
        normalized_user_contact = _normalize_text(user_contact, field_name="user_contact", max_length=255)
        normalized_urgency = _normalize_text(urgency, field_name="urgency", max_length=32) or "normal"
        normalized_source = _normalize_text(source, field_name="source", max_length=64) or "agent"
        safe_payload = dict(payload or {})
        if metadata:
            existing_metadata = safe_payload.get("metadata")
            if not isinstance(existing_metadata, dict):
                existing_metadata = {}
            safe_payload["metadata"] = {**existing_metadata, **metadata}

        idempotency_record = await self._reserve_idempotency(
            owner_user_id=owner_user_id,
            key=idempotency_key,
            payload={
                "issue_type": normalized_issue_type,
                "issue_summary": normalized_summary,
                "product_model": normalized_product_model,
                "order_number": normalized_order_number,
                "purchase_channel": normalized_purchase_channel,
                "urgency": normalized_urgency,
                "source": normalized_source,
                "payload": safe_payload,
            },
        )
        if idempotency_record is not None and idempotency_record.response_ref:
            return await self._replay_ticket(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        ticket = await self.repository.create_ticket(
            owner_user_id=owner_user_id,
            ticket_number=_new_ticket_number(),
            issue_type=normalized_issue_type,
            issue_summary=normalized_summary,
            product_model=normalized_product_model,
            order_number=normalized_order_number,
            purchase_channel=normalized_purchase_channel,
            user_contact=normalized_user_contact,
            urgency=normalized_urgency,
            source=normalized_source,
            payload=safe_payload,
            submitted_at=_utcnow(),
        )
        await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(ticket.id))
        await self._audit(owner_user_id=owner_user_id, action="support.tickets.create", resource_id=str(ticket.id), request_id=request_id)
        return ticket

    async def list_tickets(self, *, owner_user_id: UUID, status: str | None = None, limit: int = 50) -> list[SupportTicket]:
        self._validate_limit(limit)
        return await self.repository.list_for_owner(owner_user_id=owner_user_id, status=_optional_filter(status), limit=limit)

    async def get_ticket(self, *, owner_user_id: UUID, ticket_id: UUID) -> SupportTicket:
        ticket = await self.repository.get_for_owner(ticket_id=ticket_id, owner_user_id=owner_user_id)
        if ticket is None:
            raise ApiError(code="not_found", message="Support ticket not found.", status=404)
        return ticket

    async def _reserve_idempotency(self, *, owner_user_id: UUID, key: str | None, payload: dict[str, Any]) -> IdempotencyKey | None:
        if not key:
            return None
        if self.idempotency_service is None:
            raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
        decision = await self.idempotency_service.reserve(
            actor_user_id=owner_user_id,
            scope=SUPPORT_TICKET_CREATE_IDEMPOTENCY_SCOPE,
            key=key,
            request_hash=request_hash(payload),
            expires_at=_utcnow() + timedelta(hours=24),
        )
        if decision.status == "replay" and not decision.record.response_ref:
            raise ApiError(code="idempotency_in_progress", message="Request is still in progress.", status=409)
        return decision.record

    async def _complete_idempotency(self, *, idempotency_record: IdempotencyKey | None, response_ref: str) -> None:
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=response_ref)

    async def _replay_ticket(self, *, owner_user_id: UUID, response_ref: str) -> SupportTicket:
        ticket_id = parse_idempotency_response_ref(response_ref)
        ticket = await self.repository.get_for_owner(ticket_id=ticket_id, owner_user_id=owner_user_id)
        if ticket is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return ticket

    async def _audit(self, *, owner_user_id: UUID, action: str, resource_id: str, request_id: str) -> None:
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action=action,
                resource_type="support_ticket",
                resource_id=resource_id,
                request_id=request_id,
            )

    def _validate_limit(self, limit: int) -> None:
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)


def _normalize_text(value: str | None, *, field_name: str, max_length: int, required: bool = False) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise ApiError(code="validation_failed", message=f"{field_name} is required.", status=422)
    if len(normalized) > max_length:
        raise ApiError(code="validation_failed", message=f"{field_name} is too long.", status=422)
    return normalized


def _optional_filter(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _new_ticket_number() -> str:
    return f"ticket_{uuid.uuid4().hex[:12]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
