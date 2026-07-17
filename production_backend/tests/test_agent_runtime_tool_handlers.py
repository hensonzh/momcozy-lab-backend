import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import AgentAction, AgentArtifact, AgentWorkflowState
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools import (
    BusinessContextReadToolHandler,
    ConversationHistoryImageLoadToolHandler,
    DeviceGuidanceReadToolHandler,
    DeviceUnboxingAdvanceToolHandler,
    DevicesPumpStatusReadToolHandler,
    FeedingRecordDeleteProposeToolHandler,
    FeedingRecordProposeToolHandler,
    GrowthRecordDeleteProposeToolHandler,
    GrowthRecordProposeToolHandler,
    GrowthRecordUpdateProposeToolHandler,
    GrowthRecordsReadToolHandler,
    HospitalBagCardCreateToolHandler,
    HospitalBagCartUpdateProposeToolHandler,
    HospitalBagFormCreateToolHandler,
    IbclcConsultCardCreateToolHandler,
    LegacyArtifactToolHandler,
    MilkAnalysisEvaluateToolHandler,
    MilkAnalysisIntakeToolHandler,
    MilkAnalysisReadToolHandler,
    MilkPlanProposeToolHandler,
    MilkScheduleRescheduleProposeToolHandler,
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
    PregnancyDiaryManageToolHandler,
    ProfileReadToolHandler,
    ProfileUpdateToolHandler,
    PumpingRecordProposeToolHandler,
    PregnancyPlanContextReadToolHandler,
    PregnancyPlanTodoUpdateProposeToolHandler,
    PregnancyPlanIntakeAdvanceToolHandler,
    PregnancyPlanIntakeAnalyzeToolHandler,
    PregnancyPlanIntakeStartToolHandler,
    PregnancyPlanProposeToolHandler,
    SupportTicketProposeToolHandler,
    ToolHandlerContext,
    ToolHandlerResult,
    build_default_tool_handlers,
)
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.assets.models import ProductAsset
from production_backend.app.modules.assets.service import ProductAssetService
from production_backend.app.modules.devices.models import PumpDevice, PumpTelemetryEvent
from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.modules.diary.repository import DiaryEntryMutation
from production_backend.app.modules.notifications.agent_actions import MILK_REMINDER_CREATE_ACTION
from production_backend.app.modules.plans.agent_actions import (
    MILK_PLAN_CREATE_ACTION,
    PLAN_TASK_COMPLETE_ACTION,
    PLAN_TASK_CREATE_ACTION,
    PLAN_TASK_DELETE_ACTION,
    PLAN_TASK_UPDATE_ACTION,
    PLAN_DELETE_ACTION,
    PREGNANCY_PLAN_CREATE_ACTION,
    PREGNANCY_PLAN_TODO_UPDATE_ACTION,
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
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools.milk_analysis_flow import (
    milk_analysis_context_fingerprint,
)


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


def test_support_ticket_propose_tool_handler_creates_editable_draft_artifact() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    handler = SupportTicketProposeToolHandler(runtime_service=runtime_service)
    context = _context(
        actor=actor,
        args={
            "issue_type": "malfunction",
            "issue_summary": "Pump does not start",
            "product_model": "M9",
            "user_contact": "mai@example.com",
            "urgency": "high",
            "user_confirmed": True,
            "trusted_current_user_text": "Yes, please create the support ticket now.",
            "locale": "en-US",
        },
    )

    result = asyncio.run(handler(context))

    assert result["status"] == "ticket_draft_created"
    assert result["artifact_id"] == str(runtime_service.artifact.id)
    assert result["artifact_type"] == "support_ticket_draft"
    assert result["submit_label"] == "确认并提交"
    assert result["ticket"]["issue_type"] == "malfunction"
    assert result["ticket"]["user_contact"] == "mai@example.com"
    assert result["_deferred_agent_events"][0]["event_type"] == "artifact.created"
    assert result["_deferred_agent_events"][0]["payload"]["artifact_type"] == "support_ticket_draft"
    assert runtime_service.calls[0]["owner_user_id"] == actor.user_id
    assert runtime_service.calls[0]["run_id"] == context.run_id
    assert runtime_service.calls[0]["artifact_type"] == "support_ticket_draft"
    assert runtime_service.calls[0]["payload"]["ticket"]["issue_summary"] == "Pump does not start"
    assert not any("action_type" in call for call in runtime_service.calls)


def test_support_ticket_propose_tool_handler_asks_in_chat_before_creating_draft() -> None:
    runtime_service = FakeAgentRuntimeService()
    result = asyncio.run(
        SupportTicketProposeToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "issue_type": "defect",
                    "issue_summary": "The new pump is cracked.",
                    "user_confirmed": False,
                    "user_emotion": "upset",
                    "trusted_current_user_text": "The new pump is cracked and I am very upset.",
                }
            )
        )
    )

    assert result["status"] == "needs_support_ticket_confirmation"
    assert result["requires_confirmation"] is True
    assert "需要我现在帮你创建吗" in result["confirmation_question"]
    assert runtime_service.calls == []


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
    assert result["action_status"] == "applied"
    assert result["requires_confirmation"] is False
    assert result["preview_payload"]["summary"] == "Mark nursing bra packed and add a phone charger"
    assert result["preview_payload"]["cart_update"]["set_checked"][0]["item_id"] == "nursing-bra"
    assert runtime_service.calls[0]["owner_user_id"] == actor.user_id
    assert runtime_service.calls[0]["run_id"] == context.run_id
    assert runtime_service.calls[0]["target_type"] == "hospital_bag_cart"
    assert runtime_service.calls[0]["side_effect_level"] == "low"
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"timezone": "Asia/Shanghai"}


def test_registered_hospital_bag_cart_handler_preserves_legacy_cart_result_through_idempotent_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    handlers = build_default_tool_handlers(
        profile_service=FakeProfileService(profile=None, infants=[]),
        records_service=FakeRecordsService(owner_user_id=actor.user_id),
        plans_service=FakePlansService(owner_user_id=actor.user_id),
        diary_service=FakeDiaryService(owner_user_id=actor.user_id),
        devices_service=FakeDevicesService(owner_user_id=actor.user_id),
        asset_service=FakeAssetService(),
        agent_runtime_service=runtime_service,
    )
    handler = handlers["hospital_bag_cart_update"]
    context = _context(actor=actor, args={"action": "reset_cart"})

    first = asyncio.run(handler(context))
    second = asyncio.run(handler(context))

    assert first["status"] == "cart_updated"
    assert first["action_type"] == "hospital_bag.cart.update"
    assert first["action_status"] == "applied"
    assert first["write_succeeded"] is True
    assert first["cart_update"]["groups"][0]["items"][0]["name"] == "产褥垫组合装"
    assert second["action_id"] == first["action_id"]
    cart_actions = [action for action in runtime_service.actions if action.action_type == "hospital_bag.cart.update"]
    assert len(cart_actions) == 1
    assert cart_actions[0].actor_user_id == actor.user_id
    assert cart_actions[0].apply_payload["cart_update"] == first["cart_update"]


def test_registered_hospital_bag_cart_handler_does_not_create_action_for_clarification() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    handler = HospitalBagCartUpdateProposeToolHandler(runtime_service=runtime_service)

    result = asyncio.run(handler(_context(actor=actor, args={"action": "clarify"})))

    assert result["status"] == "needs_clarification"
    assert "action_id" not in result
    assert not [action for action in runtime_service.actions if action.action_type == "hospital_bag.cart.update"]


def test_hospital_bag_form_and_card_use_one_durable_workflow_state() -> None:
    actor = _user()
    thread_id = uuid4()
    runtime_service = FakeAgentRuntimeService()
    form_context = _context(
        actor=actor,
        thread_id=thread_id,
        args={"default_values": {"due_date_or_week": "36 周"}, "runtime_workflow_context": {}},
    )

    form_result = asyncio.run(HospitalBagFormCreateToolHandler(runtime_service=runtime_service)(form_context))

    form_artifact = runtime_service.artifacts[-1]
    workflow_id = runtime_service.workflow_state.id
    assert form_result["status"] == "form_created"
    assert form_artifact.artifact_type == "form"
    assert runtime_service.workflow_state.workflow_type == "hospital_bag"
    assert runtime_service.workflow_state.status == "collecting"
    assert runtime_service.workflow_state.active_step == "collecting_intake"
    assert runtime_service.workflow_state.state == {
        "phase": "collecting_intake",
        "form_id": "hospital_bag_intake",
        "source_form_artifact_id": str(form_artifact.id),
    }

    card_result = asyncio.run(
        HospitalBagCardCreateToolHandler(runtime_service=runtime_service)(
            _context(
                actor=actor,
                thread_id=thread_id,
                args={
                    "form_artifact_id": str(form_artifact.id),
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
                    "runtime_workflow_context": dict(runtime_service.workflow_state.state),
                },
            )
        )
    )

    card_artifact = runtime_service.artifacts[-1]
    assert card_result["status"] == "card_created"
    assert card_artifact.artifact_type == "hospital_bag_card"
    assert runtime_service.workflow_state.id == workflow_id
    assert runtime_service.workflow_state.status == "completed"
    assert runtime_service.workflow_state.active_step == ""
    assert runtime_service.workflow_state.state == {
        "phase": "completed",
        "form_id": "hospital_bag_intake",
        "source_form_artifact_id": str(form_artifact.id),
        "source_form_submission_id": "submission-1",
        "result_artifact_id": str(card_artifact.id),
    }
    artifact_count = len(runtime_service.artifacts)
    duplicate_args = dict(runtime_service.workflow_state.state)
    duplicate = asyncio.run(
        HospitalBagCardCreateToolHandler(runtime_service=runtime_service)(
            _context(
                actor=actor,
                thread_id=thread_id,
                args={
                    "form_artifact_id": str(form_artifact.id),
                    "form_submission_id": "submission-1",
                    "runtime_workflow_context": duplicate_args,
                },
            )
        )
    )
    assert duplicate == {
        "status": "hospital_bag_card_already_created",
        "artifact_id": str(card_artifact.id),
        "artifact_type": "hospital_bag_card",
    }
    assert len(runtime_service.artifacts) == artifact_count


def test_hospital_bag_form_does_not_restart_active_intake() -> None:
    runtime_service = FakeAgentRuntimeService()
    artifact_count = len(runtime_service.artifacts)

    result = asyncio.run(
        HospitalBagFormCreateToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "runtime_workflow_context": {
                        "phase": "collecting_intake",
                        "source_form_artifact_id": "form-1",
                    }
                }
            )
        )
    )

    assert result == {"status": "hospital_bag_intake_already_started", "form_artifact_id": "form-1"}
    assert len(runtime_service.artifacts) == artifact_count


