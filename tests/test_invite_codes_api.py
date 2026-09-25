from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from app.modules.invites.models import InviteCode
from app.modules.invites.router import get_invite_code_service


SERVICE_KEY = "service-key-value-with-at-least-32-bytes"


def test_invite_codes_admin_ui_returns_lightweight_page() -> None:
    response = TestClient(create_app(Settings(app_env="test", service_api_key=SERVICE_KEY))).get("/v1/admin/invite-codes/ui")

    assert response.status_code == 200
    assert "Create invitation code" in response.text
    assert "Invitation codes" in response.text
    assert 'id="serviceKey"' not in response.text
    assert "Result" not in response.text
    assert 'const SERVICE_KEY = "service-key-value-with-at-least-32-bytes";' in response.text
    assert "<th>Invitation code</th>" in response.text
    assert "<th>Bound</th>" in response.text
    assert "<th>Available</th>" in response.text
    assert "<th>Assigned to</th>" in response.text
    assert "<th>Expires</th>" in response.text
    assert "<th>Actions</th>" in response.text
    assert "loadInviteCodes();" in response.text
    assert "disableInviteCode(item.code)" in response.text
    assert "Previous page" in response.text
    assert "Next page" in response.text
    assert "Disabling a bound code also signs the user out" in response.text
    assert "Sign out user" in response.text
    assert "New codes appear at the top of the table below" in response.text


def test_invite_code_admin_api_requires_service_key() -> None:
    response = TestClient(create_app(Settings(app_env="test", service_api_key=SERVICE_KEY))).post(
        "/v1/admin/invite-codes",
        json={"label": "Alice"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_admin_creates_lists_and_disables_invite_code() -> None:
    fake_service = FakeInviteCodeService()
    app = create_app(Settings(app_env="test", service_api_key=SERVICE_KEY))
    app.dependency_overrides[get_invite_code_service] = lambda: fake_service
    client = TestClient(app)

    create_response = client.post(
        "/v1/admin/invite-codes",
        headers={"X-Service-Key": SERVICE_KEY},
        json={"label": "Alice 内测", "assigned_to": "alice@example.test"},
    )
    list_response = client.get("/v1/admin/invite-codes?limit=20&offset=40", headers={"X-Service-Key": SERVICE_KEY})
    disable_response = client.post("/v1/admin/invite-codes/MCZ-TEST-0001/disable", headers={"X-Service-Key": SERVICE_KEY})

    assert create_response.status_code == 201
    assert create_response.json()["code"] == "MCZ-TEST-0001"
    assert create_response.json()["is_bound"] is False
    assert create_response.json()["is_available"] is True
    assert fake_service.create_kwargs["label"] == "Alice 内测"
    assert fake_service.create_kwargs["assigned_to"] == "alice@example.test"
    assert fake_service.create_kwargs["actor_service"] == "internal-service"
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["code"] == "MCZ-TEST-0001"
    assert list_response.json()["total"] == 55
    assert list_response.json()["limit"] == 20
    assert list_response.json()["offset"] == 40
    assert list_response.json()["has_more"] is False
    assert fake_service.list_kwargs == {"limit": 20, "offset": 40}
    assert disable_response.status_code == 200
    assert disable_response.json()["status"] == "disabled"
    assert disable_response.json()["is_available"] is False
    assert fake_service.disable_code == "MCZ-TEST-0001"


class FakeInviteCodeService:
    def __init__(self) -> None:
        self.create_kwargs = {}
        self.list_kwargs = {}
        self.disable_code = ""
        self.invite_code = _invite_code(status="active")

    async def create_invite_code(self, **kwargs):
        self.create_kwargs = kwargs
        self.invite_code = _invite_code(status="active")
        return self.invite_code

    async def list_invite_codes(self, **kwargs):
        self.list_kwargs = kwargs
        from app.modules.invites.service import InviteCodePage

        return InviteCodePage(items=[self.invite_code], total=55, limit=kwargs["limit"], offset=kwargs["offset"])

    async def disable_invite_code(self, *, code: str):
        self.disable_code = code
        self.invite_code.status = "disabled"
        self.invite_code.disabled_at = datetime(2026, 7, 7, tzinfo=timezone.utc)
        return self.invite_code


def _invite_code(*, status: str) -> InviteCode:
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    return InviteCode(
        id=uuid4(),
        code="MCZ-TEST-0001",
        status=status,
        label="Alice 内测",
        assigned_to="alice@example.test",
        bound_device_id="",
        bound_user_id=None,
        created_by_service="internal-service",
        used_count=0,
        expires_at=datetime(2027, 7, 7, tzinfo=timezone.utc),
        created_at=now,
        updated_at=now,
    )
