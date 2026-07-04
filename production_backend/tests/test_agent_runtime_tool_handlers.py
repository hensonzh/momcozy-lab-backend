import asyncio
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import AgentAction
from production_backend.app.modules.agent_runtime.tools import (
    BusinessContextReadToolHandler,
    HospitalBagCartUpdateProposeToolHandler,
    MilkSummaryReadToolHandler,
    ProfileReadToolHandler,
    SupportTicketProposeToolHandler,
    ToolHandlerContext,
    build_default_tool_handlers,
)
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.devices.models import PumpDevice, PumpTelemetryEvent
from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.modules.plans.models import Plan, PlanTask
from production_backend.app.modules.profiles.models import InfantProfile, UserProfile
from production_backend.app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from production_backend.app.modules.records.schemas import MilkTrendDayRead, MilkTrendListResponse


def test_profile_read_tool_handler_returns_safe_context_projection() -> None:
    actor = _user()
    profile = UserProfile(
        user_id=actor.user_id,
        display_name="Mai",
        age=31,
        delivery_date=date(2026, 9, 20),
        lactation_advice="Hydrate",
        feeding_advice="Track feeds",
        daily_summary="Doing well",
    )
    infant = InfantProfile(
        id=uuid4(),
        owner_user_id=actor.user_id,
        infant_name="Nori",
        sex="female",
        birth_date=date(2026, 1, 10),
        status="active",
    )
    handler = ProfileReadToolHandler(service=FakeProfileService(profile=profile, infants=[infant]))

    result = asyncio.run(handler(_context(actor=actor, args={})))

    assert result["profile"]["user_id"] == str(actor.user_id)
    assert result["profile"]["delivery_date"] == "2026-09-20"
    assert result["profile"]["profile_onboarding_complete"] is True
    assert result["infants"] == [
        {
            "id": str(infant.id),
            "owner_user_id": str(actor.user_id),
            "infant_name": "Nori",
            "sex": "female",
            "birth_date": "2026-01-10",
            "status": "active",
        }
    ]


def test_support_ticket_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    handler = SupportTicketProposeToolHandler(runtime_service=runtime_service)
    context = _context(
        actor=actor,
        args={
            "issue_type": "pump",
            "issue_summary": "Pump does not start",
            "product_model": "M9",
            "user_contact": "mai@example.com",
            "locale": "en-US",
        },
    )

    result = asyncio.run(handler(context))

    assert result["action_id"] == str(runtime_service.action.id)
    assert result["action_type"] == "support.ticket.create"
    assert result["action_status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert result["preview_payload"]["has_user_contact"] is True
    assert "user_contact" not in result["preview_payload"]
    assert runtime_service.calls[0]["owner_user_id"] == actor.user_id
    assert runtime_service.calls[0]["run_id"] == context.run_id
    assert runtime_service.calls[0]["apply_payload"]["user_contact"] == "mai@example.com"
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"locale": "en-US"}


def test_hospital_bag_cart_update_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    handler = HospitalBagCartUpdateProposeToolHandler(runtime_service=runtime_service)
    context = _context(
        actor=actor,
        args={
            "cart_update": {
                "set_checked": [{"item_id": "nursing-bra", "checked": True}],
                "add_items": [{"item_id": "charger", "label": "Phone charger"}],
            },
            "summary": "Mark nursing bra packed and add a phone charger",
            "timezone": "Asia/Shanghai",
        },
    )

    result = asyncio.run(handler(context))

    assert result["action_id"] == str(runtime_service.action.id)
    assert result["action_type"] == "hospital_bag.cart.update"
    assert result["action_status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert result["preview_payload"]["summary"] == "Mark nursing bra packed and add a phone charger"
    assert result["preview_payload"]["cart_update"]["set_checked"][0]["item_id"] == "nursing-bra"
    assert runtime_service.calls[0]["owner_user_id"] == actor.user_id
    assert runtime_service.calls[0]["run_id"] == context.run_id
    assert runtime_service.calls[0]["target_type"] == "hospital_bag_cart"
    assert runtime_service.calls[0]["side_effect_level"] == "low"
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"timezone": "Asia/Shanghai"}