def test_hospital_bag_card_rejects_stale_form_submission() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            HospitalBagCardCreateToolHandler(runtime_service=FakeAgentRuntimeService())(
                _context(
                    args={
                        "form_artifact_id": "old-form",
                        "form_submission_id": "submission-1",
                        "confirmed_form_data": {"due_date_or_week": "36 周"},
                        "runtime_workflow_context": {
                            "phase": "collecting_intake",
                            "source_form_artifact_id": "current-form",
                        },
                    }
                )
            )
        )

    assert exc_info.value.code == "stale_hospital_bag_intake"


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
    assert isinstance(result, ToolHandlerResult)
    assert result.output == {
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
    assert result.retained_information[0].context_key == "milk:status"
    assert result.retained_information[0].information == result.output
    assert result.retained_information[0].ttl_turns == 3


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


def test_milk_analysis_reader_summarizes_rhythm_from_full_window_not_display_slice() -> None:
    actor = _user()

    class FullWindowRecordsService(FakeRecordsService):
        async def list_pumpings(self, *, owner_user_id, start_at=None, end_at=None, limit):
            self.pumping_query = {
                "owner_user_id": owner_user_id,
                "start_at": start_at,
                "end_at": end_at,
                "limit": limit,
            }
            times = [
                datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
                datetime(2026, 7, 2, 8, tzinfo=timezone.utc),
                datetime(2026, 7, 2, 4, tzinfo=timezone.utc),
                datetime(2026, 7, 1, 20, tzinfo=timezone.utc),
                datetime(2026, 7, 1, 16, tzinfo=timezone.utc),
                datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
                datetime(2026, 7, 1, 8, tzinfo=timezone.utc),
                datetime(2026, 7, 1, 4, tzinfo=timezone.utc),
                datetime(2026, 7, 1, 0, tzinfo=timezone.utc),
            ]
            return [
                PumpingRecord(
                    id=uuid4(),
                    owner_user_id=self._owner_user_id,
                    pump_start_time=value,
                    pump_end_time=None,
                    milk_volume_ml=80,
                    pump_type="electric",
                    duration_seconds=900,
                    source="device",
                    title="Pump session",
                )
                for value in times[:limit]
            ]

    handler = MilkAnalysisReadToolHandler(
        records_service=FullWindowRecordsService(owner_user_id=actor.user_id),
        profile_service=FakeProfileService(profile=None, infants=[]),
    )

    result = asyncio.run(
        handler(_context(actor=actor, args={"days": 7, "limit": 8, "runtime_timezone": "UTC"}))
    )

    assert len(result["recent_pumpings"]) == 8
    assert result["pumping_rhythm"] == {
        "timezone": "UTC",
        "representative_date": "2026-07-01",
        "representative_times": ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"],
    }


def test_milk_analysis_intake_is_durable_and_evaluation_emits_an_analysis_card() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    intake_handler = MilkAnalysisIntakeToolHandler(
        records_service=FakeRecordsService(owner_user_id=actor.user_id),
        profile_service=FakeProfileService(profile=None, infants=[]),
        runtime_service=runtime_service,
    )
    base_context = _context(actor=actor, args={"action": "start"})

    started = asyncio.run(intake_handler(base_context))
    assert started.output["progress"] == {"index": 2, "total": 6, "completed_count": 1, "remaining_count": 5}
    assert runtime_service.workflow_state.workflow_type == "milk_analysis"

    answers = [
        "24 小时有 7 片湿尿布",
        "精神不错，吃奶后能安稳",
        "最近体重增长正常",
        "没有发热、寒战、红肿、硬块或疼痛加重",
        "吸完后舒服，没有持续胀痛",
    ]
    for answer in answers:
        advanced = asyncio.run(
            intake_handler(
                _context(
                    actor=actor,
                    thread_id=base_context.thread_id,
                    args={"action": "answer", "trusted_current_user_text": answer},
                )
            )
        )

    assert advanced.output["can_evaluate"] is True
    evaluated = asyncio.run(
        MilkAnalysisEvaluateToolHandler(runtime_service=runtime_service)(
            _context(
                actor=actor,
                thread_id=base_context.thread_id,
                args={},
            )
        )
    )

    assert evaluated.output["artifact_type"] == "milk_analysis_card"
    assert "analysis_context_fingerprint" not in evaluated.output
    assert runtime_service.workflow_state.state["assessment"]["analysis_context_fingerprint"]
    valid_until = datetime.fromisoformat(runtime_service.workflow_state.state["assessment"]["valid_until"])
    assert valid_until > datetime.now(timezone.utc)
    assert runtime_service.artifact.payload["card_type"] == "milk_analysis_card"
    assert "analysis_context_fingerprint" not in runtime_service.artifact.payload
    assert evaluated.output["_deferred_agent_events"][0]["event_type"] == "artifact.created"
    artifact_count = len(runtime_service.artifacts)
    replayed = asyncio.run(
        MilkAnalysisEvaluateToolHandler(runtime_service=runtime_service)(_context(actor=actor, thread_id=base_context.thread_id, args={}))
    )
    assert replayed.output["replayed"] is True
    assert replayed.output["artifact_id"] == evaluated.output["artifact_id"]
    assert len(runtime_service.artifacts) == artifact_count
    restarted = asyncio.run(
        intake_handler(
            _context(
                actor=actor,
                thread_id=base_context.thread_id,
                args={"action": "start"},
            )
        )
    )
    assert restarted.output["progress"]["completed_count"] == 1
    assert runtime_service.workflow_state.state["phase"] == "collecting_intake"
    assert "evaluation_artifact_id" not in runtime_service.workflow_state.state


def test_milk_analysis_intake_absorbs_only_current_turn_grounded_answers() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    records_service = FakeRecordsService(owner_user_id=actor.user_id)
    handler = MilkAnalysisIntakeToolHandler(
        records_service=records_service,
        profile_service=FakeProfileService(profile=None, infants=[]),
        runtime_service=runtime_service,
    )
    base_context = _context(actor=actor, args={"action": "start"})

    asyncio.run(handler(base_context))
    current_text = "宝宝近 24 小时有 7 片湿尿布，精神很好，吃完能安稳，最近体重增长正常"
    advanced = asyncio.run(
        handler(
            _context(
                actor=actor,
                thread_id=base_context.thread_id,
                args={
                    "action": "answer",
                    "observed_answers": [
                        {"field": "infant_wet_diapers", "evidence": "近 24 小时有 7 片湿尿布"},
                        {"field": "infant_state_or_satisfaction", "evidence": "精神很好，吃完能安稳"},
                        {"field": "infant_growth_signal", "evidence": "最近体重增长正常"},
                    ],
                    "trusted_current_user_text": current_text,
                },
            )
        )
    )

    assert advanced.output["current_field"] == "maternal_red_flags"
    assert advanced.output["progress"] == {
        "index": 5,
        "total": 6,
        "completed_count": 4,
        "remaining_count": 2,
    }
    assert records_service.feeding_query["limit"] == 100
    assert records_service.pumping_query["limit"] == 100
    assert records_service.feeding_query["start_at"] is not None
    assert records_service.feeding_query["end_at"] is not None

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler(
                _context(
                    actor=actor,
                    thread_id=base_context.thread_id,
                    args={
                        "action": "answer",
                        "observed_answers": [
                            {"field": "maternal_red_flags", "evidence": "没有发热和红肿"},
                        ],
                        "trusted_current_user_text": "我还不确定",
                    },
                )
            )
        )
    assert exc_info.value.code == "milk_analysis_answer_not_grounded"


def test_milk_plan_proposal_rejects_missing_durable_analysis_even_with_valid_plan_args() -> None:
    actor = _user()
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            MilkPlanProposeToolHandler(
                runtime_service=FakeAgentRuntimeService(),
                plans_service=FakePlansService(owner_user_id=actor.user_id),
            )(
                _context(
                    actor=actor,
                    args={
                        "direction": "increase",
                        "days": 1,
                    }
                )
            )
        )

    assert exc_info.value.code == "milk_analysis_required_before_plan"


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


def test_pregnancy_plan_context_exposes_bounded_embedded_todos_for_agent_updates() -> None:
    actor = _user()
    plans_service = FakePlansService(owner_user_id=actor.user_id)
    plans_service.plan_payload = {
        "card": {
            "card_json": {
                "todo_plan": {
                    "periods": [
                        {
                            "status": "current",
                            "items": [
                                {
                                    "item_id": "prepare-hospital-bag",
                                    "title": "准备待产包",
                                    "completed": False,
                                }
                            ],
                        }
                    ]
                }
            }
        }
    }
    result = asyncio.run(
        PregnancyPlanContextReadToolHandler(
            profile_service=FakeProfileService(profile=None, infants=[]),
            plans_service=plans_service,
        )(_context(actor=actor, args={}))
    )

    assert result["plans"][0]["current_todos"] == [
        {
            "item_id": "prepare-hospital-bag",
            "number": 1,
            "title": "准备待产包",
            "completed": False,
        }
    ]


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
    handler = PregnancyDiaryManageToolHandler(diary_service=diary_service)

    result = asyncio.run(handler(_context(actor=actor, args={"action": "read", "entry_date": "2026-07-02"})))

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
    assert result["plans"][0]["version"] == 1
    assert result["tasks"][0]["title"] == "Call clinic"
    assert "recent_diary_entries" not in result
    assert result["counts"] == {"plans": 1, "tasks": 1}


def test_pregnancy_plan_context_exposes_only_active_plan_owner_defaults_for_birth_prep_prefill() -> None:
    actor = _user()
    profile_service = FakeProfileService(profile=None, infants=[])
    plans_service = FakePlansService(owner_user_id=actor.user_id)
    plans_service.plan_payload = {
        "card": {
            "owner": {
                "due_date_or_week": "31周",
                "birth_path": "剖宫产",
                "birth_setting": "市妇幼",
                "feeding_intention": "混合",
                "support_person": "伴侣",
            },
            "medical_notes": "must not be projected",
        }
    }
    handler = PregnancyPlanContextReadToolHandler(profile_service=profile_service, plans_service=plans_service)

    result = asyncio.run(handler(_context(actor=actor, args={})))

    assert result["plans"][0]["owner"] == {
        "due_date_or_week": "31周",
        "birth_path": "剖宫产",
        "birth_setting": "市妇幼",
        "feeding_intention": "混合",
        "support_person": "伴侣",
    }
    assert "payload" not in result["plans"][0]
    assert "medical_notes" not in str(result["plans"][0])


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
    assert result.retained_information[0].context_key == "devices:pump_status"


def test_device_guidance_read_tool_handler_returns_reference_and_bounded_metadata() -> None:
    handler = DeviceGuidanceReadToolHandler(asset_service=FakeAssetService())

    result = asyncio.run(handler(_context(args={"limit": 1, "content_type": "application/pdf", "model": "Air1", "topic": "setup"})))

    assert isinstance(result, ToolHandlerResult)
    assert result["device_model"] == "Air1"
    assert result["current_step"]["id"] == "guide.parts"
    assert result["assets"][0]["id"] == "asset-guide"
    assert result["count"] == 1
    assert result["query_context"] == {
        "model": "Air1",
        "topic": "setup",
        "step": "",
        "query": "",
        "measured_nipple_mm": None,
    }
    assert result.retained_information[0].ttl_turns is None


def test_device_guidance_read_tool_handler_returns_copyable_image_markdown() -> None:
    handler = DeviceGuidanceReadToolHandler(asset_service=FakeAssetService())

    result = asyncio.run(handler(_context(args={"model": "Air1", "content_type": "image/png", "step": "guide.parts"})))

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


@pytest.mark.parametrize(
    ("step", "expected_labels"),
    [
        ("guide.parts", ["air1 guide parts components"]),
        ("guide.controls", ["air1 guide controls button indicator"]),
        ("guide.charging", ["air1 guide charging methods"]),
        ("guide.disassembly", ["air1 guide disassembly steps"]),
        (
            "guide.cleaning",
            [
                "air1 guide cleaning washable parts",
                "air1 guide cleaning disinfection methods",
            ],
        ),
        (
            "guide.flange",
            ["air1 guide flange measurement", "air1 guide flange size card"],
        ),
        ("guide.assembly", ["air1 guide assembly steps"]),
        ("guide.wearing_start", ["air1 guide wearing start"]),
        ("guide.bluetooth", ["air1 guide bluetooth pairing"]),
        ("guide.finish_storage", ["air1 guide finish storage pouring"]),
    ],
)
def test_device_guidance_explicit_step_returns_only_manual_images(
    step: str,
    expected_labels: list[str],
) -> None:
    handler = DeviceGuidanceReadToolHandler(asset_service=ProductAssetService())

    result = asyncio.run(
        handler(
            _context(
                args={
                    "model": "Air1",
                    "content_type": "image/png",
                    "step": step,
                    "limit": 10,
                }
            )
        )
    )

    assert [asset["label"] for asset in result["assets"]] == expected_labels


def test_device_guidance_read_tool_handler_restores_unboxing_overview_resources() -> None:
    handler = DeviceGuidanceReadToolHandler(asset_service=FakeAssetService())

    result = asyncio.run(handler(_context(args={"model": "Air1", "topic": "unboxing", "limit": 10})))

    assert result["product_highlights"]
    assert any("无线可穿戴" in item for item in result["product_highlights"])
    assert [resource["kind"] for resource in result["quick_start_resources"]] == ["pdf", "video"]
    assert all(resource.get("markdown_link") for resource in result["quick_start_resources"])


