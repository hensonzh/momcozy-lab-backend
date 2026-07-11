import asyncio
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import AgentAction, AgentArtifact
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools import (
    BusinessContextReadToolHandler,
    DeviceGuidanceAssetsReadToolHandler,
    DevicesPumpStatusReadToolHandler,
    FeedingRecordDeleteProposeToolHandler,
    FeedingRecordProposeToolHandler,
    GrowthRecordDeleteProposeToolHandler,
    GrowthRecordProposeToolHandler,
    GrowthRecordUpdateProposeToolHandler,
    GrowthRecordsReadToolHandler,
    HospitalBagCartUpdateProposeToolHandler,
    IbclcConsultCardCreateToolHandler,
    ImageInspectToolHandler,
    LegacyArtifactToolHandler,
    MilkAnalysisReadToolHandler,
    MilkPlanProposeToolHandler,
    MilkReminderProposeToolHandler,
    MilkSummaryReadToolHandler,
    MilkStatusReadToolHandler,
    PlanDeleteProposeToolHandler,
    PlanTaskCompleteProposeToolHandler,
    PlanTaskCreateProposeToolHandler,
    PlanTaskDeleteProposeToolHandler,
    PlanTaskUpdateProposeToolHandler,
    PlansCalendarReadToolHandler,
    PlansCurrentReadToolHandler,
    PregnancyDiaryEntriesReadToolHandler,
    PregnancyDiaryEntryCreateToolHandler,
    PregnancyDiaryEntryDeleteProposeToolHandler,
    PregnancyDiaryEntryUpdateToolHandler,
    ProfileReadToolHandler,
    ProfileUpdateToolHandler,
    PumpingRecordProposeToolHandler,
    PregnancyPlanContextReadToolHandler,
    PregnancyPlanProposeToolHandler,
    SupportTicketProposeToolHandler,
    ToolHandlerContext,
    ToolHandlerResult,
    build_default_tool_handlers,
)
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.assets.models import ProductAsset
from production_backend.app.modules.devices.models import PumpDevice, PumpTelemetryEvent
from production_backend.app.modules.diary.agent_actions import (
    PREGNANCY_DIARY_ENTRY_DELETE_ACTION,
)
from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.modules.notifications.agent_actions import MILK_REMINDER_CREATE_ACTION
from production_backend.app.modules.plans.agent_actions import (
    MILK_PLAN_CREATE_ACTION,
    PLAN_TASK_COMPLETE_ACTION,
    PLAN_TASK_CREATE_ACTION,
    PLAN_TASK_DELETE_ACTION,
    PLAN_TASK_UPDATE_ACTION,
    PLAN_DELETE_ACTION,
    PREGNANCY_PLAN_CREATE_ACTION,
)
from production_backend.app.modules.plans.models import Plan, PlanTask
from production_backend.app.modules.profiles.models import InfantProfile, UserProfile
from production_backend.app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from production_backend.app.modules.records.agent_actions import (
    FEEDING_RECORD_CREATE_ACTION,
    FEEDING_RECORD_DELETE_ACTION,
    GROWTH_RECORD_CREATE_ACTION,
    GROWTH_RECORD_DELETE_ACTION,
    GROWTH_RECORD_UPDATE_ACTION,
    PUMPING_RECORD_CREATE_ACTION,
)
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


def test_profile_update_tool_handler_updates_explicit_profile_fields() -> None:
    actor = _user()
    profile_service = FakeProfileService(profile=None, infants=[])
    handler = ProfileUpdateToolHandler(service=profile_service)

    result = asyncio.run(
        handler(
            _context(
                actor=actor,
                args={"display_name": " Mai ", "age": 31, "onboarding_skipped": True},
            )
        )
    )

    assert result["status"] == "profile_updated"
    assert result["updated_fields"] == ["age", "display_name", "profile_onboarding_skipped_at"]
    assert result["profile"]["display_name"] == "Mai"
    assert result["profile"]["age"] == 31
    assert result["profile"]["profile_onboarding_skipped"] is True
    assert profile_service.update_profile_kwargs["user_id"] == actor.user_id
    assert profile_service.update_profile_kwargs["request_id"] == "call-1"
    assert profile_service.update_profile_kwargs["values"]["display_name"] == "Mai"
    assert profile_service.update_profile_kwargs["values"]["age"] == 31
    assert profile_service.update_profile_kwargs["values"]["profile_onboarding_skipped_at"] is not None


def test_profile_update_tool_handler_requires_at_least_one_field() -> None:
    handler = ProfileUpdateToolHandler(service=FakeProfileService(profile=None, infants=[]))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(handler(_context(args={})))

    assert exc_info.value.code == "validation_failed"


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
    assert result["action_status"] == "confirmed"
    assert result["requires_confirmation"] is False
    assert result["preview_payload"]["summary"] == "Mark nursing bra packed and add a phone charger"
    assert result["preview_payload"]["cart_update"]["set_checked"][0]["item_id"] == "nursing-bra"
    assert runtime_service.calls[0]["owner_user_id"] == actor.user_id
    assert runtime_service.calls[0]["run_id"] == context.run_id
    assert runtime_service.calls[0]["target_type"] == "hospital_bag_cart"
    assert runtime_service.calls[0]["side_effect_level"] == "low"
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"timezone": "Asia/Shanghai"}