def test_business_context_read_tool_handler_returns_bounded_owner_scoped_summary() -> None:
    actor = _user()
    records_service = FakeRecordsService(owner_user_id=actor.user_id)
    plans_service = FakePlansService(owner_user_id=actor.user_id)
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)
    devices_service = FakeDevicesService(owner_user_id=actor.user_id)
    handler = BusinessContextReadToolHandler(
        records_service=records_service,
        plans_service=plans_service,
        diary_service=diary_service,
        devices_service=devices_service,
    )

    result = asyncio.run(handler(_context(actor=actor, args={"limit": 2, "owner_user_id": str(uuid4())})))

    assert records_service.owner_user_id == actor.user_id
    assert result["records"]["feedings"][0]["feed_time"] == "2026-07-02T08:00:00+00:00"
    assert result["records"]["pumpings"][0]["milk_volume_ml"] == 80
    assert result["records"]["growth"][0]["weight_kg"] == 6.2
    assert result["plans"]["plans"][0]["title"] == "Birth plan"
    assert result["plans"]["tasks"][0]["task_date"] == "2026-07-03"
    assert result["diary"]["entries"][0]["content_summary"].endswith("...")
    assert result["devices"]["pumps"][0]["device_id"] == "pump-1"
    assert result["devices"]["telemetry"][0]["payload"] == {"mode": "stimulation"}


def test_milk_summary_read_tool_handler_returns_bounded_owner_scoped_summary() -> None:
    actor = _user()
    records_service = FakeRecordsService(owner_user_id=actor.user_id)
    handler = MilkSummaryReadToolHandler(records_service=records_service)

    result = asyncio.run(handler(_context(actor=actor, args={"days": 3, "limit": 2, "owner_user_id": str(uuid4())})))

    assert records_service.owner_user_id == actor.user_id
    assert result["window"] == {"days": 3, "include_today": True}
    assert result["recent_feedings"][0]["volume_ml"] == 60
    assert result["recent_pumpings"][0]["milk_volume_ml"] == 80
    assert result["pumping_trends"] == [
        {
            "date": "2026-07-01",
            "pumped_milk_volume_ml": 80,
            "pumping_count": 1,
            "measured_only": True,
        },
        {
            "date": "2026-07-02",
            "pumped_milk_volume_ml": 90,
            "pumping_count": 2,
            "measured_only": True,
        },
    ]
    assert result["totals"] == {
        "recent_feeding_volume_ml": 60.0,
        "recent_pumped_volume_ml": 80.0,
        "trend_pumped_volume_ml": 170.0,
        "trend_pumping_count": 3,
    }


def test_support_ticket_propose_tool_handler_requires_summary() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(SupportTicketProposeToolHandler(runtime_service=FakeAgentRuntimeService())(_context(args={})))

    assert exc_info.value.code == "validation_failed"


def test_hospital_bag_cart_update_propose_tool_handler_requires_cart_update() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(HospitalBagCartUpdateProposeToolHandler(runtime_service=FakeAgentRuntimeService())(_context(args={})))

    assert exc_info.value.code == "validation_failed"


def test_support_ticket_propose_tool_handler_rejects_legacy_nested_ticket_shape() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            SupportTicketProposeToolHandler(runtime_service=FakeAgentRuntimeService())(
                _context(args={"ticket": {"issue_summary": "Pump does not start"}})
            )
        )

    assert exc_info.value.code == "validation_failed"


def test_build_default_tool_handlers_wires_registered_tool_names() -> None:
    actor_id = uuid4()
    handlers = build_default_tool_handlers(
        profile_service=FakeProfileService(profile=None, infants=[]),
        records_service=FakeRecordsService(owner_user_id=actor_id),
        plans_service=FakePlansService(owner_user_id=actor_id),
        diary_service=FakeDiaryService(owner_user_id=actor_id),
        devices_service=FakeDevicesService(owner_user_id=actor_id),
        agent_runtime_service=FakeAgentRuntimeService(),
    )

    assert set(handlers) == {
        "profile.read",
        "business.context.read",
        "records.milk_summary.read",
        "hospital_bag.cart_update.propose",
        "support.ticket.propose",
    }


def _context(*, actor: CurrentUser | None = None, args: dict | None = None) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=actor or _user(),
        run_id=uuid4(),
        tool_name="tool",
        call_id="call-1",
        args=args or {},
    )


def _user() -> CurrentUser:
    user_id = uuid4()
    return CurrentUser(
        user_id=user_id,
        subject=str(user_id),
        session_id="session",
        token_id="token",
        roles=frozenset({"user"}),
        permissions=frozenset(
            {
                "profile:read:self",
                "business_context:read:self",
                "hospital_bag_cart:update:self",
                "support_ticket:create:self",
            }
        ),
    )


class FakeProfileService:
    def __init__(self, *, profile: UserProfile | None, infants: list[InfantProfile]) -> None:
        self.profile = profile
        self.infants = infants

    async def get_user_profile(self, *, user_id):
        return self.profile

    async def list_infants(self, *, owner_user_id):
        return self.infants