def test_device_guidance_unboxing_query_does_not_erase_topic_resources() -> None:
    handler = DeviceGuidanceReadToolHandler(asset_service=FakeAssetService())

    result = asyncio.run(
        handler(
            _context(
                args={
                    "model": "Air1",
                    "topic": "unboxing",
                    "query": "我刚收到吸奶器，想开箱",
                    "limit": 10,
                }
            )
        )
    )

    assert [resource["kind"] for resource in result["quick_start_resources"]] == ["pdf", "video"]


def test_device_guidance_read_tool_handler_returns_real_manifest_quick_start_media() -> None:
    handler = DeviceGuidanceReadToolHandler(asset_service=ProductAssetService())

    result = asyncio.run(handler(_context(args={"model": "Air1", "topic": "unboxing", "limit": 10})))

    resources = result["quick_start_resources"]
    assert [resource["kind"] for resource in resources] == ["pdf", "video"]
    assert resources[0]["content_type"] == "application/pdf"
    assert resources[1]["content_type"] == "video/mp4"


def test_device_guidance_read_tool_handler_restores_flange_recommendation() -> None:
    handler = DeviceGuidanceReadToolHandler(asset_service=FakeAssetService())

    result = asyncio.run(
        handler(
            _context(
                args={
                    "model": "Air1",
                    "topic": "flange",
                    "query": "量到 14 毫米",
                    "measured_nipple_mm": 14,
                }
            )
        )
    )

    recommendation = result["flange_recommendation"]
    assert recommendation["status"] == "recommended"
    assert recommendation["matched_range"] == "13-15mm"
    assert recommendation["recommended_flange_mm"] == 17
    assert recommendation["recommended_insert_mm"] == 17
    assert recommendation["included_with_air1"] is True


def test_device_unboxing_advance_tool_starts_and_returns_first_step_reference() -> None:
    actor = _user()
    thread_id = uuid4()
    runtime_service = FakeAgentRuntimeService()
    handler = DeviceUnboxingAdvanceToolHandler(
        runtime_service=runtime_service,
        asset_service=FakeAssetService(),
    )

    result = asyncio.run(
        handler(
            _context(
                actor=actor,
                thread_id=thread_id,
                args={"model": "Air1", "action": "start"},
            )
        )
    )

    assert result["status"] == "unboxing_started"
    assert result["workflow"] == {
        "device_model": "Air1",
        "phase": "guiding",
        "current_step": "guide.parts",
        "completed_steps": [],
    }
    assert result["guidance"]["current_step"]["id"] == "guide.parts"
    assert runtime_service.workflow_state.thread_id == thread_id
    assert runtime_service.workflow_state.owner_user_id == actor.user_id
    assert runtime_service.workflow_state.active_step == "guide.parts"
    assert result.retained_information[0].ttl_turns is None
    assert result.retained_information[0].invalidate_prefixes == ("device_guidance:step:",)


def test_device_unboxing_advance_tool_completes_current_step_and_returns_next_step() -> None:
    actor = _user()
    thread_id = uuid4()
    runtime_service = FakeAgentRuntimeService()
    runtime_service.workflow_state = AgentWorkflowState(
        id=uuid4(),
        thread_id=thread_id,
        owner_user_id=actor.user_id,
        run_id=uuid4(),
        workflow_type="device_unboxing",
        status="waiting",
        schema_version="v1",
        state={"phase": "guiding", "device_model": "Air1", "completed_steps": []},
        active_step="guide.parts",
    )
    handler = DeviceUnboxingAdvanceToolHandler(
        runtime_service=runtime_service,
        asset_service=FakeAssetService(),
    )

    result = asyncio.run(
        handler(
            _context(
                actor=actor,
                thread_id=thread_id,
                args={"model": "Air1", "action": "complete_current"},
            )
        )
    )

    assert result["status"] == "unboxing_step_advanced"
    assert result["workflow"]["completed_steps"] == ["guide.parts"]
    assert result["workflow"]["current_step"] == "guide.controls"
    assert result["guidance"]["current_step"]["id"] == "guide.controls"
    assert runtime_service.workflow_state.active_step == "guide.controls"


def test_conversation_history_image_load_handler_adds_visible_image_to_model_context() -> None:
    storage = FakeImageObjectStorage(body=b"image")
    handler = ConversationHistoryImageLoadToolHandler(
        asset_service=FakeImageAssetService(),
        object_storage=storage,
    )
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
                    "text": "这是当前对话历史中由智能体此前展示的目标图片。请结合当前用户问题，只依据图片中可见内容回答。",
                },
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,aW1hZ2U=",
                    "detail": "high",
                },
            ],
        },
    )


def test_conversation_history_image_load_handler_rejects_url_not_visible_to_model() -> None:
    handler = ConversationHistoryImageLoadToolHandler(
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
    assert result["action_status"] == "applied"
    assert result["user_visible"] is False
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
    assert result["action_status"] == "applied"
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
    assert feeding_delete["action_status"] == "applied"
    assert feeding_delete["preview_payload"] == {
        "record_type": "feeding_record",
        "record_id": str(record_id),
        "reason": "duplicate",
    }
    assert growth_create["action_type"] == GROWTH_RECORD_CREATE_ACTION
    assert growth_create["action_status"] == "applied"
    assert growth_create["preview_payload"]["weight_kg"] == 6.4
    assert runtime_service.calls[-3]["apply_payload"]["metadata"] == {"timezone": "Asia/Shanghai"}
    assert growth_update["action_type"] == GROWTH_RECORD_UPDATE_ACTION
    assert growth_update["action_status"] == "applied"
    assert growth_update["preview_payload"]["fields"] == ["height_cm"]
    assert growth_delete["action_type"] == GROWTH_RECORD_DELETE_ACTION
    assert growth_delete["action_status"] == "applied"
    assert growth_delete["preview_payload"]["record_type"] == "growth_record"


def test_milk_plan_propose_tool_handler_creates_confirmation_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    plans_service = FakePlansService(owner_user_id=actor.user_id)
    context = _context(
        actor=actor,
        args={
            "direction": "maintain",
            "start_date": "2026-07-13",
            "days": 2,
            "preferred_pumping_times": ["08:00", "20:00"],
            "runtime_local_date": "2026-07-12",
            "runtime_timezone": "Asia/Shanghai",
        },
    )
    analysis_context = {
        "schema_version": "milk_analysis.v1",
        "records_snapshot": {"status": {"data_coverage": "ready", "pumping_trend": "stable"}},
        "answers": {"maternal_red_flags": "没有这些情况"},
    }
    analysis_fingerprint = milk_analysis_context_fingerprint(analysis_context)
    valid_until = datetime.now(timezone.utc) + timedelta(minutes=20)
    runtime_service.workflow_state = AgentWorkflowState(
        id=uuid4(),
        thread_id=context.thread_id,
        owner_user_id=actor.user_id,
        run_id=context.run_id,
        workflow_type="milk_analysis",
        status="ready",
        schema_version="milk_analysis.v1",
        state={
            "phase": "assessment_complete",
            "assessment": {
                "analysis_context": analysis_context,
                "analysis_context_fingerprint": analysis_fingerprint,
                "valid_until": valid_until.isoformat(),
                "plan_decision": {"can_start_plan": True, "recommended_direction": "maintain"},
            },
        },
        active_step="assessment_complete",
    )

    result = asyncio.run(
        MilkPlanProposeToolHandler(runtime_service=runtime_service, plans_service=plans_service)(context)
    )

    assert result["action_type"] == MILK_PLAN_CREATE_ACTION
    assert result["action_status"] == "confirmation_required"
    assert result["user_visible"] is True
    assert result["preview_payload"] == {
        "plan_type": "milk_management",
        "title": "2 天稳奶计划",
        "summary": "2 天稳奶安排：沿用近期可执行节奏，保持记录和复盘。近期实测奶量不足以给出可靠数字目标，本轮先按节奏和身体反应复盘。",
        "has_payload": True,
        "start_date": "2026-07-13",
        "days": 2,
        "scheduled_task_count": 4,
        "calendar_write_strategy": "append",
        "existing_future_task_count": 0,
    }
    assert runtime_service.calls[0]["target_type"] == "plan"
    assert runtime_service.calls[0]["side_effect_level"] == "medium"
    assert runtime_service.calls[0]["expires_at"] == valid_until
    apply_payload = runtime_service.calls[0]["apply_payload"]
    assert apply_payload["calendar_write_strategy"] == "append"
    assert apply_payload["expected_replaced_task_ids"] == []
    assert apply_payload["payload"]["direction"] == "maintain"
    assert apply_payload["payload"]["tasks"] == [
        {
            "title": "稳奶吸奶",
            "time": "08:00",
            "task_type": "pumping",
            "description": "保持近期可执行节奏，并记录实际完成和身体感受。",
        },
        {
            "title": "稳奶吸奶",
            "time": "20:00",
            "task_type": "pumping",
            "description": "保持近期可执行节奏，并记录实际完成和身体感受。",
        },
    ]
    assert apply_payload["payload"]["analysis_context_fingerprint"] == analysis_fingerprint
    assert apply_payload["payload"]["analysis_workflow_state_id"] == str(runtime_service.workflow_state.id)
    assert result["artifact_type"] == "milk_plan_preview"
    assert result["task_count"] == 4
    assert runtime_service.artifact.payload["scheduled_task_count"] == 4
    assert runtime_service.artifact.payload["goal"]["basis"] == "measured_pumping_average_7d"
    assert runtime_service.artifact.payload["safety_notes"]
    assert runtime_service.artifact.payload["action_id"] == result["action_id"]
    assert "analysis_context_fingerprint" not in result
    assert result["_deferred_agent_events"][0]["event_type"] == "artifact.created"


def test_milk_plan_proposal_requires_an_explicit_append_or_replace_choice_when_future_tasks_exist() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    plans_service = FakePlansService(owner_user_id=actor.user_id)
    plans_service.future_milk_tasks = [
        PlanTask(
            id=uuid4(),
            owner_user_id=actor.user_id,
            plan_id=uuid4(),
            task_date=date(2026, 7, 14),
            task_time="08:00",
            title="旧稳奶任务",
            status="pending",
            payload={},
        )
    ]
    context = _context(
        actor=actor,
        args={
            "direction": "maintain",
            "start_date": "2026-07-13",
            "days": 2,
            "runtime_local_date": "2026-07-12",
        },
    )
    analysis_context = {
        "schema_version": "milk_analysis.v1",
        "records_snapshot": {"status": {"data_coverage": "ready", "pumping_trend": "stable"}},
        "answers": {"maternal_red_flags": "没有这些情况"},
    }
    fingerprint = milk_analysis_context_fingerprint(analysis_context)
    runtime_service.workflow_state = AgentWorkflowState(
        id=uuid4(),
        thread_id=context.thread_id,
        owner_user_id=actor.user_id,
        run_id=context.run_id,
        workflow_type="milk_analysis",
        status="ready",
        schema_version="milk_analysis.v1",
        state={
            "phase": "assessment_complete",
            "assessment": {
                "analysis_context": analysis_context,
                "analysis_context_fingerprint": fingerprint,
                "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
                "plan_decision": {"can_start_plan": True, "recommended_direction": "maintain"},
            },
        },
        active_step="assessment_complete",
    )
    handler = MilkPlanProposeToolHandler(runtime_service=runtime_service, plans_service=plans_service)

    needs_choice = asyncio.run(handler(context))

    assert isinstance(needs_choice, ToolHandlerResult)
    assert needs_choice.output == {
        "status": "milk_plan_calendar_strategy_required",
        "existing_future_task_count": 1,
        "allowed_strategies": ["append", "replace_future_plan_tasks"],
        "question": "未来日程已有奶量计划任务。你希望把新计划追加进去，还是替换这些未来未完成任务？",
    }
    assert runtime_service.calls == []

    confirmed = asyncio.run(
        handler(
            _context(
                actor=actor,
                thread_id=context.thread_id,
                args={
                    "direction": "maintain",
                    "start_date": "2026-07-13",
                    "days": 2,
                    "calendar_write_strategy": "replace_future_plan_tasks",
                    "runtime_local_date": "2026-07-12",
                },
            )
        )
    )
    assert confirmed["action_status"] == "confirmation_required"
    assert runtime_service.action.apply_payload["expected_replaced_task_ids"] == [
        str(plans_service.future_milk_tasks[0].id)
    ]


def test_milk_plan_proposal_rejects_a_tampered_analysis_fingerprint() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        actor=actor,
        args={
            "direction": "maintain",
            "days": 1,
        },
    )
    runtime_service.workflow_state = AgentWorkflowState(
        id=uuid4(),
        thread_id=context.thread_id,
        owner_user_id=actor.user_id,
        run_id=context.run_id,
        workflow_type="milk_analysis",
        status="ready",
        schema_version="milk_analysis.v1",
        state={
            "phase": "assessment_complete",
            "assessment": {
                "analysis_context": {"records_snapshot": {"status": "normal"}, "answers": {}},
                "analysis_context_fingerprint": "tampered",
                "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
                "plan_decision": {"can_start_plan": True, "recommended_direction": "maintain"},
            },
        },
        active_step="assessment_complete",
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            MilkPlanProposeToolHandler(
                runtime_service=runtime_service,
                plans_service=FakePlansService(owner_user_id=actor.user_id),
            )(context)
        )

    assert exc_info.value.code == "milk_analysis_fingerprint_mismatch"