def test_legacy_artifact_tool_handler_returns_old_form_card_and_cart_envelopes() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()

    form_result = asyncio.run(
        LegacyArtifactToolHandler(runtime_service=runtime_service, tool_name="hospital_bag_form_create")(
            _context(actor=actor, args={"default_values": {"due_date_or_week": "36 周"}})
        )
    )
    assert form_result["tool_name"] == "ui_form_create"
    assert form_result["status"] == "form_created"
    assert form_result["form"]["id"] == "hospital_bag_intake"
    assert form_result["form"]["submit_label"] == "提交"

    card_result = asyncio.run(
        LegacyArtifactToolHandler(runtime_service=runtime_service, tool_name="hospital_bag_card_create")(
            _context(
                actor=actor,
                args={
                    "form_submission_id": "submission-1",
                    "confirmed_form_data": {
                        "due_date_or_week": "36 周",
                        "first_birth": "是",
                        "fetus_count": "单胎",
                        "pregnancy_history_or_notes": ["没有"],
                        "birth_path": "顺产",
                        "feeding_intention": "亲喂母乳",
                        "return_to_work_timing": "3 个月后",
                        "support_person": "有人全天帮忙",
                        "top_worries": ["怕漏买"],
                    },
                },
            )
        )
    )
    assert card_result["tool_name"] == "hospital_bag_card_create"
    assert card_result["status"] == "card_created"
    assert card_result["card"]["card_type"] == "hospital_bag_card"
    assert card_result["card"]["card_json"]["packing_groups"][0]["title"] == "证件文件包"
    assert card_result["assistant_followup"]["kind"] == "hospital_bag_cart"
    assert runtime_service.artifact.artifact_type == "hospital_bag_card"
    assert runtime_service.artifact.payload["card"]["card_json"]["title"] == "待产包"
    assert runtime_service.artifact.payload["source_form_submission_id"] == "submission-1"
    assert runtime_service.calls[-1]["emit_event"] is False

    cart_result = asyncio.run(
        LegacyArtifactToolHandler(runtime_service=runtime_service, tool_name="hospital_bag_cart_update")(
            _context(actor=actor, args={"action": "reset_cart"})
        )
    )
    assert cart_result["tool_name"] == "hospital_bag_cart_update"
    assert cart_result["status"] == "cart_updated"
    assert cart_result["cart_update"]["groups"][0]["items"][0]["name"] == "产褥垫组合装"
    assert cart_result["cart_update"]["groups"][0]["items"][0]["image_url"].startswith("https://")
    assert cart_result["cart_update"]["totals"]["itemCount"] == 18
    assert cart_result["cart_update"]["totals"]["exchange_rate_usd_cny"] == 6.8
    assert cart_result["cart_update"]["totals"]["total"] > 0


def test_legacy_artifact_tool_handler_matches_old_cart_and_pump_actions() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()

    pump_result = asyncio.run(
        LegacyArtifactToolHandler(runtime_service=runtime_service, tool_name="hospital_bag_pump_recommend")(
            _context(
                actor=actor,
                args={
                    "requested_model": "Air1",
                    "use_case": "work_pumping",
                    "preference": "portable",
                    "feeding_intention": "breastfeeding",
                },
            )
        )
    )
    assert pump_result["tool_name"] == "hospital_bag_pump_recommend"
    assert pump_result["status"] == "pump_recommended"
    assert pump_result["recommendation_mode"] == "requested_model_review"
    assert pump_result["recommended_product"]["sku_id"] == "pump-air-1"
    assert pump_result["recommended_product"]["price_position"] == "premium_highest"
    assert pump_result["cart_sync_suggestion"] == {
        "tool_name": "hospital_bag_cart_update",
        "action": "replace_pump_model",
        "product_sku_id": "pump-air-1",
        "item_ids": ["milk-pump"],
    }
    assert "不能把 Air 1 描述为降低预算" in pump_result["price_guidance"]

    replace_result = asyncio.run(
        LegacyArtifactToolHandler(runtime_service=runtime_service, tool_name="hospital_bag_cart_update")(
            _context(actor=actor, args={"action": "replace_pump_model", "product_sku_id": "pump-m9"})
        )
    )
    pump_items = [item for group in replace_result["cart_update"]["groups"] for item in group["items"] if item["id"] == "pump-m9"]
    assert pump_items
    assert pump_items[0]["official_price_usd"] == 159.99
    assert pump_items[0]["price_label"].startswith("¥")
    assert replace_result["cart_update"]["replaced_items"][0]["from_item_id"] == "milk-pump"

    budget_result = asyncio.run(
        LegacyArtifactToolHandler(runtime_service=runtime_service, tool_name="hospital_bag_cart_update")(
            _context(actor=actor, args={"action": "optimize_budget", "target_budget": 1000, "budget_mode": "under"})
        )
    )
    assert budget_result["cart_update"]["action"] == "optimize_budget"
    assert budget_result["cart_update"]["target_budget"] == 1000
    assert "before_totals" in budget_result["cart_update"]
    assert budget_result["cart_update"]["removed_item_ids"]
    assert budget_result["cart_update"]["totals"]["itemCount"] < budget_result["cart_update"]["before_totals"]["itemCount"]


def test_business_context_read_tool_handler_returns_bounded_owner_scoped_summary() -> None:
    actor = _user()
    records_service = FakeRecordsService(owner_user_id=actor.user_id)
    plans_service = FakePlansService(owner_user_id=actor.user_id)
    devices_service = FakeDevicesService(owner_user_id=actor.user_id)
    handler = BusinessContextReadToolHandler(
        records_service=records_service,
        plans_service=plans_service,
        devices_service=devices_service,
    )

    result = asyncio.run(handler(_context(actor=actor, args={"limit": 2, "owner_user_id": str(uuid4())})))

    assert records_service.owner_user_id == actor.user_id
    assert result["records"]["feedings"][0]["feed_time"] == "2026-07-02T08:00:00+00:00"
    assert result["records"]["pumpings"][0]["milk_volume_ml"] == 80
    assert result["records"]["growth"][0]["weight_kg"] == 6.2
    assert result["plans"]["plans"][0]["title"] == "Birth plan"
    assert result["plans"]["tasks"][0]["task_date"] == "2026-07-03"
    assert "diary" not in result
    assert result["devices"]["pumps"][0]["device_id"] == "pump-1"
    assert result["devices"]["telemetry"][0]["payload"] == {"mode": "stimulation"}


def test_milk_summary_read_tool_handler_returns_bounded_owner_scoped_summary() -> None:
    actor = _user()
    records_service = FakeRecordsService(owner_user_id=actor.user_id)
    infant = InfantProfile(
        id=uuid4(),
        owner_user_id=actor.user_id,
        infant_name="Nori",
        sex="female",
        birth_date=date(2026, 1, 10),
        status="active",
    )
    profile_service = FakeProfileService(profile=None, infants=[infant])
    handler = MilkSummaryReadToolHandler(records_service=records_service, profile_service=profile_service)

    result = asyncio.run(handler(_context(actor=actor, args={"days": 3, "limit": 2, "owner_user_id": str(uuid4())})))

    assert records_service.owner_user_id == actor.user_id
    assert profile_service.infant_owner_user_id == actor.user_id
    assert result["window"] == {"days": 3, "include_today": True}
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


