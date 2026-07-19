import asyncio
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.modules.audit.models import IdempotencyKey
from app.modules.audit.service import IdempotencyDecision
from app.modules.support.models import SupportTicket
from app.modules.support.service import SupportTicketsService


def test_direct_support_ticket_main_flow_replays_idempotent_submit_and_owner_scopes_inbox() -> None:
    asyncio.run(_run_direct_support_ticket_main_flow())


async def _run_direct_support_ticket_main_flow() -> None:
    owner_user_id = uuid4()
    other_user_id = uuid4()
    repository = InMemorySupportTicketsRepository()
    audit_service = FlowAuditService()
    idempotency_service = FlowIdempotencyService()
    service = SupportTicketsService(
        repository=repository,
        audit_service=audit_service,
        idempotency_service=idempotency_service,
    )

    ticket = await service.create_ticket(
        owner_user_id=owner_user_id,
        issue_type="device_fault",
        issue_summary=" Pump does not turn on ",
        product_model="Air1",
        user_contact="mia@example.com",
        urgency="high",
        source="app",
        payload={"origin": "settings_support"},
        metadata={"screen": "device_help"},
        request_id="req_ticket",
        idempotency_key="idem-ticket",
    )
    replayed = await service.create_ticket(
        owner_user_id=owner_user_id,
        issue_type="device_fault",
        issue_summary="Pump does not turn on",
        product_model="Air1",
        user_contact="mia@example.com",
        urgency="high",
        source="app",
        payload={"origin": "settings_support"},
        metadata={"screen": "device_help"},
        idempotency_key="idem-ticket",
    )
    with pytest.raises(ApiError) as idempotency_conflict:
        await service.create_ticket(
            owner_user_id=owner_user_id,
            issue_type="device_fault",
            issue_summary="Pump does not turn on",
            product_model="Air1",
            user_contact="different@example.com",
            urgency="high",
            source="app",
            payload={"origin": "settings_support"},
            metadata={"screen": "device_help"},
            idempotency_key="idem-ticket",
        )
    await service.create_ticket(
        owner_user_id=other_user_id,
        issue_type="device_fault",
        issue_summary="Other user pump does not turn on",
        product_model="Air1",
        source="app",
        idempotency_key="idem-other-ticket",
    )

    visible_tickets = await service.list_tickets(owner_user_id=owner_user_id, status="submitted", limit=10)
    fetched = await service.get_ticket(owner_user_id=owner_user_id, ticket_id=ticket.id)
    with pytest.raises(ApiError) as cross_user_get:
        await service.get_ticket(owner_user_id=other_user_id, ticket_id=ticket.id)

    assert replayed.id == ticket.id
    assert ticket.issue_summary == "Pump does not turn on"
    assert ticket.source == "app"
    assert ticket.payload["origin"] == "settings_support"
    assert ticket.payload["metadata"] == {"screen": "device_help"}
    assert [item.id for item in visible_tickets] == [ticket.id]
    assert fetched.id == ticket.id
    assert idempotency_conflict.value.code == "idempotency_conflict"
    assert cross_user_get.value.code == "not_found"
    assert [entry["action"] for entry in audit_service.entries] == [
        "support.tickets.create",
        "support.tickets.create",
    ]
    assert idempotency_service.completed_response_refs == [str(ticket.id), str(repository.tickets[-1].id)]


class InMemorySupportTicketsRepository:
    def __init__(self) -> None:
        self.tickets: list[SupportTicket] = []

    async def create_ticket(self, **kwargs):
        ticket = SupportTicket(
            id=uuid4(),
            status="submitted",
            **kwargs,
        )
        self.tickets.append(ticket)
        return ticket

    async def get_for_owner(self, *, ticket_id: UUID, owner_user_id: UUID):
        return next(
            (
                ticket
                for ticket in self.tickets
                if ticket.id == ticket_id and ticket.owner_user_id == owner_user_id
            ),
            None,
        )

    async def list_for_owner(self, *, owner_user_id: UUID, status: str | None, limit: int):
        tickets = [
            ticket
            for ticket in self.tickets
            if ticket.owner_user_id == owner_user_id and (status is None or ticket.status == status)
        ]
        return tickets[:limit]


class FlowIdempotencyService:
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, str, str], IdempotencyKey] = {}
        self.completed_response_refs: list[str] = []

    async def reserve(self, *, actor_user_id: UUID, scope: str, key: str, request_hash: str, expires_at):
        record_key = (actor_user_id, scope, key)
        existing = self.records.get(record_key)
        if existing is None:
            record = IdempotencyKey(
                id=uuid4(),
                actor_user_id=actor_user_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
                expires_at=expires_at,
                response_ref="",
            )
            self.records[record_key] = record
            return IdempotencyDecision(status="reserved", record=record)
        if existing.request_hash != request_hash:
            raise ApiError(
                code="idempotency_conflict",
                message="Idempotency key was reused with a different request.",
                status=409,
            )
        return IdempotencyDecision(status="replay", record=existing)

    async def mark_completed(self, *, record: IdempotencyKey, response_ref: str):
        record.response_ref = response_ref
        self.completed_response_refs.append(response_ref)
        return record


class FlowAuditService:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)