def test_milk_plan_proposal_rejects_an_expired_analysis_before_creating_an_action() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        actor=actor,
        args={
            "direction": "maintain",
            "days": 1,
        },
    )
    analysis_context = {"records_snapshot": {"status": "normal"}, "answers": {}}
    runtime_service.workflow_state = AgentWorkflowState(
        id=uuid4(),
        thread_id=context.thread_id,
        owner_user_id=actor.user_id,
        run_id=context.run_id,
        workflow_type="milk_analysis",
        status="ready",
        schema_version="milk_analysis.v1",
        state={
            "phase": "assessment_complete",
            "assessment": {
                "analysis_context": analysis_context,
                "analysis_context_fingerprint": milk_analysis_context_fingerprint(analysis_context),
                "valid_until": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                "plan_decision": {"can_start_plan": True, "recommended_direction": "maintain"},
            },
        },
        active_step="assessment_complete",
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            MilkPlanProposeToolHandler(
                runtime_service=runtime_service,
                plans_service=FakePlansService(owner_user_id=actor.user_id),
            )(context)
        )

    assert exc_info.value.code == "milk_analysis_expired_before_plan"
    assert runtime_service.calls == []


def test_milk_plan_proposal_requires_the_assessment_direction() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        actor=actor,
        args={
            "days": 1,
        },
    )
    analysis_context = {"records_snapshot": {"status": "normal"}, "answers": {}}
    runtime_service.workflow_state = AgentWorkflowState(
        id=uuid4(),
        thread_id=context.thread_id,
        owner_user_id=actor.user_id,
        run_id=context.run_id,
        workflow_type="milk_analysis",
        status="ready",
        schema_version="milk_analysis.v1",
        state={
            "phase": "assessment_complete",
            "assessment": {
                "analysis_context": analysis_context,
                "analysis_context_fingerprint": milk_analysis_context_fingerprint(analysis_context),
                "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
                "plan_decision": {"can_start_plan": True, "recommended_direction": "maintain"},
            },
        },
        active_step="assessment_complete",
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            MilkPlanProposeToolHandler(
                runtime_service=runtime_service,
                plans_service=FakePlansService(owner_user_id=actor.user_id),
            )(context)
        )

    assert exc_info.value.code == "validation_failed"
    assert runtime_service.calls == []


def test_milk_schedule_proposal_is_owner_scoped_and_contains_freshness_guards() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    plans_service = FakeMilkSchedulePlansService(owner_user_id=actor.user_id)

    result = asyncio.run(
        MilkScheduleRescheduleProposeToolHandler(
            runtime_service=runtime_service,
            plans_service=plans_service,
        )(
            _context(
                actor=actor,
                args={
                    "plan_id": str(plans_service.plan.id),
                    "calendar_events": [
                        {
                            "date": "2026-07-14",
                            "start_time": "10:30",
                            "end_time": "12:30",
                            "title": "会议",
                        }
                    ],
                },
            )
        )
    )

    assert result["action_type"] == "plans.milk_schedule.reschedule"
    assert result["action_status"] == "confirmation_required"
    assert result["conflict_count"] == 1
    update = runtime_service.calls[0]["apply_payload"]["updates"][0]
    assert update["task_id"] == str(plans_service.tasks[1].id)
    assert update["expected_task_time"] == "11:00"
    assert runtime_service.calls[0]["apply_payload"]["calendar_events"] == [
        {
            "date": "2026-07-14",
            "start_time": "10:30",
            "end_time": "12:30",
            "title": "会议",
            "duration_minutes": 120,
        }
    ]
    assert result["calendar_event_count"] == 1
    assert result["_deferred_agent_events"][0]["event_type"] == "artifact.created"


def test_milk_schedule_proposal_keeps_a_new_calendar_event_when_no_milk_task_moves() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    plans_service = FakeMilkSchedulePlansService(owner_user_id=actor.user_id)

    result = asyncio.run(
        MilkScheduleRescheduleProposeToolHandler(
            runtime_service=runtime_service,
            plans_service=plans_service,
        )(
            _context(
                actor=actor,
                args={
                    "plan_id": str(plans_service.plan.id),
                    "calendar_events": [
                        {
                            "date": "2026-07-14",
                            "start_time": "16:00",
                            "end_time": "17:00",
                            "title": "晚餐",
                        }
                    ],
                },
            )
        )
    )

    assert result["action_status"] == "confirmation_required"
    assert result["updated_count"] == 0
    assert result["calendar_event_count"] == 1
    assert result["affected_dates"] == ["2026-07-14"]
    assert runtime_service.action.apply_payload["updates"] == []


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
                    "trusted_current_user_text": "请帮我找一位 IBCLC 顾问",
                },
            )
        )
    )

    assert ibclc["artifact_type"] == "ibclc_consult_card"
    assert ibclc["reason"] == "Latch pain"
    assert runtime_service.artifacts[-1].payload["feeding_context"] == "Pain on left side after feeding."
    assert runtime_service.artifacts[-1].payload["consultant"]["credentials"] == "IBCLC 国际认证哺乳顾问"
    assert runtime_service.artifacts[-1].payload["chat"]["label"] == "咨询 IBCLC"


@pytest.mark.parametrize(
    "message",
    [
        "先不用找 IBCLC",
        "IBCLC 是什么？",
        "我需要找 IBCLC 吗？",
        "好的",
    ],
)
def test_ibclc_card_handler_blocks_without_explicit_semantic_consent(message: str) -> None:
    runtime_service = FakeAgentRuntimeService()
    initial_artifact_count = len(runtime_service.artifacts)

    result = asyncio.run(
        IbclcConsultCardCreateToolHandler(runtime_service=runtime_service)(
            _context(args={"reason": "Latch pain", "trusted_current_user_text": message})
        )
    )

    assert result["status"] == "ibclc_consult_blocked"
    assert result["requires_confirmation"] is True
    assert len(runtime_service.artifacts) == initial_artifact_count


def test_ibclc_card_handler_allows_short_confirmation_after_previous_offer() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        IbclcConsultCardCreateToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "reason": "Latch pain",
                    "trusted_current_user_text": "好的",
                    "trusted_previous_assistant_text": "需要我帮你打开 IBCLC 在线咨询入口吗？",
                }
            )
        )
    )

    assert result["status"] == "created"
    assert result["artifact_type"] == "ibclc_consult_card"


def test_ibclc_card_handler_allows_confirmation_after_canonical_cross_clause_offer() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        IbclcConsultCardCreateToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "reason": "Latch pain",
                    "trusted_current_user_text": "好的",
                    "trusted_previous_assistant_text": "我这里有很多优秀的 IBCLC 可以帮助到你，你需要我帮你推荐吗？",
                }
            )
        )
    )

    assert result["status"] == "created"
    assert result["artifact_type"] == "ibclc_consult_card"


@pytest.mark.parametrize(
    "message",
    [
        "别推荐 IBCLC",
        "不推荐 IBCLC",
        "我不想咨询 IBCLC",
        "我不想联系 IBCLC",
        "别打开 IBCLC 咨询入口",
        "取消 IBCLC 咨询",
        "你推荐 IBCLC 吗？",
        "请问你推荐 IBCLC 吗？",
        "麻烦问下，你推荐 IBCLC 吗？",
    ],
)
def test_ibclc_card_handler_blocks_negative_or_decision_question_bypasses(message: str) -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        IbclcConsultCardCreateToolHandler(runtime_service=runtime_service)(
            _context(args={"reason": "Latch pain", "trusted_current_user_text": message})
        )
    )

    assert result["status"] == "ibclc_consult_blocked"
    assert not any(artifact.artifact_type == "ibclc_consult_card" for artifact in runtime_service.artifacts)


def test_ibclc_card_handler_accepts_legacy_short_affirmation_after_previous_offer() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        IbclcConsultCardCreateToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "reason": "Latch pain",
                    "trusted_current_user_text": "嗯嗯",
                    "trusted_previous_assistant_text": "需要我帮你推荐一位 IBCLC 哺乳顾问吗？",
                }
            )
        )
    )

    assert result["status"] == "created"


@pytest.mark.parametrize(
    "previous_assistant_text",
    [
        "我不建议继续忍痛，需要我帮你打开 IBCLC 在线咨询入口吗？",
        "没有必要一个人硬撑，需要我帮你推荐一位 IBCLC 吗？",
    ],
)
def test_ibclc_card_handler_accepts_offer_after_negative_context_clause(
    previous_assistant_text: str,
) -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        IbclcConsultCardCreateToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "reason": "Latch pain",
                    "trusted_current_user_text": "好的",
                    "trusted_previous_assistant_text": previous_assistant_text,
                }
            )
        )
    )

    assert result["status"] == "created"


@pytest.mark.parametrize(
    "previous_assistant_text",
    [
        "我现在不能帮你推荐 IBCLC 哺乳顾问。",
        "目前不需要我帮你打开 IBCLC 在线咨询入口。",
        "现在无需我帮你推荐 IBCLC 哺乳顾问。",
        "暂时不用我帮你联系泌乳顾问。",
        "我不建议现在帮你推荐 IBCLC 哺乳顾问。",
        "目前没有必要帮你打开 IBCLC 在线咨询入口。",
        "我不建议现在帮你找 IBCLC，需要我帮你看看其他喂养资料吗？",
        "目前没有必要打开 IBCLC 在线咨询入口，要我继续讲讲含乳姿势吗？",
        "目前不需要找 IBCLC，你需要我帮你整理喂养记录吗？",
        "IBCLC 是国际认证哺乳顾问。你需要我帮你整理喂养记录吗？",
        "IBCLC 可以提供专业支持，你需要我帮你整理喂养记录吗？",
    ],
)
def test_ibclc_card_handler_rejects_short_affirmation_after_negated_previous_offer(
    previous_assistant_text: str,
) -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        IbclcConsultCardCreateToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "reason": "Latch pain",
                    "trusted_current_user_text": "好的",
                    "trusted_previous_assistant_text": previous_assistant_text,
                }
            )
        )
    )

    assert result["status"] == "ibclc_consult_blocked"
    assert result["reason"] == "short_confirmation_without_ibclc_offer"


def test_ibclc_card_handler_reuses_same_run_artifact_without_duplicate_event() -> None:
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        args={
            "reason": "Latch pain",
            "trusted_current_user_text": "请帮我找一位 IBCLC 顾问",
        }
    )

    first = asyncio.run(IbclcConsultCardCreateToolHandler(runtime_service=runtime_service)(context))
    second = asyncio.run(IbclcConsultCardCreateToolHandler(runtime_service=runtime_service)(context))

    cards = [artifact for artifact in runtime_service.artifacts if artifact.artifact_type == "ibclc_consult_card"]
    assert len(cards) == 1
    assert first["artifact_id"] == second["artifact_id"]
    assert first["reused"] is False
    assert second["reused"] is True
    assert "_deferred_agent_events" in first
    assert "_deferred_agent_events" not in second