def test_milk_status_read_tool_handler_returns_deterministic_status_snapshot() -> None:
    actor = _user()
    records_service = FakeRecordsService(owner_user_id=actor.user_id)
    infant = InfantProfile(
        id=uuid4(),
        owner_user_id=actor.user_id,
        infant_name="Nori",
        sex="female",
        birth_date=date(2026, 1, 10),
        status="active",
    )
    profile_service = FakeProfileService(profile=None, infants=[infant])
    handler = MilkStatusReadToolHandler(records_service=records_service, profile_service=profile_service)

    result = asyncio.run(handler(_context(actor=actor, args={"days": 3, "limit": 2, "owner_user_id": str(uuid4())})))

    assert records_service.owner_user_id == actor.user_id
    assert profile_service.infant_owner_user_id == actor.user_id
    assert result == {
        "window": {"days": 3, "limit": 2, "include_today": True},
        "status": {
            "data_coverage": "ready",
            "pumping_trend": "stable",
            "measured_only": True,
        },
        "counts": {
            "infants": 1,
            "recent_feedings": 1,
            "recent_pumpings": 1,
            "trend_days": 2,
            "days_with_pumping": 2,
            "trend_pumping_count": 3,
        },
        "volumes": {
            "recent_feeding_volume_ml": 60.0,
            "recent_pumped_volume_ml": 80.0,
            "trend_pumped_volume_ml": 170.0,
            "average_daily_pumped_volume_ml": 56.67,
        },
        "latest": {
            "feeding_at": "2026-07-02T08:00:00+00:00",
            "pumping_at": "2026-07-02T08:00:00+00:00",
        },
        "observation_flags": [],
    }


def test_milk_analysis_read_tool_handler_returns_growth_and_next_step_snapshot() -> None:
    actor = _user()
    records_service = FakeRecordsService(owner_user_id=actor.user_id)
    profile_service = FakeProfileService(profile=None, infants=[])
    handler = MilkAnalysisReadToolHandler(records_service=records_service, profile_service=profile_service)

    result = asyncio.run(handler(_context(actor=actor, args={"days": 3, "limit": 2})))

    assert records_service.owner_user_id == actor.user_id
    assert result["status"]["data_coverage"] == "ready"
    assert result["counts"]["recent_growth"] == 1
    assert result["recent_growth"][0]["weight_kg"] == 6.2
    assert result["analysis"]["pathway"] == "补充宝宝资料后再判断供需"
    assert result["analysis"]["recommended_next_step"] == "先确认宝宝资料或体重/尿布等摄入信号。"


def test_growth_records_read_tool_handler_returns_bounded_owner_scoped_records() -> None:
    actor = _user()
    records_service = FakeRecordsService(owner_user_id=actor.user_id)
    infant_id = uuid4()
    handler = GrowthRecordsReadToolHandler(records_service=records_service)

    result = asyncio.run(handler(_context(actor=actor, args={"infant_id": str(infant_id), "limit": 2})))

    assert records_service.owner_user_id == actor.user_id
    assert records_service.growth_infant_id == infant_id
    assert result["count"] == 1
    assert result["infant_id"] == str(infant_id)
    assert result["growth"][0]["height_cm"] == 62


def test_plans_current_read_tool_handler_returns_bounded_owner_scoped_summary() -> None:
    actor = _user()
    plans_service = FakePlansService(owner_user_id=actor.user_id)
    handler = PlansCurrentReadToolHandler(plans_service=plans_service)

    result = asyncio.run(handler(_context(actor=actor, args={"limit": 2, "owner_user_id": str(uuid4())})))

    assert plans_service.owner_user_id == actor.user_id
    assert plans_service.plan_status == "active"
    assert plans_service.limit == 2
    assert result["plans"][0]["title"] == "Birth plan"
    assert result["tasks"][0]["task_date"] == "2026-07-03"
    assert result["counts"] == {"plans": 1, "tasks": 1}


def test_plans_calendar_read_tool_handler_filters_by_date_and_status() -> None:
    actor = _user()
    plans_service = FakePlansService(owner_user_id=actor.user_id)
    handler = PlansCalendarReadToolHandler(plans_service=plans_service)

    result = asyncio.run(handler(_context(actor=actor, args={"task_date": "2026-07-03", "status": "pending", "limit": 2})))

    assert plans_service.owner_user_id == actor.user_id
    assert plans_service.task_date == date(2026, 7, 3)
    assert plans_service.task_status == "pending"
    assert result["filters"] == {"task_date": "2026-07-03", "status": "pending", "limit": 2}
    assert result["tasks"][0]["title"] == "Call clinic"


def test_pregnancy_diary_read_tool_handler_returns_owner_scoped_entry() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)
    handler = PregnancyDiaryEntriesReadToolHandler(diary_service=diary_service)

    result = asyncio.run(handler(_context(actor=actor, args={"entry_date": "2026-07-02"})))

    assert diary_service.owner_user_id == actor.user_id
    assert result["status"] == "entry_read"
    assert result["entry"]["entry_date"] == "2026-07-02"
    assert result["entry"]["content"] == "x" * 600


def test_pregnancy_plan_context_read_tool_handler_returns_bounded_owner_scoped_summary() -> None:
    actor = _user()
    profile = UserProfile(
        user_id=actor.user_id,
        display_name="Mai",
        age=31,
        delivery_date=date(2026, 9, 20),
    )
    profile_service = FakeProfileService(profile=profile, infants=[])
    plans_service = FakePlansService(owner_user_id=actor.user_id)
    handler = PregnancyPlanContextReadToolHandler(
        profile_service=profile_service,
        plans_service=plans_service,
    )

    result = asyncio.run(handler(_context(actor=actor, args={"limit": 2, "owner_user_id": str(uuid4())})))

    assert profile_service.profile_user_id == actor.user_id
    assert plans_service.owner_user_id == actor.user_id
    assert plans_service.plan_status == "active"
    assert plans_service.plan_type == "pregnancy"
    assert result["profile"]["delivery_date"] == "2026-09-20"
    assert result["plans"][0]["title"] == "Birth plan"
    assert result["tasks"][0]["title"] == "Call clinic"
    assert "recent_diary_entries" not in result
    assert result["counts"] == {"plans": 1, "tasks": 1}


