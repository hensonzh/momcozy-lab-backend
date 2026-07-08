from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.invites.models import InviteCode
from production_backend.app.modules.invites.router import get_invite_code_service


SERVICE_KEY = "service-key-value-with-at-least-32-bytes"


def test_invite_codes_admin_ui_returns_lightweight_page() -> None:
    response = TestClient(create_app(Settings(app_env="test", service_api_key=SERVICE_KEY))).get("/v1/admin/invite-codes/ui")

    assert response.status_code == 200
    assert "创建邀请码" in response.text
    assert "邀请码列表" in response.text
    assert "管理员凭证" not in response.text
    assert 'id="serviceKey"' not in response.text
    assert "结果" not in response.text
    assert 'const SERVICE_KEY = "service-key-value-with-at-least-32-bytes";' in response.text
    assert "<th>邀请码</th>" in response.text
    assert "<th>是否绑定</th>" in response.text
    assert "<th>是否可用</th>" in response.text
    assert "<th>分发对象</th>" in response.text
    assert "<th>有效期至</th>" in response.text
    assert "<th>操作</th>" in response.text
    assert "loadInviteCodes();" in response.text
    assert "disableInviteCode(item.code)" in response.text
    assert "上一页" in response.text
    assert "下一页" in response.text
    assert "创建成功后会直接新增到下方表格第一行" in response.text


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
        from production_backend.app.modules.invites.service import InviteCodePage

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