def test_pregnancy_plan_propose_tool_handler_applies_without_duplicate_confirmation() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        actor=actor,
        args={
            "summary": "Prepare appointments and bag tasks.",
            "due_date_or_week": "99周",
            "birth_path": "剖宫产",
            "additional_info": "模型臆造的补充不得覆盖耐久工作流事实",
            "runtime_plan_context": {
                "has_active_plan": False,
                "delivery_date": "2026-09-18",
                "current_week": "32周",
                "due_date_or_week": "32周",
                "birth_path": "顺产",
                "workflow_phase": "ready_to_generate",
                "analysis_run_id": str(uuid4()),
                "workflow_state_id": "workflow-1",
                "source_form_artifact_id": "form-1",
                "source_form_submission_id": "submission-1",
                "final_additional_info": "下周需要出差两天",
                "personalized_followup_records": [
                    {"topic": "doctor_special_notes_followup", "answer": "医生让我下周复查", "plan_impact": "不可信覆盖"},
                    {"topic": "prior_c_section_birth_path_detail", "answer": "上次因胎位剖宫产"},
                    {"topic": "chronic_medical_condition_coordination", "answer": "下周复核用药"},
                    {"topic": "ivf_week_confirmation", "answer": "第四条不得进入"},
                    {"topic": "foreign_owner_secret", "answer": "另一位用户的秘密"},
                ],
                "checkup_status": "已上传产检记录",
                "checkup_records_uploaded": "是",
                "owner_user_id": "other-owner-id",
                "foreign_plan_id": "other-owner-plan",
            },
        },
    )

    result = asyncio.run(PregnancyPlanProposeToolHandler(runtime_service=runtime_service)(context))

    assert result["action_type"] == PREGNANCY_PLAN_CREATE_ACTION
    assert result["action_status"] == "applied"
    assert result["requires_confirmation"] is False
    assert result["confirmation_policy"] == "explicit_intent"
    assert result["user_visible"] is False
    assert result["preview_payload"] == {
        "plan_type": "pregnancy",
        "title": "孕期计划",
        "summary": "Prepare appointments and bag tasks.",
        "has_payload": True,
    }
    assert runtime_service.calls[0]["target_type"] == "plan"
    assert runtime_service.calls[0]["side_effect_level"] == "medium"
    assert runtime_service.calls[0]["idempotency_key"].startswith("pregnancy-plan:")
    assert runtime_service.calls[0]["apply_payload"]["payload"]["plan_context"]["due_date_or_week"] == "32周"
    assert runtime_service.calls[0]["apply_payload"]["payload"]["plan_context"]["birth_path"] == "顺产"
    assert runtime_service.calls[0]["apply_payload"]["payload"]["plan_context"]["delivery_date"] == "2026-09-18"
    assert runtime_service.calls[0]["apply_payload"]["payload"]["plan_context"]["final_additional_info"] == "下周需要出差两天"
    sanitized_context = runtime_service.calls[0]["apply_payload"]["payload"]["plan_context"]
    assert len(sanitized_context["personalized_followup_records"]) == 3
    assert sanitized_context["checkup_status"] == "已上传产检记录"
    assert sanitized_context["checkup_records_uploaded"] == "是"
    assert "owner_user_id" not in sanitized_context
    assert "foreign_plan_id" not in sanitized_context
    assert "模型臆造的补充" not in str(sanitized_context)
    assert "不可信覆盖" not in str(sanitized_context)
    assert "第四条不得进入" not in str(sanitized_context)
    assert "另一位用户的秘密" not in str(sanitized_context)
    assert runtime_service.calls[0]["apply_payload"]["payload"]["lineage"] == {
        "workflow_state_id": "workflow-1",
        "source_form_artifact_id": "form-1",
        "source_form_submission_id": "submission-1",
    }
    assert runtime_service.calls[0]["apply_payload"]["payload"]["card"]["card_type"] == "birth_journey_plan_card"
    card_json = runtime_service.calls[0]["apply_payload"]["payload"]["card"]["card_json"]
    current_item_ids = {item["id"] for item in card_json["todo_plan"]["periods"][0]["items"]}
    assert len([item_id for item_id in current_item_ids if item_id.startswith("personalized_followup_")]) == 3
    assert "review_uploaded_checkup_records" in current_item_ids
    assert (
        runtime_service.calls[0]["apply_payload"]["payload"]["card"]["card_json"]["generation_context"]["additional_information_provided"]
        is True
    )
    assert result["artifact_type"] == "birth_journey_plan_card"
    card_artifact = runtime_service.artifacts[-1]
    assert card_artifact.artifact_type == "birth_journey_plan_card"
    assert card_artifact.payload["action_id"] == result["action_id"]
    assert runtime_service.workflow_state.workflow_type == "pregnancy_plan"
    assert runtime_service.workflow_state.status == "completed"
    assert runtime_service.workflow_state.active_step == ""
    assert runtime_service.workflow_state.state == {
        "phase": "ready_to_generate",
        "consumed_by_action_id": result["action_id"],
        "source_form_artifact_id": "form-1",
        "source_form_submission_id": "submission-1",
        "form_id": "birth_journey_basic_info_intake",
    }

    artifact_count = len(runtime_service.artifacts)
    context.args["summary"] = "Same analysis, different model wording."
    duplicate = asyncio.run(PregnancyPlanProposeToolHandler(runtime_service=runtime_service)(context))

    assert duplicate["action_id"] == result["action_id"]
    assert duplicate["action_status"] == "applied"
    assert len(runtime_service.artifacts) == artifact_count


def test_failed_pregnancy_plan_apply_does_not_create_card_or_consume_workflow() -> None:
    runtime_service = FakeAgentRuntimeService(failed_action_types={PREGNANCY_PLAN_CREATE_ACTION})
    artifact_count = len(runtime_service.artifacts)
    context = _context(
        args={
            "summary": "Prepare appointments and bag tasks.",
            "runtime_plan_context": {
                "has_active_plan": False,
                "current_week": "32周",
                "workflow_phase": "ready_to_generate",
                "analysis_run_id": str(uuid4()),
                "workflow_state_id": "workflow-1",
                "source_form_artifact_id": "form-1",
                "source_form_submission_id": "submission-1",
            },
        }
    )

    result = asyncio.run(PregnancyPlanProposeToolHandler(runtime_service=runtime_service)(context))

    assert isinstance(result, ToolHandlerResult)
    assert result.output["status"] == "action_failed"
    assert result.output["action_status"] == "failed"
    assert result.output["write_succeeded"] is False
    assert result.output["error_code"] == "plan_create_failed"
    assert len(runtime_service.artifacts) == artifact_count
    assert "no plan was created" in result.model_context[0]["content"]
    assert runtime_service.workflow_state is None


def test_pregnancy_plan_intake_start_creates_one_form_and_durable_workflow_state() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(
        actor=actor,
        args={
            "default_values": {"current_week": "32周", "age": 36},
            "runtime_plan_context": {"has_active_plan": False},
        },
    )

    result = asyncio.run(PregnancyPlanIntakeStartToolHandler(runtime_service=runtime_service)(context))

    assert result["status"] == "form_created"
    assert result["form"]["id"] == "birth_journey_basic_info_intake"
    assert result["form"]["title"] == "孕周与基本情况"
    assert result["form"]["default_values"] == {"current_week": "32周", "age": 36}
    required_ids = {field["id"] for field in result["form"]["fields"] if field["required"]}
    assert required_ids == {"current_week", "ivf", "fetus_count", "age", "first_birth", "birth_path"}
    form_artifact = runtime_service.artifacts[-1]
    assert form_artifact.artifact_type == "form"
    assert runtime_service.workflow_state.workflow_type == "pregnancy_plan"
    assert runtime_service.workflow_state.status == "collecting"
    assert runtime_service.workflow_state.active_step == "collecting_intake"
    assert runtime_service.workflow_state.state == {
        "phase": "collecting_intake",
        "source_form_artifact_id": str(form_artifact.id),
        "form_id": "birth_journey_basic_info_intake",
    }
    assert result["_deferred_agent_events"] == [
        {
            "event_type": "artifact.created",
            "payload": {
                "artifact_id": str(form_artifact.id),
                "artifact_type": "form",
                "schema_version": "1.0",
                "status": "created",
                "artifact": {
                    "id": str(form_artifact.id),
                    "artifact_type": "form",
                    "schema_version": "1.0",
                    "status": "created",
                    "payload": form_artifact.payload,
                    "raw_payload_ref": "",
                },
                "form": result["form"],
            },
        }
    ]


def test_pregnancy_plan_intake_start_reuses_existing_active_plan() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        PregnancyPlanIntakeStartToolHandler(runtime_service=runtime_service)(
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


@pytest.mark.parametrize(
    ("workflow", "expected"),
    [
        (
            {
                "phase": "collecting_intake",
                "source_form_artifact_id": "form-1",
            },
            {
                "status": "pregnancy_plan_intake_already_started",
                "form_artifact_id": "form-1",
            },
        ),
        (
            {"phase": "awaiting_additional_information"},
            {
                "status": "pregnancy_plan_intake_already_analyzed",
                "requires_user_reply": True,
            },
        ),
    ],
)
def test_pregnancy_plan_intake_start_does_not_reset_an_existing_workflow(
    workflow: dict[str, object],
    expected: dict[str, object],
) -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        PregnancyPlanIntakeStartToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "runtime_plan_context": {"has_active_plan": False},
                    "runtime_workflow_context": workflow,
                }
            )
        )
    )

    assert result == expected
    assert runtime_service.calls == []


def test_pregnancy_plan_intake_start_creates_a_fresh_form_after_prior_intake_was_consumed() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        PregnancyPlanIntakeStartToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "runtime_plan_context": {"has_active_plan": False},
                    "runtime_workflow_context": {
                        "phase": "awaiting_additional_information",
                        "consumed_by_action_id": "action-from-deleted-plan",
                        "source_form_artifact_id": "old-form",
                    },
                }
            )
        )
    )

    assert result["status"] == "form_created"
    assert runtime_service.artifacts[-1].artifact_type == "form"
    assert runtime_service.workflow_state.state["phase"] == "collecting_intake"
    assert runtime_service.workflow_state.state["source_form_artifact_id"] != "old-form"


def test_pregnancy_plan_intake_start_creates_a_fresh_form_after_safety_interruption() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        PregnancyPlanIntakeStartToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "runtime_plan_context": {"has_active_plan": False},
                    "runtime_workflow_context": {
                        "phase": "collecting_intake",
                        "interrupted_by_safety_signal": True,
                        "source_form_artifact_id": "urgent-form",
                    },
                }
            )
        )
    )

    assert result["status"] == "form_created"
    assert runtime_service.artifacts[-1].artifact_type == "form"
    assert runtime_service.workflow_state.state["phase"] == "collecting_intake"
    assert runtime_service.workflow_state.state["source_form_artifact_id"] != "urgent-form"


def test_pregnancy_plan_intake_start_restarts_only_when_explicitly_requested() -> None:
    runtime_service = FakeAgentRuntimeService()
    old_form_id = "old-active-form"

    result = asyncio.run(
        PregnancyPlanIntakeStartToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "restart": True,
                    "runtime_plan_context": {"has_active_plan": False},
                    "runtime_workflow_context": {
                        "phase": "collecting_intake",
                        "source_form_artifact_id": old_form_id,
                    },
                }
            )
        )
    )

    assert result["status"] == "form_created"
    assert runtime_service.workflow_state.state["source_form_artifact_id"] != old_form_id
    assert runtime_service.workflow_calls[-1]["expires_at"] > datetime.now(timezone.utc)


def test_pregnancy_plan_intake_can_be_abandoned_and_then_requires_fresh_intake() -> None:
    runtime_service = FakeAgentRuntimeService()
    workflow = {
        "phase": "personalized_followup",
        "source_form_artifact_id": "form-1",
        "source_form_submission_id": "submission-1",
    }

    result = asyncio.run(
        PregnancyPlanIntakeAdvanceToolHandler(runtime_service=runtime_service)(
            _context(args={"action": "abandon", "runtime_workflow_context": workflow})
        )
    )

    assert result == {
        "status": "pregnancy_plan_intake_abandoned",
        "requires_fresh_intake": True,
    }
    assert runtime_service.workflow_state.status == "failed"
    assert runtime_service.workflow_state.active_step == ""
    assert runtime_service.workflow_state.state["abandoned"] is True
    assert runtime_service.workflow_calls[-1]["expires_at"] is None