def test_devices_pump_status_read_tool_handler_returns_bounded_owner_scoped_summary() -> None:
    actor = _user()
    devices_service = FakeDevicesService(owner_user_id=actor.user_id)
    handler = DevicesPumpStatusReadToolHandler(devices_service=devices_service)

    result = asyncio.run(handler(_context(actor=actor, args={"limit": 2, "owner_user_id": str(uuid4())})))

    assert devices_service.owner_user_id == actor.user_id
    assert devices_service.limit == 2
    assert result["pumps"][0]["device_id"] == "pump-1"
    assert result["telemetry"][0]["payload"] == {"mode": "stimulation"}
    assert result["counts"] == {"pumps": 1, "telemetry": 1}


def test_device_guidance_assets_read_tool_handler_returns_bounded_metadata() -> None:
    handler = DeviceGuidanceAssetsReadToolHandler(asset_service=FakeAssetService())

    result = asyncio.run(handler(_context(args={"limit": 1, "content_type": "application/pdf", "model": "Air1", "topic": "setup"})))

    assert result == {
        "assets": [
            {
                "id": "asset-guide",
                "label": "Air1 unboxing pump guide",
                "domain": "device_guidance",
                "content_type": "application/pdf",
                "size_bytes": 1200,
                "kind": "pdf",
                "url": "/v1/assets/asset-guide?kind=pdf",
                "markdown_link": "[Air1 unboxing pump guide](/v1/assets/asset-guide?kind=pdf)",
            }
        ],
        "count": 1,
        "available_count": 1,
        "query_context": {"model": "Air1", "topic": "setup", "query": "", "measured_nipple_mm": None},
    }


def test_device_guidance_assets_read_tool_handler_returns_copyable_image_markdown() -> None:
    handler = DeviceGuidanceAssetsReadToolHandler(asset_service=FakeAssetService())

    result = asyncio.run(handler(_context(args={"content_type": "image/png"})))

    assert result["assets"] == [
        {
            "id": "asset-image",
            "label": "Air1 components overview",
            "domain": "device_guidance",
            "content_type": "image/png",
            "size_bytes": 800,
            "kind": "image",
            "url": "/v1/assets/asset-image?kind=image",
            "markdown_image": "![Air1 components overview](/v1/assets/asset-image?kind=image)",
        }
    ]
    assert result["media_voice"] == [
        {
            "media_id": "/v1/assets/asset-image?kind=image",
            "kind": "image",
            "visual_label": "Air1 components overview",
            "voice_policy": "announce",
            "priority": "instructional",
            "spoken_label": "我放了一张当前步骤的对照图，你可以边看图边完成这一步。",
        }
    ]


def test_image_inspect_tool_handler_loads_visible_packaged_image_as_transient_model_context() -> None:
    storage = FakeImageObjectStorage(body=b"image")
    handler = ImageInspectToolHandler(asset_service=FakeImageAssetService(), object_storage=storage)
    image_url = "/skill-assets/device-guidance/air1/images/guide.png"

    result = asyncio.run(
        handler(
            _context(
                args={
                    "image_url": image_url,
                    "detail": "high",
                    "visible_image_urls": [image_url],
                }
            )
        )
    )

    assert isinstance(result, ToolHandlerResult)
    assert result.output["status"] == "image_context_ready"
    assert result.output["image_url"] == image_url
    assert result.output["asset_id"] == "asset-image"
    assert result.output["detail"] == "high"
    assert storage.keys == ["product-assets/device-guidance/assets/air1/images/guide.png"]
    assert result.model_context == (
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "这是你选择查看的历史图片。请结合当前用户问题，只依据图片中可见内容回答。",
                },
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,aW1hZ2U=",
                    "detail": "high",
                },
            ],
        },
    )


def test_image_inspect_tool_handler_rejects_url_not_visible_to_model() -> None:
    handler = ImageInspectToolHandler(
        asset_service=FakeImageAssetService(),
        object_storage=FakeImageObjectStorage(body=b"image"),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler(
                _context(
                    args={
                        "image_url": "/skill-assets/device-guidance/air1/images/guide.png",
                        "visible_image_urls": [],
                    }
                )
            )
        )

    assert exc_info.value.code == "image_reference_not_visible"


def test_feeding_record_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        actor=actor,
        args={
            "infant_id": str(uuid4()),
            "feed_time": "2026-07-02T09:15:00+00:00",
            "feed_type": "bottle",
            "feed_action": "fed",
            "volume_ml": 75,
            "title": "Morning bottle",
            "locale": "en-US",
        },
    )

    result = asyncio.run(FeedingRecordProposeToolHandler(runtime_service=runtime_service)(context))

    assert result["action_id"] == str(runtime_service.action.id)
    assert result["action_type"] == FEEDING_RECORD_CREATE_ACTION
    assert result["action_status"] == "confirmation_required"
    assert result["preview_payload"]["volume_ml"] == 75.0
    assert result["preview_payload"]["has_infant_id"] is True
    assert "infant_id" not in result["preview_payload"]
    assert runtime_service.calls[0]["owner_user_id"] == actor.user_id
    assert runtime_service.calls[0]["target_type"] == "feeding_record"
    assert runtime_service.calls[0]["side_effect_level"] == "low"
    assert runtime_service.calls[0]["apply_payload"]["infant_id"] == context.args["infant_id"]
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"locale": "en-US"}


def test_pumping_record_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        actor=actor,
        args={
            "pump_start_time": "2026-07-02T09:00:00+00:00",
            "pump_end_time": "2026-07-02T09:18:00+00:00",
            "milk_volume_ml": 90,
            "duration_seconds": 1080,
            "pump_type": "electric",
            "timezone": "Asia/Shanghai",
        },
    )

    result = asyncio.run(PumpingRecordProposeToolHandler(runtime_service=runtime_service)(context))

    assert result["action_type"] == PUMPING_RECORD_CREATE_ACTION
    assert result["action_status"] == "confirmation_required"
    assert result["preview_payload"]["milk_volume_ml"] == 90.0
    assert result["preview_payload"]["duration_seconds"] == 1080
    assert runtime_service.calls[0]["target_type"] == "pumping_record"
    assert runtime_service.calls[0]["apply_payload"]["source"] == "agent"
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"timezone": "Asia/Shanghai"}


