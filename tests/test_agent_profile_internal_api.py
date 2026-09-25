from datetime import date
from uuid import uuid4
from fastapi.testclient import TestClient
from app.core.settings import Settings
from app.factory import create_app
from app.modules.profiles.agent_router import get_agent_profile_read_service

SERVICE_KEY = "agent-runtime-service-key-with-32-bytes"

def test_agent_profile_read_uses_service_identity_and_explicit_actor_scope() -> None:
    actor_user_id = uuid4()
    service = FakeAgentProfileReadService()
    app = _app()
    app.dependency_overrides[get_agent_profile_read_service] = lambda: service

    response = TestClient(app).get(
        "/v1/internal/agent/profile",
        headers={"X-Service-Key": SERVICE_KEY},
        params={
            "actor_user_id": str(actor_user_id),
            "infant_scope": "all",
            "as_of_date": "2026-07-26",
            "timezone": "Asia/Shanghai",
        },
    )

    assert response.status_code == 200
    assert response.json()["infant_scope"] == "all"
    assert service.kwargs == {
        "owner_user_id": actor_user_id,
        "as_of_date": date(2026, 7, 26),
        "infant_scope": "all",
        "timezone": "Asia/Shanghai",
    }


def test_agent_profile_read_rejects_invalid_timezone() -> None:
    service = FakeAgentProfileReadService()
    app = _app()
    app.dependency_overrides[get_agent_profile_read_service] = lambda: service
    response = TestClient(app).get("/v1/internal/agent/profile",
        headers={"X-Service-Key": SERVICE_KEY},
        params={"actor_user_id": str(uuid4()), "timezone": "Bad/Zone"})
    assert response.status_code == 422
    assert service.kwargs == {}


def test_agent_profile_internal_routes_reject_missing_service_identity() -> None:
    response = TestClient(_app()).get(
        "/v1/internal/agent/profile",
        params={"actor_user_id": str(uuid4())},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def _app():
    return create_app(
        Settings(
            app_env="test",
            agent_runtime_service_api_key=SERVICE_KEY,
        )
    )


class FakeAgentProfileReadService:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def read(self, **kwargs):
        self.kwargs = kwargs
        return {
            "as_of_date": kwargs["as_of_date"] or date.today(),
            "infant_scope": kwargs["infant_scope"],
            "mother": {
                "preferred_name": None,
                "age": None,
                "delivery_count": None,
                "current_delivery_method": None,
                "actual_delivery_date": None,
                "has_cesarean_history": None,
                "postpartum_days": None,
                "current_feeding_mode": None,
            },
            "infants": [],
            "missing_fields": [],
            "data_quality_issues": [],
        }


def test_topical_records_require_service_identity_and_preserve_actor_scope() -> None:
    from app.modules.profiles.agent_router import get_agent_topical_records_service
    from app.modules.profiles.topical_records import TopicalRecordsReadOutput

    actor, infant = uuid4(), uuid4()
    class FakeRecords:
        query = None

        async def read(self, query):
            self.query = query
            return TopicalRecordsReadOutput(topic=query.topic, infant_id=query.infant_id,
                start_date=query.start_date, end_date=query.end_date, timezone=query.timezone,
                items=[], has_more=False)

    service = FakeRecords()
    app = _app()
    app.dependency_overrides[get_agent_topical_records_service] = lambda: service
    params = {"actor_user_id": str(actor), "infant_id": str(infant), "topic": "growth",
        "start_date": "2026-09-01", "end_date": "2026-09-24", "timezone": "Asia/Shanghai", "limit": 5}
    client = TestClient(app)
    assert client.get("/v1/internal/agent/records", params=params).status_code == 401
    response = client.get("/v1/internal/agent/records", params=params, headers={"X-Service-Key": SERVICE_KEY})
    assert response.status_code == 200
    assert service.query.actor_user_id == actor
    assert service.query.infant_id == infant
    assert service.query.limit == 5
    assert response.json()["coverage"] == "recorded_entries_only"


def test_topical_records_reject_invalid_scope_and_unbounded_window() -> None:
    from app.modules.profiles.agent_router import get_agent_topical_records_service

    app = _app()
    app.dependency_overrides[get_agent_topical_records_service] = lambda: object()
    client = TestClient(app)
    base = {"actor_user_id": str(uuid4()), "topic": "growth", "timezone": "UTC",
        "start_date": "2026-08-01", "end_date": "2026-09-01"}
    for params in (base, {**base, "infant_id": str(uuid4()), "limit": 21},
                   {**base, "infant_id": str(uuid4()), "timezone": "unknown"}):
        response = client.get("/v1/internal/agent/records", params=params, headers={"X-Service-Key": SERVICE_KEY})
        assert response.status_code == 422