def test_pregnancy_plan_intake_analyze_uses_verified_form_and_returns_private_model_context() -> None:
    runtime_service = FakeAgentRuntimeService()
    run_id = uuid4()
    form_artifact_id = str(uuid4())
    context = ToolHandlerContext(
        actor=_user(),
        run_id=run_id,
        thread_id=uuid4(),
        tool_name="pregnancy.plan_intake.analyze",
        call_id="call-1",
        args={
            "form_artifact_id": form_artifact_id,
            "form_submission_id": "submission-1",
            "confirmed_form_data": {
                "current_week": "32周",
                "ivf": "是",
                "fetus_count": "双胎",
                "age": 36,
                "first_birth": "是",
                "birth_path": "还没确定",
                "medical_notes": "甲状腺用药",
                "doctor_notes": "医生提醒复查胎儿生长",
            },
            "runtime_plan_context": {"has_active_plan": False},
            "runtime_workflow_context": {
                "phase": "collecting_intake",
                "source_form_artifact_id": form_artifact_id,
            },
        },
    )

    result = asyncio.run(PregnancyPlanIntakeAnalyzeToolHandler(runtime_service=runtime_service)(context))

    assert isinstance(result, ToolHandlerResult)
    assert result.output == {
        "status": "intake_in_progress",
        "workflow_phase": "personalized_followup",
        "next_step": "personalized_followup",
        "focus_count": 7,
        "personalized": True,
        "requires_user_reply": True,
        "followup_round": 1,
        "followup_max_rounds": 3,
    }
    assert "甲状腺" not in str(result.output)
    trusted = result.model_context[0]["content"]
    assert "甲状腺用药" in trusted
    assert "医生提醒复查胎儿生长" in trusted
    assert "医生已经给了需要优先落实的特殊提醒" in trusted
    assert "这会直接影响复查时间、观察重点和异常联系路径" in trusted
    assert "这项提醒具体对应什么复查或观察要求" in trusted
    assert "还有其他需要补充的信息吗" not in trusted
    workflow = runtime_service.workflow_state
    assert workflow.workflow_type == "pregnancy_plan"
    assert workflow.status == "waiting"
    assert workflow.active_step == "personalized_followup"
    assert workflow.state["phase"] == "personalized_followup"
    assert workflow.state["analysis_run_id"] == str(run_id)
    assert workflow.state["source_form_submission_id"] == "submission-1"
    assert workflow.state["plan_context"]["medical_notes"] == "甲状腺用药"


def test_pregnancy_plan_initial_analysis_bridges_to_checkup_upload_before_asking_one_question() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        PregnancyPlanIntakeAnalyzeToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "form_artifact_id": "form-1",
                    "form_submission_id": "submission-1",
                    "confirmed_form_data": {
                        "current_week": "20周",
                        "ivf": "否",
                        "fetus_count": "单胎",
                        "age": 30,
                        "first_birth": "是",
                        "birth_path": "顺产",
                    },
                    "runtime_plan_context": {"has_active_plan": False},
                    "runtime_workflow_context": {
                        "phase": "collecting_intake",
                        "source_form_artifact_id": "form-1",
                    },
                }
            )
        )
    )

    assert isinstance(result, ToolHandlerResult)
    assert result.output["workflow_phase"] == "checkup_records_upload"
    trusted = result.model_context[0]["content"]
    assert "Briefly explain the 1-2 most material items from analysis" in trusted
    assert "Then ask exactly visible_question and stop" in trusted
    assert "请上传目前能找到的产检记录" in trusted
    assert "还有其他需要补充的信息吗" not in trusted


def test_pregnancy_plan_intake_analyze_rejects_missing_required_and_stale_submissions() -> None:
    handler = PregnancyPlanIntakeAnalyzeToolHandler(runtime_service=FakeAgentRuntimeService())
    valid_values = {
        "current_week": "20周",
        "ivf": "否",
        "fetus_count": "单胎",
        "age": 30,
        "first_birth": "是",
        "birth_path": "顺产",
    }

    with pytest.raises(ApiError) as missing:
        asyncio.run(
            handler(
                _context(
                    args={
                        "form_artifact_id": "form-1",
                        "form_submission_id": "submission-1",
                        "confirmed_form_data": {**valid_values, "current_week": ""},
                        "runtime_plan_context": {"has_active_plan": False},
                        "runtime_workflow_context": {
                            "phase": "collecting_intake",
                            "source_form_artifact_id": "form-1",
                        },
                    }
                )
            )
        )
    assert missing.value.code == "validation_failed"
    assert missing.value.details["missing_fields"] == ["current_week"]

    with pytest.raises(ApiError) as invalid:
        asyncio.run(
            handler(
                _context(
                    args={
                        "form_artifact_id": "form-1",
                        "form_submission_id": "submission-1",
                        "confirmed_form_data": {**valid_values, "age": 999},
                        "runtime_plan_context": {"has_active_plan": False},
                        "runtime_workflow_context": {
                            "phase": "collecting_intake",
                            "source_form_artifact_id": "form-1",
                        },
                    }
                )
            )
        )
    assert invalid.value.code == "validation_failed"
    assert invalid.value.details["invalid_fields"] == ["age"]

    with pytest.raises(ApiError) as stale:
        asyncio.run(
            handler(
                _context(
                    args={
                        "form_artifact_id": "form-old",
                        "form_submission_id": "submission-old",
                        "confirmed_form_data": valid_values,
                        "runtime_plan_context": {"has_active_plan": False},
                        "runtime_workflow_context": {
                            "phase": "collecting_intake",
                            "source_form_artifact_id": "form-new",
                        },
                    }
                )
            )
        )
    assert stale.value.code == "stale_pregnancy_plan_intake"


def test_pregnancy_plan_intake_analyze_reuses_the_same_submission_snapshot() -> None:
    runtime_service = FakeAgentRuntimeService()
    workflow = {
        "phase": "checkup_records_upload",
        "source_form_artifact_id": "form-1",
        "source_form_submission_id": "submission-1",
        "form_id": "birth_journey_basic_info_intake",
        "analysis_run_id": str(uuid4()),
        "plan_context": {"current_week": "20周", "age": 30},
        "analysis": {
            "stage": {"id": "second_trimester", "current_week": 20, "summary": "孕中期"},
            "focuses": [
                {
                    "id": "pregnancy_stage_timing",
                    "title": "当前孕期阶段",
                    "management_meaning": "孕中期检查有时间窗。",
                    "plan_impact": "按孕周安排。",
                }
            ],
            "final_question": "还有其他需要补充的信息吗？如果没有，我就基于目前的信息开始为你制定孕期计划啦。",
        },
        "followup_topics": [],
        "personalized_followup_records": [],
        "visible_question": "请上传目前能找到的产检记录，我会把关键复查和待确认项纳入计划；如果暂时没有或不方便上传，也可以直接跳过。",
    }

    result = asyncio.run(
        PregnancyPlanIntakeAnalyzeToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "form_artifact_id": "form-1",
                    "form_submission_id": "submission-1",
                    "confirmed_form_data": {
                        "current_week": "20周",
                        "ivf": "否",
                        "fetus_count": "单胎",
                        "age": 30,
                        "first_birth": "否",
                        "birth_path": "顺产",
                    },
                    "runtime_plan_context": {"has_active_plan": False},
                    "runtime_workflow_context": workflow,
                }
            )
        )
    )

    assert isinstance(result, ToolHandlerResult)
    assert result.output["status"] == "intake_in_progress"
    assert result.output["workflow_phase"] == "checkup_records_upload"
    assert result.output["focus_count"] == 1
    assert result.output["personalized"] is False
    assert len(runtime_service.artifacts) == 1


def test_pregnancy_plan_intake_advance_exposes_one_followup_with_full_reasoning_contract() -> None:
    runtime_service = FakeAgentRuntimeService()
    workflow = {
        "phase": "personalized_followup",
        "source_form_artifact_id": "form-1",
        "source_form_submission_id": "submission-1",
        "analysis_run_id": "analysis-run",
        "plan_context": {"current_week": "20周", "age": 36},
        "analysis": {"stage": {"id": "second_trimester"}, "focuses": []},
        "followup_topics": [
            {
                "id": "age_35_plus_checkup_detail",
                "observation": "你 36 岁，在产科管理上通常会被归入高龄孕产妇范围。",
                "management_meaning": "高龄孕产妇属于产科管理分层。",
                "plan_impact": "计划会更早关注血压血糖、胎儿生长和复查节奏。",
                "question": "有没有已经被提醒过或正在复查的项目？暂无异常也可以。",
                "reply_options": ["暂无异常", "正在复查", "还不确定"],
            }
        ],
        "personalized_followup_records": [],
    }

    result = asyncio.run(
        PregnancyPlanIntakeAdvanceToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "action": "submit_personalized_followup",
                    "topic": "age_35_plus_checkup_detail",
                    "answer": "暂无异常",
                    "runtime_workflow_context": workflow,
                    "runtime_checkup_attachment_count": 0,
                }
            )
        )
    )

    assert isinstance(result, ToolHandlerResult)
    assert result.output["workflow_phase"] == "checkup_records_upload"
    model_context = result.model_context[0]["content"]
    assert "你 36 岁" in model_context
    assert "高龄孕产妇属于产科管理分层" in model_context
    assert "计划会更早关注血压血糖" in model_context
    assert model_context.count("有没有已经被提醒过或正在复查的项目") == 1


@pytest.mark.parametrize("model_topic", [None, "multiple_pregnancy_type"])
def test_pregnancy_plan_intake_advance_binds_the_runtime_current_followup_topic(model_topic: str | None) -> None:
    runtime_service = FakeAgentRuntimeService()
    workflow = {
        "phase": "personalized_followup",
        "source_form_artifact_id": "form-1",
        "source_form_submission_id": "submission-1",
        "analysis_run_id": "analysis-run",
        "plan_context": {"current_week": "25周", "fetus_count": "双胎"},
        "analysis": {"stage": {"id": "second_trimester"}, "focuses": []},
        "followup_topics": [
            {
                "id": "multiple_pregnancy_monitoring",
                "observation": "多胎妊娠需要更密切地关注复查节奏。",
                "management_meaning": "双胎类型会影响监测重点。",
                "plan_impact": "计划会纳入对应的复查与异常联系路径。",
                "question": "目前双胎类型确认了吗？",
                "reply_options": ["单绒双羊", "双绒双羊", "还没确认"],
            }
        ],
        "personalized_followup_records": [],
    }

    model_args = {
        "action": "submit_personalized_followup",
        "answer": "模型转述不应成为可信答案",
        "trusted_current_user_text": "双胎类型还没完全确认",
        "runtime_workflow_context": workflow,
        "runtime_checkup_attachment_count": 0,
    }
    if model_topic is not None:
        model_args["topic"] = model_topic

    result = asyncio.run(PregnancyPlanIntakeAdvanceToolHandler(runtime_service=runtime_service)(_context(args=model_args)))

    assert isinstance(result, ToolHandlerResult)
    assert runtime_service.workflow_state is not None
    assert runtime_service.workflow_state.state["personalized_followup_records"] == [
        {
            "topic": "multiple_pregnancy_monitoring",
            "question": "目前双胎类型确认了吗？",
            "answer": "双胎类型还没完全确认",
            "plan_impact": "计划会纳入对应的复查与异常联系路径。",
        }
    ]