def test_record_delete_and_growth_propose_tool_handlers_create_actions() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    record_id = uuid4()
    growth_id = uuid4()

    feeding_delete = asyncio.run(
        FeedingRecordDeleteProposeToolHandler(runtime_service=runtime_service)(
            _context(actor=actor, args={"record_id": str(record_id), "reason": "duplicate"})
        )
    )
    growth_create = asyncio.run(
        GrowthRecordProposeToolHandler(runtime_service=runtime_service)(
            _context(
                actor=actor,
                args={
                    "measured_at": "2026-07-04T10:00:00+00:00",
                    "weight_kg": 6.4,
                    "timezone": "Asia/Shanghai",
                },
            )
        )
    )
    growth_update = asyncio.run(
        GrowthRecordUpdateProposeToolHandler(runtime_service=runtime_service)(
            _context(actor=actor, args={"record_id": str(growth_id), "height_cm": 63})
        )
    )
    growth_delete = asyncio.run(
        GrowthRecordDeleteProposeToolHandler(runtime_service=runtime_service)(_context(actor=actor, args={"record_id": str(growth_id)}))
    )

    assert feeding_delete["action_type"] == FEEDING_RECORD_DELETE_ACTION
    assert feeding_delete["preview_payload"] == {
        "record_type": "feeding_record",
        "record_id": str(record_id),
        "reason": "duplicate",
    }
    assert growth_create["action_type"] == GROWTH_RECORD_CREATE_ACTION
    assert growth_create["action_status"] == "confirmation_required"
    assert growth_create["preview_payload"]["weight_kg"] == 6.4
    assert runtime_service.calls[-3]["apply_payload"]["metadata"] == {"timezone": "Asia/Shanghai"}
    assert growth_update["action_type"] == GROWTH_RECORD_UPDATE_ACTION
    assert growth_update["preview_payload"]["fields"] == ["height_cm"]
    assert growth_delete["action_type"] == GROWTH_RECORD_DELETE_ACTION
    assert growth_delete["preview_payload"]["record_type"] == "growth_record"


def test_milk_plan_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        actor=actor,
        args={
            "title": "Increase pumping consistency",
            "summary": "Pump after morning and evening feeds for the next week.",
            "direction": "maintain",
            "days": 7,
            "tasks": [{"title": "Pump at 20:00"}],
            "reminders": [{"title": "Drink water"}],
        },
    )

    result = asyncio.run(MilkPlanProposeToolHandler(runtime_service=runtime_service)(context))

    assert result["action_type"] == MILK_PLAN_CREATE_ACTION
    assert result["action_status"] == "confirmation_required"
    assert result["preview_payload"] == {
        "plan_type": "milk_management",
        "title": "Increase pumping consistency",
        "summary": "Pump after morning and evening feeds for the next week.",
        "has_payload": True,
    }
    assert runtime_service.calls[0]["target_type"] == "plan"
    assert runtime_service.calls[0]["side_effect_level"] == "medium"
    assert runtime_service.calls[0]["apply_payload"]["payload"] == {
        "direction": "maintain",
        "days": 7,
        "tasks": [{"title": "Pump at 20:00"}],
        "reminders": [{"title": "Drink water"}],
    }
    assert result["artifact_type"] == "milk_plan_preview"
    assert result["task_count"] == 1
    assert runtime_service.artifact.payload["action_id"] == result["action_id"]
    assert result["_deferred_agent_events"][0]["event_type"] == "artifact.created"


def test_ibclc_card_handler_creates_artifact() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()

    ibclc = asyncio.run(
        IbclcConsultCardCreateToolHandler(runtime_service=runtime_service)(
            _context(
                actor=actor,
                args={
                    "reason": "Latch pain",
                    "feeding_context": "Pain on left side after feeding.",
                    "urgency": "soon",
                },
            )
        )
    )

    assert ibclc["artifact_type"] == "ibclc_consult_card"
    assert ibclc["reason"] == "Latch pain"
    assert runtime_service.artifacts[-1].payload["feeding_context"] == "Pain on left side after feeding."


def test_pregnancy_plan_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        actor=actor,
        args={
            "summary": "Prepare appointments and bag tasks.",
            "due_date_or_week": "32周",
            "birth_path": "顺产",
            "runtime_plan_context": {"has_active_plan": False, "delivery_date": "2026-09-18"},
        },
    )

    result = asyncio.run(PregnancyPlanProposeToolHandler(runtime_service=runtime_service)(context))

    assert result["action_type"] == PREGNANCY_PLAN_CREATE_ACTION
    assert result["action_status"] == "confirmation_required"
    assert result["preview_payload"] == {
        "plan_type": "pregnancy",
        "title": "孕期计划",
        "summary": "Prepare appointments and bag tasks.",
        "has_payload": True,
    }
    assert runtime_service.calls[0]["target_type"] == "plan"
    assert runtime_service.calls[0]["side_effect_level"] == "medium"
    assert runtime_service.calls[0]["apply_payload"]["payload"]["plan_context"]["due_date_or_week"] == "32周"
    assert runtime_service.calls[0]["apply_payload"]["payload"]["plan_context"]["delivery_date"] == "2026-09-18"
    assert runtime_service.calls[0]["apply_payload"]["payload"]["card"]["card_type"] == "birth_journey_plan_card"
    assert result["artifact_type"] == "birth_journey_plan_card"
    assert runtime_service.artifact.payload["action_id"] == result["action_id"]


def test_pregnancy_plan_propose_tool_handler_reuses_existing_active_plan() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        PregnancyPlanProposeToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "runtime_plan_context": {
                        "has_active_plan": True,
                        "active_plan_id": "plan-1",
                        "active_plan_title": "我的孕期计划",
                    }
                }
            )
        )
    )

    assert result == {"status": "existing_plan_found", "plan_id": "plan-1", "title": "我的孕期计划"}
    assert runtime_service.calls == []


