from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.dependencies import require_current_user
from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.stool_photos.router import get_service, stool_photo_review_available
from app.modules.stool_photos.rules import validate_original
from test_stool_photo_rules import image_bytes


class FakeService:
    def __init__(self, owner):
        self.owner = owner
        self.photo = None
        self.uploads = 0

    async def upload(self, *, owner, body, key, request_id):
        assert owner == self.owner and key == "once"
        meta = validate_original(body)
        assert meta.content_type == "image/png"
        self.uploads += 1
        self.photo = SimpleNamespace(
            id=uuid4(), owner_user_id=owner, object_key="secret", review_status="pending",
            reason_codes=[], policy_version=None, reviewed_at=None, review_expires_at=None,
        )
        return self.photo

    async def delete(self, *, owner, photo_id, request_id):
        if owner != self.owner or self.photo is None or photo_id != self.photo.id:
            raise ApiError(code="not_found", message="Photo not found.", status=404)
        self.photo = None

    async def get(self, *, owner, photo_id):
        if owner != self.owner or self.photo is None or photo_id != self.photo.id:
            raise ApiError(code="not_found", message="Photo not found.", status=404)
        return self.photo


def test_stool_upload_requires_auth_and_owner_review_returns_no_private_metadata():
    owner = uuid4()
    app = create_app(Settings(app_env="test"))
    service = FakeService(owner)
    app.dependency_overrides[get_service] = lambda: service
    unauthenticated = TestClient(app).post(
        "/v1/stool-photos", files={"file": ("photo.jpg", image_bytes(image_format="PNG"))},
        headers={"Idempotency-Key": "once"},
    )
    assert unauthenticated.status_code == 401
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    app.dependency_overrides[stool_photo_review_available] = lambda: True
    client = TestClient(app)
    uploaded = client.post("/v1/stool-photos", files={"file": ("photo.jpg", image_bytes(image_format="PNG"))},
                           headers={"Idempotency-Key": "once"})
    assert uploaded.status_code == 202
    assert uploaded.json()["review_status"] == "pending"
    assert "object_key" not in uploaded.text and "sha256" not in uploaded.text
    assert service.uploads == 1
    photo_id = uploaded.json()["photo_id"]
    assert uploaded.json()["review_id"] == photo_id
    review = client.get(f"/v1/stool-photos/{photo_id}/review")
    assert review.status_code == 200 and review.json()["review_status"] == "pending"
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=uuid4())
    assert client.get(f"/v1/stool-photos/{photo_id}/review").status_code == 404
    assert client.delete(f"/v1/stool-photos/{photo_id}").status_code == 404
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    assert client.delete(f"/v1/stool-photos/{photo_id}").status_code == 204
    assert client.get(f"/v1/stool-photos/{photo_id}/review").status_code == 404


def test_stool_upload_rejects_too_large_before_service_call():
    app = create_app(Settings(app_env="test"))
    owner = uuid4()
    service = FakeService(owner)
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[stool_photo_review_available] = lambda: True
    response = TestClient(app).post("/v1/stool-photos",
        files={"file": ("photo.jpg", b"x" * (10 * 1024 * 1024 + 1))},
        headers={"Idempotency-Key": "once"})
    assert response.status_code == 413 and service.uploads == 0


def test_review_provider_requires_explicit_privacy_approval_and_key():
    import pytest
    for settings in (
        Settings(app_env="test", stool_photo_review_provider="decisions"),
        Settings(app_env="test", stool_photo_review_provider="decisions", stool_photo_third_party_approved=True),
    ):
        with pytest.raises(ValueError):
            settings.validate_for_startup()
    Settings(app_env="test", stool_photo_review_provider="disabled").validate_for_startup()


def test_stool_capability_disables_capture_without_calibrated_review_policy():
    owner = uuid4()
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    response = TestClient(app).get('/v1/stool-photos/capability')
    assert response.status_code == 200
    assert response.json() == {'review_available': False, 'analysis_available': False, 'followup_available': False}



def test_followup_capability_requires_both_analysis_and_followup_gates():
    owner = uuid4()
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    for analysis, followup, expected in ((False, False, False), (True, False, False), (True, True, True)):
        app.state.settings = Settings(app_env="test", stool_analysis_enabled=analysis, stool_followup_enabled=followup)
        response = TestClient(app).get('/v1/stool-photos/capability')
        assert response.status_code == 200
        assert response.json()['followup_available'] is expected


def test_stool_upload_fails_closed_when_review_unavailable_even_for_valid_authenticated_photo():
    owner = uuid4()
    app = create_app(Settings(app_env="test"))
    service = FakeService(owner)
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(user_id=owner)
    app.dependency_overrides[get_service] = lambda: service
    response = TestClient(app).post(
        "/v1/stool-photos", files={"file": ("private.png", image_bytes(image_format="PNG"))},
        headers={"Idempotency-Key": "once"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "stool_photo_review_unavailable"
    assert service.uploads == 0
    assert TestClient(app).get("/v1/stool-photos/capability").json() == {
        "review_available": False, "analysis_available": False, "followup_available": False,
    }