def test_pregnancy_plan_intake_upload_cannot_be_forged_without_runtime_verified_attachment() -> None:
    workflow = {
        "phase": "checkup_records_upload",
        "source_form_artifact_id": "form-1",
        "source_form_submission_id": "submission-1",
        "analysis_run_id": "analysis-run",
        "plan_context": {"current_week": "20周"},
        "analysis": {"stage": {"id": "second_trimester"}, "focuses": []},
        "followup_topics": [],
        "personalized_followup_records": [],
    }
    runtime_service = FakeAgentRuntimeService()
    handler = PregnancyPlanIntakeAdvanceToolHandler(runtime_service=runtime_service)

    unverified = asyncio.run(
        handler(
            _context(
                args={
                    "action": "mark_checkup_records_uploaded",
                    "runtime_workflow_context": workflow,
                    "runtime_checkup_attachment_count": 0,
                }
            )
        )
    )

    assert isinstance(unverified, ToolHandlerResult)
    assert unverified.output == {
        "status": "checkup_attachment_required",
        "workflow_phase": "checkup_records_upload",
        "next_step": "checkup_records_upload",
        "requires_user_reply": True,
    }
    assert runtime_service.workflow_state is None

    verified = asyncio.run(
        handler(
            _context(
                args={
                    "action": "mark_checkup_records_uploaded",
                    "runtime_workflow_context": workflow,
                    "runtime_checkup_attachment_count": 1,
                }
            )
        )
    )
    assert isinstance(verified, ToolHandlerResult)
    assert verified.output["workflow_phase"] == "final_plan_confirmation"
    assert runtime_service.workflow_state.state["plan_context"]["checkup_records_uploaded"] == "是"


def test_pregnancy_plan_intake_analyze_stops_for_urgent_signals_without_advancing_workflow() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        PregnancyPlanIntakeAnalyzeToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "form_artifact_id": "form-1",
                    "form_submission_id": "submission-urgent",
                    "confirmed_form_data": {
                        "current_week": "32周",
                        "ivf": "否",
                        "fetus_count": "单胎",
                        "age": 30,
                        "first_birth": "是",
                        "birth_path": "顺产",
                        "doctor_notes": "刚刚胎动明显减少",
                    },
                    "runtime_plan_context": {"has_active_plan": False},
                    "runtime_workflow_context": {
                        "phase": "collecting_intake",
                        "source_form_artifact_id": "form-1",
                    },
                }
            )
        )
    )

    assert isinstance(result, ToolHandlerResult)
    assert result.output["status"] == "urgent_care_required"
    assert result.output["signal_ids"] == ["reduced_fetal_movement"]
    assert result.output["blocks_plan_flow"] is True
    assert "孕期计划啦" not in result.output["required_response"]
    assert len(runtime_service.artifacts) == 1
    assert runtime_service.calls == []


def test_pregnancy_plan_propose_requires_workflow_ready_to_generate() -> None:
    runtime_service = FakeAgentRuntimeService()
    run_id = uuid4()
    handler = PregnancyPlanProposeToolHandler(runtime_service=runtime_service)

    missing = asyncio.run(
        handler(
            ToolHandlerContext(
                actor=_user(),
                run_id=run_id,
                thread_id=uuid4(),
                tool_name="pregnancy.plan.propose",
                call_id="call-1",
                args={"runtime_plan_context": {"has_active_plan": False, "workflow_phase": "collecting_intake"}},
            )
        )
    )
    assert missing == {"status": "needs_pregnancy_plan_intake"}

    not_ready = asyncio.run(
        handler(
            ToolHandlerContext(
                actor=_user(),
                run_id=run_id,
                thread_id=uuid4(),
                tool_name="pregnancy.plan.propose",
                call_id="call-2",
                args={
                    "runtime_plan_context": {
                        "has_active_plan": False,
                        "workflow_phase": "final_plan_confirmation",
                        "current_week": "32周",
                    }
                },
            )
        )
    )
    assert not_ready == {"status": "pregnancy_plan_intake_in_progress", "next_step": "final_plan_confirmation"}
    assert runtime_service.calls == []


def test_pregnancy_plan_propose_stops_for_urgent_supplemental_information() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        PregnancyPlanProposeToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "additional_info": "我刚刚破水了",
                    "runtime_plan_context": {
                        "has_active_plan": False,
                        "workflow_phase": "awaiting_additional_information",
                        "analysis_run_id": str(uuid4()),
                        "current_week": "32周",
                    },
                }
            )
        )
    )

    assert isinstance(result, ToolHandlerResult)
    assert result.output["status"] == "urgent_care_required"
    assert result.output["signal_ids"] == ["rupture_of_membranes"]
    assert runtime_service.calls == []


def test_pregnancy_plan_propose_uses_trusted_current_message_for_urgent_guard_when_model_omits_supplement() -> None:
    runtime_service = FakeAgentRuntimeService()

    result = asyncio.run(
        PregnancyPlanProposeToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "trusted_current_user_text": "我现在大量出血",
                    "runtime_plan_context": {
                        "has_active_plan": False,
                        "workflow_phase": "awaiting_additional_information",
                        "analysis_run_id": str(uuid4()),
                        "current_week": "32周",
                    },
                }
            )
        )
    )

    assert isinstance(result, ToolHandlerResult)
    assert result.output["status"] == "urgent_care_required"
    assert result.output["signal_ids"] == ["heavy_bleeding"]
    assert runtime_service.calls == []


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
    assert result["action_status"] == "applied"
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
    assert result["action_status"] == "applied"
    assert result["user_visible"] is False
    assert result["preview_payload"] == {
        "task_id": str(task_id),
        "completed": False,
    }
    assert runtime_service.calls[0]["target_type"] == "plan_task"
    assert runtime_service.calls[0]["side_effect_level"] == "medium"
    assert runtime_service.calls[0]["apply_payload"]["task_id"] == str(task_id)
    assert runtime_service.calls[0]["apply_payload"]["completed"] is False
    assert runtime_service.calls[0]["apply_payload"]["metadata"] == {"timezone": "Asia/Shanghai"}


def test_pregnancy_plan_todo_update_propose_targets_embedded_plan_item() -> None:
    runtime_service = FakeAgentRuntimeService()
    plan_id = uuid4()

    result = asyncio.run(
        PregnancyPlanTodoUpdateProposeToolHandler(runtime_service=runtime_service)(
            _context(
                args={
                    "plan_id": str(plan_id),
                    "item_id": "prepare-hospital-bag",
                    "completed": True,
                    "expected_version": 2,
                }
            )
        )
    )

    assert result["action_type"] == PREGNANCY_PLAN_TODO_UPDATE_ACTION
    assert result["action_status"] == "applied"
    assert result["preview_payload"] == {
        "plan_id": str(plan_id),
        "item_id": "prepare-hospital-bag",
        "completed": True,
        "expected_version": 2,
    }
    assert runtime_service.calls[-1]["target_type"] == "plan"
    assert runtime_service.calls[-1]["target_id"] == str(plan_id)


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
    assert update["action_status"] == "applied"
    assert update["preview_payload"]["fields"] == ["task_date", "task_time", "title"]
    assert runtime_service.calls[-3]["target_id"] == str(task_id)
    assert task_delete["action_type"] == PLAN_TASK_DELETE_ACTION
    assert task_delete["action_status"] == "applied"
    assert task_delete["preview_payload"]["reason"] == "no longer needed"
    assert plan_delete["action_type"] == PLAN_DELETE_ACTION
    assert plan_delete["action_status"] == "applied"
    assert plan_delete["user_visible"] is False
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


def test_pregnancy_diary_manage_write_creates_content_only() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)
    context = _context(
        actor=actor,
        args={
            "action": "write",
            "entry_date": "2026-07-04",
            "content": "Today I felt steady.",
            "capture_mode": "explicit_request",
            "capture_evidence": "Save this to my diary.",
            "trusted_current_user_text": "Save this to my diary.",
        },
    )

    result = asyncio.run(PregnancyDiaryManageToolHandler(diary_service=diary_service)(context))

    assert result["status"] == "entry_created"
    assert result["entry"]["entry_date"] == "2026-07-04"
    assert diary_service.create_kwargs["owner_user_id"] == actor.user_id
    assert diary_service.create_kwargs["values"] == {"content": "Today I felt steady."}


def test_pregnancy_diary_manage_write_bounds_long_model_call_id_for_audit() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)
    run_id = uuid4()
    context = ToolHandlerContext(
        actor=actor,
        run_id=run_id,
        tool_name="pregnancy_diary.manage",
        call_id="fc_" + "x" * 120,
        args={
            "action": "write",
            "entry_date": "2026-07-04",
            "content": "Today I felt steady.",
            "capture_mode": "explicit_request",
            "capture_evidence": "Save this to my diary.",
            "trusted_current_user_text": "Save this to my diary.",
        },
    )

    asyncio.run(PregnancyDiaryManageToolHandler(diary_service=diary_service)(context))

    request_id = diary_service.create_kwargs["request_id"]
    assert request_id.startswith(f"agent-tool:{run_id}:")
    assert len(request_id) <= 80


def test_pregnancy_diary_manage_write_returns_full_existing_entry_on_date_conflict() -> None:
    actor = _user()
    diary_service = ExistingDiaryService(owner_user_id=actor.user_id)

    result = asyncio.run(
        PregnancyDiaryManageToolHandler(diary_service=diary_service)(
            _context(
                actor=actor,
                args={
                    "action": "write",
                    "entry_date": "2026-07-02",
                    "content": "New content",
                    "capture_mode": "explicit_request",
                    "capture_evidence": "Keep that memory.",
                    "trusted_current_user_text": "Keep that memory.",
                },
            )
        )
    )

    assert result["status"] == "entry_already_exists"
    assert result["entry"]["id"]
    assert result["entry"]["content"] == "x" * 600
    assert result["side_effect_performed"] is False
    assert "action=update" in result.retained_information[0].guidance
    assert "content" not in result.retained_information[0].information["entry"]


def test_pregnancy_diary_manage_update_replaces_with_complete_content() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)

    result = asyncio.run(
        PregnancyDiaryManageToolHandler(diary_service=diary_service)(
            _context(
                actor=actor,
                args={
                    "action": "update",
                    "entry_date": "2026-07-02",
                    "content": "Earlier facts and the new fact rewritten as one complete entry.",
                    "capture_mode": "explicit_request",
                    "capture_evidence": "Keep that memory.",
                    "trusted_current_user_text": "Keep that memory.",
                },
            )
        )
    )

    assert result["status"] == "entry_updated"
    assert diary_service.update_kwargs["owner_user_id"] == actor.user_id
    assert diary_service.update_kwargs["values"] == {
        "content": "Earlier facts and the new fact rewritten as one complete entry."
    }
    assert "content_mode" not in diary_service.update_kwargs


def test_pregnancy_diary_automatic_capture_requires_persisted_opt_in() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)

    result = asyncio.run(
        PregnancyDiaryManageToolHandler(diary_service=diary_service)(
            _context(
                actor=actor,
                args={
                    "action": "write",
                    "entry_date": "2026-07-04",
                    "content": "Today the baby kicked after lunch.",
                    "capture_mode": "automatic",
                    "runtime_auto_capture_enabled": False,
                },
            )
        )
    )

    assert result["status"] == "auto_capture_consent_required"
    assert result["side_effect_performed"] is False
    assert diary_service.create_kwargs == {}


def test_pregnancy_diary_automatic_capture_writes_after_persisted_opt_in() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)

    result = asyncio.run(
        PregnancyDiaryManageToolHandler(diary_service=diary_service)(
            _context(
                actor=actor,
                args={
                    "action": "write",
                    "entry_date": "2026-07-04",
                    "content": "Today the baby kicked after lunch.",
                    "capture_mode": "automatic",
                    "runtime_auto_capture_enabled": True,
                },
            )
        )
    )

    assert result["status"] == "entry_created"
    assert diary_service.create_kwargs["values"] == {"content": "Today the baby kicked after lunch."}


def test_pregnancy_diary_explicit_capture_requires_current_message_evidence() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)

    result = asyncio.run(
        PregnancyDiaryManageToolHandler(diary_service=diary_service)(
            _context(
                actor=actor,
                args={
                    "action": "write",
                    "entry_date": "2026-07-04",
                    "content": "Today the baby kicked after lunch.",
                    "capture_mode": "explicit_request",
                    "capture_evidence": "Save this to my diary.",
                    "trusted_current_user_text": "Today the baby kicked after lunch.",
                },
            )
        )
    )

    assert result["status"] == "explicit_capture_request_required"
    assert result["side_effect_performed"] is False
    assert diary_service.create_kwargs == {}


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