def test_plan_task_create_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    plan_id = uuid4()
    context = _context(
        actor=actor,
        args={
            "plan_id": str(plan_id),
            "task_date": "2026-07-04",
            "task_time": "09:00",
            "title": "Book prenatal appointment",
            "description": "Ask about birth plan questions.",
            "payload": {"category": "appointments"},
            "locale": "en-US",
        },
    )

    result = asyncio.run(PlanTaskCreateProposeToolHandler(runtime_service=runtime_service)(context))

    assert result["action_type"] == PLAN_TASK_CREATE_ACTION
    assert result["action_status"] == "confirmation_required"
    assert result["preview_payload"] == {
        "task_date": "2026-07-04",
        "task_time": "09:00",
        "title": "Book prenatal appointment",
        "description": "Ask about birth plan questions.",
        "has_plan_id": True,
        "has_payload": True,
    }
    assert runtime_service.calls[0]["target_type"] == "plan_task"
    assert runtime_service.calls[0]["side_effect_level"] == "medium"
    assert runtime_service.calls[0]["apply_payload"]["plan_id"] == str(plan_id)
    assert runtime_service.calls[0]["apply_payload"]["payload"] == {"category": "appointments"}
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"locale": "en-US"}


def test_plan_task_complete_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    task_id = uuid4()
    context = _context(
        actor=actor,
        args={
            "task_id": str(task_id),
            "completed": False,
            "timezone": "Asia/Shanghai",
        },
    )

    result = asyncio.run(PlanTaskCompleteProposeToolHandler(runtime_service=runtime_service)(context))

    assert result["action_type"] == PLAN_TASK_COMPLETE_ACTION
    assert result["action_status"] == "confirmation_required"
    assert result["preview_payload"] == {
        "task_id": str(task_id),
        "completed": False,
    }
    assert runtime_service.calls[0]["target_type"] == "plan_task"
    assert runtime_service.calls[0]["side_effect_level"] == "medium"
    assert runtime_service.calls[0]["apply_payload"]["task_id"] == str(task_id)
    assert runtime_service.calls[0]["apply_payload"]["completed"] is False
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"timezone": "Asia/Shanghai"}


def test_plan_task_update_delete_and_plan_delete_tool_handlers_create_confirmation_actions() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    task_id = uuid4()
    plan_id = uuid4()

    update = asyncio.run(
        PlanTaskUpdateProposeToolHandler(runtime_service=runtime_service)(
            _context(
                actor=actor,
                args={
                    "task_id": str(task_id),
                    "task_date": "2026-07-05",
                    "task_time": "10:30",
                    "title": "Move pumping session",
                },
            )
        )
    )
    task_delete = asyncio.run(
        PlanTaskDeleteProposeToolHandler(runtime_service=runtime_service)(
            _context(actor=actor, args={"task_id": str(task_id), "reason": "no longer needed"})
        )
    )
    plan_delete = asyncio.run(
        PlanDeleteProposeToolHandler(runtime_service=runtime_service)(_context(actor=actor, args={"plan_id": str(plan_id)}))
    )

    assert update["action_type"] == PLAN_TASK_UPDATE_ACTION
    assert update["preview_payload"]["fields"] == ["task_date", "task_time", "title"]
    assert runtime_service.calls[-3]["target_id"] == str(task_id)
    assert task_delete["action_type"] == PLAN_TASK_DELETE_ACTION
    assert task_delete["preview_payload"]["reason"] == "no longer needed"
    assert plan_delete["action_type"] == PLAN_DELETE_ACTION
    assert runtime_service.calls[-1]["target_type"] == "plan"


def test_milk_reminder_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        actor=actor,
        args={
            "title": "Time to pump",
            "body": "A short evening pumping session is due.",
            "remind_at": "2026-07-04T20:00:00+08:00",
            "payload": {"routine": "evening"},
            "timezone": "Asia/Shanghai",
        },
    )

    result = asyncio.run(MilkReminderProposeToolHandler(runtime_service=runtime_service)(context))

    assert result["action_type"] == MILK_REMINDER_CREATE_ACTION
    assert result["action_status"] == "confirmation_required"
    assert result["preview_payload"] == {
        "notification_type": "milk_reminder",
        "title": "Time to pump",
        "body": "A short evening pumping session is due.",
        "remind_at": "2026-07-04T20:00:00+08:00",
        "has_payload": True,
    }
    assert runtime_service.calls[0]["target_type"] == "notification"
    assert runtime_service.calls[0]["side_effect_level"] == "medium"
    assert runtime_service.calls[0]["apply_payload"]["payload"] == {"routine": "evening"}
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"timezone": "Asia/Shanghai"}


def test_pregnancy_diary_create_tool_writes_entry_synchronously() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)
    context = _context(
        actor=actor,
        args={
            "entry_date": "2026-07-04",
            "gestational_week": "32w",
            "mood": "calm",
            "energy_level": "medium",
            "symptom_tags": ["backache"],
            "content": "Today I felt steady.",
        },
    )

    result = asyncio.run(PregnancyDiaryEntryCreateToolHandler(diary_service=diary_service)(context))

    assert result["status"] == "entry_created"
    assert result["entry"]["entry_date"] == "2026-07-04"
    assert diary_service.create_kwargs["owner_user_id"] == actor.user_id
    assert diary_service.create_kwargs["values"]["content"] == "Today I felt steady."


def test_pregnancy_diary_create_tool_returns_existing_entry_on_date_conflict() -> None:
    actor = _user()
    diary_service = ExistingDiaryService(owner_user_id=actor.user_id)

    result = asyncio.run(
        PregnancyDiaryEntryCreateToolHandler(diary_service=diary_service)(
            _context(actor=actor, args={"entry_date": "2026-07-02", "content": "New content"})
        )
    )

    assert result["status"] == "entry_already_exists"
    assert result["entry"]["content"] == "x" * 600


