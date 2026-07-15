from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.support.models import SupportTicket
from production_backend.app.modules.support.router import get_support_tickets_service


def test_support_tickets_require_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).get("/v1/support/tickets")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_create_support_ticket_uses_current_user_and_production_contract() -> None:
    user_id = uuid4()
    fake_service = FakeSupportTicketsService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_support_tickets_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/support/tickets",
        headers={"Idempotency-Key": " idem-ticket ", "X-Request-ID": "req_ticket"},
        json={
            "issue_type": "device_fault",
            "issue_summary": "Pump does not turn on",
            "product_model": "Air1",
            "urgency": "high",
            "thread_id": "thread-1",
            "payload": {"origin": "agent_action"},
        },
    )

    assert response.status_code == 201
    assert response.json()["issue_type"] == "device_fault"
    assert fake_service.create_kwargs["owner_user_id"] == user_id
    assert fake_service.create_kwargs["idempotency_key"] == "idem-ticket"
    assert fake_service.create_kwargs["request_id"] == "req_ticket"
    assert fake_service.create_kwargs["metadata"]["thread_id"] == "thread-1"
    assert fake_service.create_kwargs["payload"] == {"origin": "agent_action"}


def test_create_support_ticket_rejects_legacy_ticket_object_shape() -> None:
    user_id = uuid4()
    fake_service = FakeSupportTicketsService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_support_tickets_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/support/tickets",
        json={
            "ticket": {
                "issue_type": "device_fault",
                "issue_summary": "Pump does not turn on",
            }
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert fake_service.create_kwargs == {}


def test_list_and_get_support_tickets_use_current_user_scope() -> None:
    user_id = uuid4()
    ticket_id = uuid4()
    fake_service = FakeSupportTicketsService(user_id=user_id, ticket_id=ticket_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_support_tickets_service] = lambda: fake_service

    list_response = TestClient(app).get("/v1/support/tickets?status=submitted&limit=10")
    get_response = TestClient(app).get(f"/v1/support/tickets/{ticket_id}")

    assert list_response.status_code == 200
    assert get_response.status_code == 200
    assert fake_service.list_kwargs["owner_user_id"] == user_id
    assert fake_service.list_kwargs["status"] == "submitted"
    assert fake_service.list_kwargs["limit"] == 10
    assert fake_service.get_kwargs["ticket_id"] == ticket_id


def _override_current_user(app, user_id: UUID) -> None:
    from production_backend.app.api.dependencies import require_current_user

    async def fake_current_user() -> CurrentUser:
        return CurrentUser(
            user_id=user_id,
            subject=str(user_id),
            session_id="session",
            token_id="token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        )

    app.dependency_overrides[require_current_user] = fake_current_user


class FakeSupportTicketsService:
    def __init__(self, *, user_id: UUID, ticket_id: UUID | None = None) -> None:
        self.user_id = user_id
        self.ticket_id = ticket_id or uuid4()
        self.create_kwargs = {}
        self.list_kwargs = {}
        self.get_kwargs = {}

    async def create_ticket(self, **kwargs):
        self.create_kwargs = kwargs
        return self._ticket()

    async def list_tickets(self, **kwargs):
        self.list_kwargs = kwargs
        return [self._ticket()]

    async def get_ticket(self, **kwargs):
        self.get_kwargs = kwargs
        return self._ticket()

    def _ticket(self) -> SupportTicket:
        return SupportTicket(
            id=self.ticket_id,
            owner_user_id=self.user_id,
            ticket_number="ticket_test",
            status="submitted",
            issue_type="device_fault",
            issue_summary="Pump does not turn on",
            product_model="Air1",
            order_number="",
            purchase_channel="",
            user_contact="",
            urgency="high",
            source="agent",
            payload={},
        )