def test_milk_plan_propose_tool_handler_requires_direction() -> None:
    actor = _user()
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            MilkPlanProposeToolHandler(
                runtime_service=FakeAgentRuntimeService(),
                plans_service=FakePlansService(owner_user_id=actor.user_id),
            )(_context(actor=actor, args={}))
        )

    assert exc_info.value.code == "validation_failed"


def test_milk_plan_propose_rejects_invalid_preferred_time_before_confirmation() -> None:
    actor = _user()
    runtime_service = FakeAgentRuntimeService()
    context = _context(actor=actor, args={"direction": "maintain", "preferred_pumping_times": ["25:00"]})
    analysis_context = {"records_snapshot": {"status": "normal"}, "answers": {}}
    runtime_service.workflow_state = AgentWorkflowState(
        id=uuid4(),
        thread_id=context.thread_id,
        owner_user_id=actor.user_id,
        run_id=context.run_id,
        workflow_type="milk_analysis",
        status="ready",
        schema_version="milk_analysis.v1",
        state={
            "phase": "assessment_complete",
            "assessment": {
                "analysis_context": analysis_context,
                "analysis_context_fingerprint": milk_analysis_context_fingerprint(analysis_context),
                "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
                "plan_decision": {"can_start_plan": True, "recommended_direction": "maintain"},
            },
        },
        active_step="assessment_complete",
    )
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            MilkPlanProposeToolHandler(
                runtime_service=runtime_service,
                plans_service=FakePlansService(owner_user_id=actor.user_id),
            )(context)
        )

    assert exc_info.value.code == "validation_failed"
    assert "preferred" in exc_info.value.message


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


def test_pregnancy_diary_manage_write_requires_content() -> None:
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            PregnancyDiaryManageToolHandler(diary_service=FakeDiaryService(owner_user_id=_user().user_id))(
                _context(args={"action": "write", "entry_date": "2026-07-04"})
            )
        )

    assert exc_info.value.code == "validation_failed"


def test_pregnancy_diary_manage_delete_requires_confirmation() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)

    result = asyncio.run(
        PregnancyDiaryManageToolHandler(diary_service=diary_service)(
            _context(actor=actor, args={"action": "delete", "entry_date": "2026-07-04", "confirmed": False})
        )
    )

    assert result["status"] == "needs_delete_confirmation"
    assert result["side_effect_performed"] is False
    assert diary_service.delete_kwargs == {}


def test_pregnancy_diary_manage_delete_rejects_model_only_confirmation() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)

    result = asyncio.run(
        PregnancyDiaryManageToolHandler(diary_service=diary_service)(
            _context(
                actor=actor,
                args={
                    "action": "delete",
                    "entry_date": "2026-07-04",
                    "confirmed": True,
                    "confirmation_evidence": "请删除 7 月 4 日的日记",
                    "trusted_current_user_text": "我想看看 7 月 4 日的日记",
                },
            )
        )
    )

    assert result["status"] == "needs_delete_confirmation"
    assert result["side_effect_performed"] is False
    assert diary_service.delete_kwargs == {}


def test_pregnancy_diary_manage_delete_deletes_confirmed_entry_synchronously() -> None:
    actor = _user()
    diary_service = FakeDiaryService(owner_user_id=actor.user_id)

    result = asyncio.run(
        PregnancyDiaryManageToolHandler(diary_service=diary_service)(
            _context(
                actor=actor,
                args={
                    "action": "delete",
                    "entry_date": "2026-07-04",
                    "confirmed": True,
                    "confirmation_evidence": "请删除 7 月 4 日的日记",
                    "trusted_current_user_text": "请删除 7 月 4 日的日记",
                },
            )
        )
    )

    assert result["status"] == "entry_deleted"
    assert result["entry_date"] == "2026-07-04"
    assert result.retained_information[0].context_key == "pregnancy_diary:entry:2026-07-04"
    assert result.retained_information[0].priority == 200
    assert result.retained_information[0].invalidate_prefixes == ("pregnancy_diary:",)
    assert diary_service.delete_kwargs["owner_user_id"] == actor.user_id
    assert diary_service.delete_kwargs["entry_date"] == date(2026, 7, 4)


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
        "records.milk_analysis.intake",
        "records.milk_analysis.evaluate",
        "records.pumping_record_delete.propose",
        "plans.calendar.read",
        "plans.current.read",
        "pregnancy_diary.manage",
        "devices.guidance.read",
        "devices.pump_status.read",
        "devices.unboxing.advance",
        "conversation_history.image.load",
        "notifications.milk_reminder.propose",
        "plans.milk_plan.propose",
        "plans.milk_schedule.propose",
        "plans.milk_task_update.propose",
        "plans.milk_task_delete.propose",
        "plans.task_complete.propose",
        "plans.task_create.propose",
        "plans.task_delete.propose",
        "plans.task_update.propose",
        "plans.plan_delete.propose",
        "pregnancy.plan_context.read",
        "pregnancy.plan_intake.analyze",
        "pregnancy.plan_intake.advance",
        "pregnancy.plan_intake.start",
        "pregnancy.plan.propose",
        "pregnancy.plan_todo.propose",
        "records.feeding_record.propose",
        "records.pumping_record.propose",
        "support.ticket.propose",
    }
    assert isinstance(handlers["hospital_bag_cart_update"], HospitalBagCartUpdateProposeToolHandler)


def _context(*, actor: CurrentUser | None = None, args: dict | None = None, thread_id=None) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=actor or _user(),
        run_id=uuid4(),
        tool_name="tool",
        call_id="call-1",
        args=args or {},
        thread_id=thread_id or uuid4(),
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
        self.feeding_query = {}
        self.pumping_query = {}

    async def list_feedings(self, *, owner_user_id, start_at=None, end_at=None, limit):
        self.owner_user_id = owner_user_id
        self.feeding_query = {
            "owner_user_id": owner_user_id,
            "start_at": start_at,
            "end_at": end_at,
            "limit": limit,
        }
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

    async def list_pumpings(self, *, owner_user_id, start_at=None, end_at=None, limit):
        self.pumping_query = {
            "owner_user_id": owner_user_id,
            "start_at": start_at,
            "end_at": end_at,
            "limit": limit,
        }
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
        self.plan_payload = {}
        self.future_milk_tasks = []
        self.future_milk_tasks_query = {}

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
                payload=self.plan_payload,
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

    async def list_future_milk_plan_tasks(self, **kwargs):
        self.future_milk_tasks_query = kwargs
        return self.future_milk_tasks


class FakeMilkSchedulePlansService:
    def __init__(self, *, owner_user_id) -> None:
        self.plan = Plan(
            id=uuid4(),
            owner_user_id=owner_user_id,
            plan_type="milk_management",
            title="稳奶计划",
            summary="",
            status="active",
            source="agent_action",
            payload={},
        )
        self.tasks = [
            PlanTask(
                id=uuid4(),
                owner_user_id=owner_user_id,
                plan_id=self.plan.id,
                task_date=date(2026, 7, 14),
                task_time=time,
                title="吸奶",
                status="pending",
                payload={"task_type": "pumping", "duration_minutes": 30},
            )
            for time in ("08:00", "11:00", "14:00")
        ]

    async def get_plan(self, *, owner_user_id, plan_id):
        if owner_user_id != self.plan.owner_user_id or plan_id != self.plan.id:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        return self.plan

    async def list_tasks_for_plan(self, *, owner_user_id, plan_id, task_dates, status, limit):
        await self.get_plan(owner_user_id=owner_user_id, plan_id=plan_id)
        return [task for task in self.tasks if task.task_date in task_dates and task.status == status][:limit]

    async def list_tasks(self, *, owner_user_id, task_date, status, limit):
        await self.get_plan(owner_user_id=owner_user_id, plan_id=self.plan.id)
        return [task for task in self.tasks if task.task_date == task_date and task.status == status][:limit]


class FakeDiaryService:
    def __init__(self, *, owner_user_id) -> None:
        self._owner_user_id = owner_user_id
        self.owner_user_id = None
        self.limit = None
        self.create_kwargs = {}
        self.update_kwargs = {}
        self.delete_kwargs = {}

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

    async def update_entry_with_status(self, **kwargs):
        return DiaryEntryMutation(entry=await self.update_entry(**kwargs), changed=True)

    async def delete_entry(self, **kwargs):
        self.delete_kwargs = kwargs
        return self._entry(entry_date=kwargs["entry_date"])

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
                label="Air1 pump setup operation video",
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
                object_key="product-assets/device-guidance/assets/air1/images/air1_guide_parts_components.png",
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
    def __init__(self, *, failed_action_types: set[str] | None = None) -> None:
        self.calls = []
        self.actions = []
        self.artifacts = []
        self.workflow_state = None
        self.workflow_calls = []
        self.failed_action_types = failed_action_types or set()
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

    async def get_latest_workflow_state(self, **kwargs):
        workflow = self.workflow_state
        if workflow is None:
            return None
        if (
            workflow.owner_user_id != kwargs["owner_user_id"]
            or workflow.thread_id != kwargs["thread_id"]
            or workflow.workflow_type != kwargs["workflow_type"]
        ):
            return None
        return workflow

    async def upsert_workflow_state(self, **kwargs):
        self.workflow_calls.append(kwargs)
        if self.workflow_state is None or self.workflow_state.status in {"completed", "expired", "failed"}:
            self.workflow_state = AgentWorkflowState(
                id=uuid4(),
                thread_id=kwargs["thread_id"],
                owner_user_id=kwargs["owner_user_id"],
                run_id=kwargs["run_id"],
                workflow_type=kwargs["workflow_type"],
                status=kwargs["status"],
                schema_version=kwargs["schema_version"],
                state=kwargs["state"],
                active_step=kwargs["active_step"],
            )
        else:
            self.workflow_state.run_id = kwargs["run_id"]
            self.workflow_state.status = kwargs["status"]
            self.workflow_state.schema_version = kwargs["schema_version"]
            self.workflow_state.state = kwargs["state"]
            self.workflow_state.active_step = kwargs["active_step"]
        return self.workflow_state

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
                "failed"
                if kwargs["action_type"] in self.failed_action_types
                else (
                    "applied"
                    if kwargs["action_type"]
                    in {
                        "hospital_bag.cart.update",
                        "records.feeding_record.create",
                        "records.pumping_record.create",
                        "records.growth_record.create",
                        "records.feeding_record.delete",
                        "records.pumping_record.delete",
                        "records.growth_record.update",
                        "records.growth_record.delete",
                        "pregnancy.plan.create",
                        "pregnancy.plan_todo.update",
                        "plans.task.create",
                        "plans.task.complete",
                        "plans.task.update",
                        "plans.task.delete",
                        "plans.plan.delete",
                    }
                    else "confirmation_required"
                )
            ),
            side_effect_level=kwargs["side_effect_level"],
            preview_payload=kwargs["preview_payload"],
            apply_payload=kwargs["apply_payload"],
            idempotency_key=kwargs["idempotency_key"],
            error_code="plan_create_failed" if kwargs["action_type"] in self.failed_action_types else "",
        )
        self.actions.append(self.action)
        return self.action

    async def propose_action_once(self, **kwargs):
        reusable = next(
            (
                action
                for action in reversed(self.actions)
                if action.run_id == kwargs["run_id"]
                and action.actor_user_id == kwargs["owner_user_id"]
                and action.action_type == kwargs["action_type"]
                and action.idempotency_key == kwargs["idempotency_key"]
                and action.status in {"confirmation_required", "confirmed", "applying", "applied"}
            ),
            None,
        )
        if reusable is not None:
            return reusable, False
        return await self.propose_action(**kwargs), True

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

    async def create_artifact_once(self, **kwargs):
        existing = next(
            (
                artifact
                for artifact in reversed(self.artifacts)
                if artifact.run_id == kwargs["run_id"]
                and artifact.owner_user_id == kwargs["owner_user_id"]
                and artifact.artifact_type == kwargs["artifact_type"]
                and artifact.status != "deleted"
            ),
            None,
        )
        if existing is not None:
            return existing, False
        return await self.create_artifact(**kwargs), True


def _now() -> datetime:
    return datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc)