@pytest.mark.parametrize(
    ("content_mode", "expected_content"),
    [
        ("append", f"{'x' * 600}\nOne more thing."),
        ("replace", "One more thing."),
    ],
)
def test_pregnancy_diary_update_tool_supports_explicit_content_mode(content_mode, expected_content) -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)

    result = asyncio.run(
        PregnancyDiaryEntryUpdateToolHandler(diary_service=diary_service)(
            _context(
                actor=actor,
                args={
                    "entry_date": "2026-07-02",
                    "content": "One more thing.",
                    "content_mode": content_mode,
                },
            )
        )
    )

    assert result["status"] == "entry_updated"
    assert diary_service.update_kwargs["owner_user_id"] == actor.user_id
    assert diary_service.update_kwargs["values"]["content"] == expected_content


def test_support_ticket_propose_tool_handler_requires_summary() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(SupportTicketProposeToolHandler(runtime_service=FakeAgentRuntimeService())(_context(args={})))

    assert exc_info.value.code == "validation_failed"


def test_hospital_bag_cart_update_propose_tool_handler_requires_cart_update() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(HospitalBagCartUpdateProposeToolHandler(runtime_service=FakeAgentRuntimeService())(_context(args={})))

    assert exc_info.value.code == "validation_failed"


def test_feeding_and_pumping_record_propose_tool_handlers_require_quantity() -> None:
    with pytest.raises(ApiError) as feeding_exc:
        asyncio.run(
            FeedingRecordProposeToolHandler(runtime_service=FakeAgentRuntimeService())(
                _context(args={"feed_time": "2026-07-02T09:15:00+00:00", "feed_type": "bottle"})
            )
        )
    with pytest.raises(ApiError) as pumping_exc:
        asyncio.run(
            PumpingRecordProposeToolHandler(runtime_service=FakeAgentRuntimeService())(
                _context(args={"pump_start_time": "2026-07-02T09:00:00+00:00"})
            )
        )

    assert feeding_exc.value.code == "validation_failed"
    assert pumping_exc.value.code == "validation_failed"


def test_milk_plan_propose_tool_handler_requires_title() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(MilkPlanProposeToolHandler(runtime_service=FakeAgentRuntimeService())(_context(args={})))

    assert exc_info.value.code == "validation_failed"


def test_plan_task_propose_tool_handlers_require_required_fields() -> None:
    with pytest.raises(ApiError) as task_create_exc:
        asyncio.run(PlanTaskCreateProposeToolHandler(runtime_service=FakeAgentRuntimeService())(_context(args={})))
    with pytest.raises(ApiError) as task_complete_exc:
        asyncio.run(PlanTaskCompleteProposeToolHandler(runtime_service=FakeAgentRuntimeService())(_context(args={})))

    assert task_create_exc.value.code == "validation_failed"
    assert task_complete_exc.value.code == "validation_failed"


def test_milk_reminder_propose_tool_handler_requires_title() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(MilkReminderProposeToolHandler(runtime_service=FakeAgentRuntimeService())(_context(args={})))

    assert exc_info.value.code == "validation_failed"


def test_pregnancy_diary_create_tool_requires_values() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            PregnancyDiaryEntryCreateToolHandler(diary_service=FakeDiaryService(owner_user_id=_user().user_id))(
                _context(args={"entry_date": "2026-07-04"})
            )
        )

    assert exc_info.value.code == "validation_failed"


def test_pregnancy_diary_delete_tool_creates_confirmation_action() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        PregnancyDiaryEntryDeleteProposeToolHandler(runtime_service=runtime_service)(_context(args={"entry_date": "2026-07-04"}))
    )

    assert result["action_type"] == PREGNANCY_DIARY_ENTRY_DELETE_ACTION
    assert result["action_status"] == "confirmation_required"
    assert runtime_service.calls[0]["side_effect_level"] == "medium"


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
        asset_service=FakeAssetService(),
        agent_runtime_service=FakeAgentRuntimeService(),
    )

    assert set(handlers) == {
        "birth_plan_form_create",
        "labor_communication_card_create",
        "hospital_bag_form_create",
        "hospital_bag_card_create",
        "hospital_bag_cart_update",
        "hospital_bag_pump_recommend",
        "profile.read",
        "profile_update",
        "business.context.read",
        "ibclc_consult_card_create",
        "records.growth.read",
        "records.growth_record.propose",
        "records.growth_record_delete.propose",
        "records.growth_record_update.propose",
        "records.feeding_record_delete.propose",
        "records.milk_summary.read",
        "records.milk_status.read",
        "records.milk_analysis.read",
        "records.pumping_record_delete.propose",
        "plans.calendar.read",
        "plans.current.read",
        "pregnancy_diary.entries.read",
        "pregnancy_diary.entry.create",
        "pregnancy_diary.entry.update",
        "pregnancy_diary.entry.delete.propose",
        "devices.guidance_assets.read",
        "devices.pump_status.read",
        "images.inspect",
        "notifications.milk_reminder.propose",
        "plans.milk_plan.propose",
        "plans.task_complete.propose",
        "plans.task_create.propose",
        "plans.task_delete.propose",
        "plans.task_update.propose",
        "plans.plan_delete.propose",
        "pregnancy.plan_context.read",
        "pregnancy.plan.propose",
        "records.feeding_record.propose",
        "records.pumping_record.propose",
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
                "profile:write:self",
                "business_context:read:self",
                "files:read:self",
                "diary:write:self",
                "memory:write:self",
                "plans:write:self",
                "notifications:create:self",
                "agent_artifact:create:self",
                "hospital_bag_cart:update:self",
                "support_ticket:create:self",
            }
        ),
    )


class FakeProfileService:
    def __init__(self, *, profile: UserProfile | None, infants: list[InfantProfile]) -> None:
        self.profile = profile
        self.infants = infants
        self.profile_user_id = None
        self.infant_owner_user_id = None
        self.update_profile_kwargs = {}

    async def get_user_profile(self, *, user_id):
        self.profile_user_id = user_id
        return self.profile

    async def list_infants(self, *, owner_user_id):
        self.infant_owner_user_id = owner_user_id
        return self.infants

    async def update_user_profile(self, **kwargs):
        self.update_profile_kwargs = kwargs
        values = kwargs["values"]
        self.profile = UserProfile(
            user_id=kwargs["user_id"],
            display_name=values.get("display_name", ""),
            age=values.get("age"),
            delivery_date=values.get("delivery_date"),
            lactation_advice=values.get("lactation_advice", ""),
            feeding_advice=values.get("feeding_advice", ""),
            profile_onboarding_skipped_at=values.get("profile_onboarding_skipped_at"),
            profile_onboarding_completed_at=values.get("profile_onboarding_completed_at"),
        )
        return self.profile