class FakeRecordsService:
    def __init__(self, *, owner_user_id) -> None:
        self.owner_user_id = None
        self._owner_user_id = owner_user_id

    async def list_feedings(self, *, owner_user_id, limit):
        self.owner_user_id = owner_user_id
        return [
            FeedingRecord(
                id=uuid4(),
                owner_user_id=self._owner_user_id,
                infant_id=None,
                feed_time=_now(),
                feed_type="bottle",
                feed_action="fed",
                volume_ml=60,
                duration_seconds=None,
                title="Morning feed",
            )
        ]

    async def list_pumpings(self, *, owner_user_id, limit):
        return [
            PumpingRecord(
                id=uuid4(),
                owner_user_id=self._owner_user_id,
                pump_start_time=_now(),
                pump_end_time=None,
                milk_volume_ml=80,
                pump_type="electric",
                duration_seconds=900,
                source="device",
                title="Pump session",
            )
        ]

    async def list_growth(self, *, owner_user_id, limit):
        return [
            GrowthRecord(
                id=uuid4(),
                owner_user_id=self._owner_user_id,
                infant_id=None,
                measured_at=_now(),
                height_cm=62,
                weight_kg=6.2,
                head_cm=None,
            )
        ]

    async def get_milk_trends(self, *, owner_user_id, days, include_today):
        return MilkTrendListResponse(
            days=days,
            include_today=include_today,
            items=[
                MilkTrendDayRead(date=date(2026, 7, 1), pumped_milk_volume_ml=80, pumping_count=1),
                MilkTrendDayRead(date=date(2026, 7, 2), pumped_milk_volume_ml=90, pumping_count=2),
            ],
        )


class FakePlansService:
    def __init__(self, *, owner_user_id) -> None:
        self._owner_user_id = owner_user_id

    async def list_plans(self, *, owner_user_id, limit):
        return [
            Plan(
                id=uuid4(),
                owner_user_id=self._owner_user_id,
                plan_type="birth_journey",
                title="Birth plan",
                summary="Pack hospital bag",
                status="active",
                source="manual",
                updated_at=_now(),
            )
        ]

    async def list_tasks(self, *, owner_user_id, limit):
        return [
            PlanTask(
                id=uuid4(),
                owner_user_id=self._owner_user_id,
                plan_id=None,
                task_date=date(2026, 7, 3),
                task_time="09:00",
                title="Call clinic",
                status="pending",
            )
        ]


class FakeDiaryService:
    def __init__(self, *, owner_user_id) -> None:
        self._owner_user_id = owner_user_id

    async def list_entries(self, *, owner_user_id, limit):
        return [
            PregnancyDiaryEntry(
                id=uuid4(),
                owner_user_id=self._owner_user_id,
                entry_date=date(2026, 7, 2),
                gestational_week="32w",
                mood="calm",
                energy_level="medium",
                symptom_tags=["backache"],
                content="x" * 600,
            )
        ]


class FakeDevicesService:
    def __init__(self, *, owner_user_id) -> None:
        self._owner_user_id = owner_user_id

    async def list_devices(self, *, owner_user_id):
        return [
            PumpDevice(
                id=uuid4(),
                owner_user_id=self._owner_user_id,
                device_id="pump-1",
                model="M9",
                firmware_version="1.0",
                status="active",
                last_seen_at=_now(),
            )
        ]

    async def list_telemetry_events(self, *, owner_user_id, limit):
        return [
            PumpTelemetryEvent(
                id=uuid4(),
                owner_user_id=self._owner_user_id,
                device_id="pump-1",
                event_type="mode",
                occurred_at=_now(),
                payload={"mode": "stimulation"},
            )
        ]


class FakeAgentRuntimeService:
    def __init__(self) -> None:
        self.calls = []
        self.action = AgentAction(
            id=uuid4(),
            run_id=uuid4(),
            actor_user_id=uuid4(),
            action_type="support.ticket.create",
            target_type="support_ticket",
            target_id="",
            status="confirmation_required",
            side_effect_level="medium",
            preview_payload={},
            apply_payload={},
            idempotency_key="",
            error_code="",
        )

    async def propose_action(self, **kwargs):
        self.calls.append(kwargs)
        self.action.run_id = kwargs["run_id"]
        self.action.actor_user_id = kwargs["owner_user_id"]
        self.action.action_type = kwargs["action_type"]
        self.action.target_type = kwargs["target_type"]
        self.action.side_effect_level = kwargs["side_effect_level"]
        self.action.preview_payload = kwargs["preview_payload"]
        self.action.apply_payload = kwargs["apply_payload"]
        self.action.idempotency_key = kwargs["idempotency_key"]
        return self.action


def _now() -> datetime:
    return datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc)
