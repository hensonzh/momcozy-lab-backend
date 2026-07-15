import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.audit.service import request_hash
from production_backend.app.modules.support.models import SupportTicket
from production_backend.app.modules.support.service import SupportTicketsService


def test_support_tickets_service_creates_ticket_with_idempotency_and_audit() -> None:
    owner_user_id = uuid4()
    repository = FakeSupportTicketsRepository()
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = SupportTicketsService(repository=repository, audit_service=audit_service, idempotency_service=idempotency_service)

    ticket = asyncio.run(
        service.create_ticket(
            owner_user_id=owner_user_id,
            issue_type="device_fault",
            issue_summary="Pump does not turn on",
            product_model="Air1",
            metadata={"thread_id": "thread-1"},
            request_id="req_ticket",
            idempotency_key="idem-ticket",
        )
    )

    assert ticket.owner_user_id == owner_user_id
    assert ticket.issue_type == "device_fault"
    assert ticket.payload["metadata"]["thread_id"] == "thread-1"
    assert idempotency_service.reserve_kwargs["scope"] == "support.tickets.create"
    assert idempotency_service.completed_response_ref == str(ticket.id)
    assert audit_service.record_kwargs["action"] == "support.tickets.create"


def test_support_tickets_service_lists_and_gets_owner_ticket() -> None:
    owner_user_id = uuid4()
    ticket = _ticket(owner_user_id=owner_user_id)
    repository = FakeSupportTicketsRepository(ticket=ticket)
    service = SupportTicketsService(repository=repository)

    tickets = asyncio.run(service.list_tickets(owner_user_id=owner_user_id, status="submitted", limit=10))
    fetched = asyncio.run(service.get_ticket(owner_user_id=owner_user_id, ticket_id=ticket.id))

    assert tickets == [ticket]
    assert fetched == ticket


def test_support_ticket_idempotency_hash_includes_user_contact() -> None:
    owner_user_id = uuid4()
    idempotency_service = FakeIdempotencyService(status="reserved")
    service = SupportTicketsService(repository=FakeSupportTicketsRepository(), idempotency_service=idempotency_service)

    asyncio.run(
        service.create_ticket(
            owner_user_id=owner_user_id,
            issue_type="device_fault",
            issue_summary="Pump does not turn on",
            product_model="Air1",
            user_contact="user@example.com",
            idempotency_key="idem-ticket",
        )
    )

    expected_hash = request_hash(
        {
            "issue_type": "device_fault",
            "issue_summary": "Pump does not turn on",
            "product_model": "Air1",
            "order_number": "",
            "purchase_channel": "",
            "user_contact": "user@example.com",
            "urgency": "normal",
            "source": "agent",
            "payload": {},
        }
    )
    legacy_hash = request_hash(
        {
            "issue_type": "device_fault",
            "issue_summary": "Pump does not turn on",
            "product_model": "Air1",
            "order_number": "",
            "purchase_channel": "",
            "urgency": "normal",
            "source": "agent",
            "payload": {},
        }
    )
    assert idempotency_service.reserve_kwargs["request_hash"] == expected_hash
    assert idempotency_service.reserve_kwargs["request_hash"] != legacy_hash


def _ticket(*, owner_user_id: UUID) -> SupportTicket:
    return SupportTicket(
        id=uuid4(),
        owner_user_id=owner_user_id,
        ticket_number="ticket_test",
        status="submitted",
        issue_type="device_fault",
        issue_summary="Pump does not turn on",
        product_model="Air1",
        order_number="",
        purchase_channel="",
        user_contact="",
        urgency="normal",
        source="agent",
        payload={},
    )


class FakeSupportTicketsRepository:
    def __init__(self, *, ticket=None) -> None:
        self.ticket = ticket

    async def create_ticket(self, **kwargs):
        self.ticket = _ticket(owner_user_id=kwargs["owner_user_id"])
        for field in (
            "ticket_number",
            "issue_type",
            "issue_summary",
            "product_model",
            "order_number",
            "purchase_channel",
            "user_contact",
            "urgency",
            "source",
            "payload",
            "submitted_at",
        ):
            setattr(self.ticket, field, kwargs[field])
        return self.ticket

    async def get_for_owner(self, **kwargs):
        return self.ticket

    async def list_for_owner(self, **kwargs):
        return [self.ticket] if self.ticket else []


class FakeIdempotencyService:
    def __init__(self, *, status: str, response_ref: str = "") -> None:
        self.status = status
        self.reserve_kwargs = {}
        self.completed_response_ref = ""
        self.record = IdempotencyKey(
            actor_user_id=uuid4(),
            scope="support.tickets.create",
            key="idem-ticket",
            request_hash="hash",
            response_ref=response_ref,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    async def reserve(self, **kwargs):
        self.reserve_kwargs = kwargs
        return FakeIdempotencyDecision(status=self.status, record=self.record)

    async def mark_completed(self, *, record, response_ref: str):
        self.completed_response_ref = response_ref
        record.response_ref = response_ref
        return record


class FakeIdempotencyDecision:
    def __init__(self, *, status: str, record) -> None:
        self.status = status
        self.record = record


class FakeAuditService:
    def __init__(self) -> None:
        self.record_kwargs = {}

    async def record(self, **kwargs):
        self.record_kwargs = kwargs
        return None