class FakeRecordsService:
    def __init__(self, *, owner_user_id) -> None:
        self.owner_user_id = None
        self._owner_user_id = owner_user_id
        self.growth_infant_id = None

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

    async def list_growth(self, *, owner_user_id, infant_id=None, limit):
        self.owner_user_id = owner_user_id
        self.growth_infant_id = infant_id
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
        self.owner_user_id = None
        self.plan_status = None
        self.plan_type = None
        self.task_date = None
        self.task_status = None
        self.limit = None

    async def list_plans(self, *, owner_user_id, limit, status="active", plan_type=""):
        self.owner_user_id = owner_user_id
        self.plan_status = status
        self.plan_type = plan_type
        self.limit = limit
        return [
            Plan(
                id=uuid4(),
                owner_user_id=self._owner_user_id,
                plan_type=plan_type or "pregnancy",
                title="Birth plan",
                summary="Pack hospital bag",
                status="active",
                source="manual",
                updated_at=_now(),
            )
        ]

    async def list_tasks(self, *, owner_user_id, task_date=None, status=None, limit):
        self.owner_user_id = owner_user_id
        self.task_date = task_date
        self.task_status = status
        self.limit = limit
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
        self.owner_user_id = None
        self.limit = None
        self.create_kwargs = {}
        self.update_kwargs = {}

    async def get_entry(self, *, owner_user_id, entry_date):
        self.owner_user_id = owner_user_id
        return self._entry(entry_date=entry_date)

    async def list_entries(self, *, owner_user_id, start_date=None, end_date=None, limit):
        self.owner_user_id = owner_user_id
        self.limit = limit
        return [self._entry(entry_date=date(2026, 7, 2))]

    async def create_entry(self, **kwargs):
        self.create_kwargs = kwargs
        entry = self._entry(entry_date=kwargs["entry_date"])
        for key, value in kwargs["values"].items():
            setattr(entry, key, value)
        return entry

    async def update_entry(self, **kwargs):
        self.update_kwargs = kwargs
        entry = self._entry(entry_date=kwargs["entry_date"])
        for key, value in kwargs["values"].items():
            setattr(entry, key, value)
        return entry

    def _entry(self, *, entry_date):
        return PregnancyDiaryEntry(
            id=uuid4(),
            owner_user_id=self._owner_user_id,
            entry_date=entry_date,
            gestational_week="32w",
            mood="calm",
            energy_level="medium",
            symptom_tags=["backache"],
            attachments=[],
            content="x" * 600,
        )


class ExistingDiaryService(FakeDiaryService):
    async def create_entry(self, **kwargs):
        raise ApiError(code="conflict", message="Diary entry already exists for this date.", status=409)


class FakeDevicesService:
    def __init__(self, *, owner_user_id) -> None:
        self._owner_user_id = owner_user_id
        self.owner_user_id = None
        self.limit = None

    async def list_devices(self, *, owner_user_id):
        self.owner_user_id = owner_user_id
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
        self.owner_user_id = owner_user_id
        self.limit = limit
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


class FakeAssetService:
    def list_assets(self, *, limit):
        return [
            ProductAsset(
                id="asset-guide",
                label="Air1 unboxing pump guide",
                domain="device_guidance",
                content_type="application/pdf",
                size_bytes=1200,
                path=None,
            ),
            ProductAsset(
                id="asset-video",
                label="Pump setup video",
                domain="device_guidance",
                content_type="video/mp4",
                size_bytes=2400,
                path=None,
            ),
            ProductAsset(
                id="asset-image",
                label="Air1 components overview",
                domain="device_guidance",
                content_type="image/png",
                size_bytes=800,
                path=None,
            ),
        ][:limit]


class FakeImageAssetService:
    def list_assets(self, *, limit):
        return [
            ProductAsset(
                id="asset-image",
                label="Air1 guide",
                domain="device_guidance",
                content_type="image/png",
                size_bytes=5,
                object_key="product-assets/device-guidance/assets/air1/images/guide.png",
            )
        ][:limit]


class FakeImageObjectStorage:
    def __init__(self, *, body: bytes) -> None:
        self.body = body
        self.keys = []

    async def get_bytes(self, *, key):
        self.keys.append(key)
        return self.body


class FakeAgentRuntimeService:
    def __init__(self) -> None:
        self.calls = []
        self.actions = []
        self.artifacts = []
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
        self.artifact = AgentArtifact(
            id=uuid4(),
            run_id=uuid4(),
            owner_user_id=uuid4(),
            artifact_type="hospital_bag_card",
            schema_version="v1",
            status="created",
            payload={},
            raw_payload_ref="",
        )
        self.actions.append(self.action)
        self.artifacts.append(self.artifact)

    async def propose_action(self, **kwargs):
        self.calls.append(kwargs)
        self.action = AgentAction(
            id=uuid4(),
            run_id=kwargs["run_id"],
            actor_user_id=kwargs["owner_user_id"],
            action_type=kwargs["action_type"],
            target_type=kwargs["target_type"],
            target_id=kwargs.get("target_id", ""),
            status=(
                "confirmed"
                if kwargs["action_type"]
                in {
                    "hospital_bag.cart.update",
                }
                else "confirmation_required"
            ),
            side_effect_level=kwargs["side_effect_level"],
            preview_payload=kwargs["preview_payload"],
            apply_payload=kwargs["apply_payload"],
            idempotency_key=kwargs["idempotency_key"],
            error_code="",
        )
        self.actions.append(self.action)
        return self.action

    async def create_artifact(self, **kwargs):
        self.calls.append(kwargs)
        self.artifact = AgentArtifact(
            id=uuid4(),
            run_id=kwargs["run_id"],
            owner_user_id=kwargs["owner_user_id"],
            artifact_type=kwargs["artifact_type"],
            schema_version=kwargs["schema_version"],
            status=kwargs["status"],
            payload=kwargs["payload"],
            raw_payload_ref="",
        )
        self.artifacts.append(self.artifact)
        return self.artifact


def _now() -> datetime:
    return datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc)
