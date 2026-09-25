from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.factory import create_app
from app.core.settings import Settings
from app.modules.profiles.me_schemas import MeProfilePatch, Concern, Observation, RecordOrder
from app.modules.profiles.me_models import MePreferences, MotherObservation


def test_all_me_endpoints_require_authentication():
    client = TestClient(create_app(Settings(app_env="test")))
    for method, path in [
        ("get", ""),
        ("patch", "/profile"),
        ("put", "/order"),
        ("put", f"/concerns/{uuid4()}"),
        ("put", f"/records/{uuid4()}"),
    ]:
        response = getattr(client, method)("/v1/profile/me-experience" + path)
        assert response.status_code == 401


def test_me_profile_only_accepts_editable_fields():
    parsed = MeProfilePatch.model_validate(
        {
            "preferred_name": " Mia ",
            "gestation_weeks": 39,
            "gestation_days": 2,
            "baby_count": 2,
            "caregivers": ["partner", "self"],
            "feeding_methods": ["direct", "expressed"],
        }
    )
    assert parsed.preferred_name == "Mia"
    assert parsed.caregivers == ["partner", "self"]
    assert "age" not in parsed.model_fields_set
    for value in [{}, {"age": 0}, {"gestation_days": 7}, {"baby_count": 0}, {"is_admin": True}, {"additional_context": "x" * 501}]:
        with pytest.raises(ValidationError):
            MeProfilePatch.model_validate(value)


def test_no_pause_state_and_no_duplicate_issues():
    key = uuid4()
    assert Concern(id=key, issues=["comfort", "feeding"]).reminder is True
    with pytest.raises(ValidationError):
        Concern(id=key, issues=["comfort"], paused=True)
    with pytest.raises(ValidationError):
        Concern(id=key, issues=["comfort", "comfort"])


def test_order_requires_unique_daily_metrics():
    assert RecordOrder(order=["energy", "feed", "sleep", "mood"]).order[0] == "energy"
    for order in [["feed", "feed", "sleep", "mood"], ["feed", "energy", "sleep"], ["feed", "energy", "sleep", "mood", "not_a_record"]]:
        with pytest.raises(ValidationError):
            RecordOrder(order=order)


def test_observations_reject_invalid_or_future_inputs():
    base = {"id": uuid4(), "kind": "energy", "occurred_at": datetime.now(timezone.utc) - timedelta(minutes=1), "value": "有力气"}
    assert Observation(**base).value == "有力气"
    for changes in [
        {"value": "unknown"},
        {"occurred_at": datetime.now()},
        {"occurred_at": datetime.now(timezone.utc) + timedelta(days=1)},
        {"kind": "feed"},
        {"kind": "pain"},
    ]:
        with pytest.raises(ValidationError):
            Observation(**{**base, **changes})


@pytest.mark.parametrize(
    ("kind", "value", "fields"),
    [
        ("energy", "Energized", {}),
        ("sleep", "3–4 hours", {}),
        ("mood", "Having a hard day", {}),
        ("latch", "Stayed latched", {}),
        ("bottle", "Took some", {}),
        ("pain", "3 / 10", {"pain": 3, "side": "Left side", "phase": "When latching", "impact": "Needed a break", "swallow": "Not sure"}),
        ("storage", "60 ml", {"volume_ml": 60, "action": "Add a bag"}),
        ("pump", "60 ml", {"volume_ml": 60, "side": "Both sides"}),
    ],
)
def test_observations_accept_english_labels_without_changing_legacy_contract(kind, value, fields):
    observation = Observation(
        id=uuid4(),
        kind=kind,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        value=value,
        fields=fields,
    )
    assert observation.value == value
    assert observation.fields.model_dump(exclude_unset=True) == fields


def test_keys_isolate_owners_and_observations():
    assert list(MePreferences.__table__.primary_key.columns.keys()) == ["owner_user_id"]
    assert set(MotherObservation.__table__.primary_key.columns.keys()) == {"owner_user_id", "id"}


def test_pump_retries_reuse_canonical_record_and_conflicting_retry_is_rejected(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from starlette.requests import Request
    from app.modules.auth import CurrentUser
    from app.modules.profiles import me_router
    from app.core.errors import ApiError

    class Session:
        record = None

        async def get(self, model, key):
            return self.record

        def add(self, value):
            self.record = value

        async def flush(self):
            pass

    async def run():
        owner = uuid4()
        current = CurrentUser(owner, str(owner), 'session', 'token', frozenset(), frozenset())
        session = Session()
        service = SimpleNamespace(create_pumping=AsyncMock(return_value=SimpleNamespace(id=uuid4())))
        monkeypatch.setattr(me_router, 'get_records_service', lambda _: service)
        monkeypatch.setattr(me_router, 'ProfileRepository', lambda _: SimpleNamespace(lock_profile_owner=AsyncMock()))
        monkeypatch.setattr(me_router, '_audit', AsyncMock())
        request = Request({'type': 'http'})
        payload = dict(id=uuid4(), kind='pump', occurred_at=datetime.now(timezone.utc) - timedelta(minutes=5), value='80 ml', fields={'volume_ml': 80, 'side': '两侧'})
        first = await me_router.put_record(payload['id'], Observation(**payload), request, current, session)
        retry = await me_router.put_record(payload['id'], Observation(**payload), request, current, session)
        assert first.fields.canonical_record_id == retry.fields.canonical_record_id
        assert service.create_pumping.await_count == 1
        with pytest.raises(ApiError) as error:
            await me_router.put_record(payload['id'], Observation(**{**payload, 'value': '90 ml', 'fields': {'volume_ml': 90, 'side': '两侧'}}), request, current, session)
        assert error.value.status == 409
        assert service.create_pumping.await_count == 1

    asyncio.run(run())
