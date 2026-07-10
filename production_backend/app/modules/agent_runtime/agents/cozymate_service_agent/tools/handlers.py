from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.memory.actions import AGENT_MEMORY_CREATE_ACTION
from production_backend.app.modules.agent_runtime.memory.service import validate_memory_write_policy
from production_backend.app.modules.agent_runtime.service import AgentRuntimeService
from production_backend.app.modules.assets.models import ProductAsset
from production_backend.app.modules.assets.service import ProductAssetService
from production_backend.app.modules.devices.models import PumpDevice, PumpTelemetryEvent
from production_backend.app.modules.devices.service import DevicesService
from production_backend.app.modules.diary.agent_actions import DIARY_ENTRY_UPSERT_ACTION
from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.modules.diary.service import DiaryService
from production_backend.app.modules.files.vision_service import FileVisionService
from production_backend.app.modules.hospital_bag import HOSPITAL_BAG_CART_UPDATE_ACTION
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
from production_backend.app.modules.plans.service import PlansService
from production_backend.app.modules.profiles.models import InfantProfile, UserProfile
from production_backend.app.modules.profiles.service import ProfileService
from production_backend.app.modules.records.agent_actions import (
    FEEDING_RECORD_CREATE_ACTION,
    FEEDING_RECORD_DELETE_ACTION,
    GROWTH_RECORD_CREATE_ACTION,
    GROWTH_RECORD_DELETE_ACTION,
    GROWTH_RECORD_UPDATE_ACTION,
    PUMPING_RECORD_CREATE_ACTION,
    PUMPING_RECORD_DELETE_ACTION,
)
from production_backend.app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from production_backend.app.modules.records.service import RecordsService
from production_backend.app.modules.support.agent_actions import SUPPORT_TICKET_CREATE_ACTION

from .executor import DEFERRED_AGENT_EVENTS_KEY, ToolHandler, ToolHandlerContext
from .legacy_artifacts import artifact_record_from_legacy_result, create_legacy_artifact_result


_DIARY_ENTRY_VALUE_FIELDS = (
    "gestational_week",
    "mood",
    "energy_level",
    "sleep_summary",
    "fetal_movement",
    "symptom_tags",
    "appointment_note",
    "nutrition_note",
    "content",
    "attachments",
)


class ProfileReadToolHandler:
    def __init__(self, *, service: ProfileService) -> None:
        self.service = service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        profile = await self.service.get_user_profile(user_id=context.actor.user_id)
        infants = await self.service.list_infants(owner_user_id=context.actor.user_id)
        return {
            "profile": _profile_payload(profile=profile, actor_user_id=context.actor.user_id),
            "infants": [_infant_payload(infant) for infant in infants],
        }


class ProfileUpdateToolHandler:
    def __init__(self, *, service: ProfileService) -> None:
        self.service = service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        values = _profile_update_values(context.args)
        if not values:
            raise ApiError(code="validation_failed", message="profile_update requires at least one field.", status=422)
        profile = await self.service.update_user_profile(
            user_id=context.actor.user_id,
            values=values,
            request_id=context.call_id,
        )
        return {
            "status": "profile_updated",
            "updated_fields": sorted(values),
            "profile": _profile_payload(profile=profile, actor_user_id=context.actor.user_id),
        }


class SupportTicketProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _support_ticket_apply_payload(context.args)
        issue_summary = _text(apply_payload, "issue_summary")
        if not issue_summary:
            raise ApiError(code="validation_failed", message="issue_summary is required.", status=422)

        preview_payload = _support_ticket_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=SUPPORT_TICKET_CREATE_ACTION,
            target_type="support_ticket",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:support-ticket",
        )
        return {
            "action_id": str(action.id),
            "action_type": action.action_type,
            "action_status": action.status,
            "requires_confirmation": _action_requires_confirmation(action),
            "preview_payload": preview_payload,
        }


class HospitalBagCartUpdateProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _hospital_bag_cart_apply_payload(context.args)
        cart_update = apply_payload.get("cart_update")
        if not isinstance(cart_update, dict) or not cart_update:
            raise ApiError(code="validation_failed", message="cart_update is required.", status=422)

        preview_payload = _hospital_bag_cart_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=HOSPITAL_BAG_CART_UPDATE_ACTION,
            target_type="hospital_bag_cart",
            side_effect_level="low",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:hospital-bag-cart",
        )
        return {
            "action_id": str(action.id),
            "action_type": action.action_type,
            "action_status": action.status,
            "requires_confirmation": _action_requires_confirmation(action),
            "preview_payload": preview_payload,
        }


class AgentArtifactCreateToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService, artifact_type: str, default_title: str) -> None:
        self.runtime_service = runtime_service
        self.artifact_type = artifact_type
        self.default_title = default_title

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        payload = _artifact_payload(args=context.args, default_title=self.default_title)
        artifact = await self.runtime_service.create_artifact(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type=self.artifact_type,
            schema_version="v1",
            status="created",
            payload=payload,
            emit_event=False,
        )
        return {
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "status": artifact.status,
            "title": _text(payload, "title"),
            "summary": _text(payload, "summary"),
            DEFERRED_AGENT_EVENTS_KEY: [_deferred_artifact_created_event(artifact)],
        }


class IbclcConsultCardCreateToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        payload = _ibclc_consult_card_payload(context.args)
        if not _text(payload, "reason"):
            raise ApiError(code="validation_failed", message="reason is required.", status=422)
        artifact = await self.runtime_service.create_artifact(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type="ibclc_consult_card",
            schema_version="v1",
            status="created",
            payload=payload,
            emit_event=False,
        )
        return {
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "status": artifact.status,
            "title": _text(payload, "title"),
            "reason": _text(payload, "reason"),
            "urgency": _text(payload, "urgency") or "routine",
            DEFERRED_AGENT_EVENTS_KEY: [_deferred_artifact_created_event(artifact)],
        }


class LegacyArtifactToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService, tool_name: str) -> None:
        self.runtime_service = runtime_service
        self.tool_name = tool_name

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        result = create_legacy_artifact_result(self.tool_name, context.args)
        artifact_record = artifact_record_from_legacy_result(result)
        if artifact_record is None:
            return result

        artifact = await self.runtime_service.create_artifact(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type=str(artifact_record["artifact_type"]),
            schema_version=str(artifact_record["schema_version"]),
            status="created",
            payload=artifact_record["payload"],
            emit_event=False,
        )
        return {
            **result,
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "schema_version": artifact.schema_version,
            DEFERRED_AGENT_EVENTS_KEY: [_deferred_artifact_created_event(artifact)],
        }


class BusinessContextReadToolHandler:
    def __init__(
        self,
        *,
        records_service: RecordsService,
        plans_service: PlansService,
        diary_service: DiaryService,
        devices_service: DevicesService,
    ) -> None:
        self.records_service = records_service
        self.plans_service = plans_service
        self.diary_service = diary_service
        self.devices_service = devices_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        owner_user_id = context.actor.user_id
        feedings = await self.records_service.list_feedings(owner_user_id=owner_user_id, limit=limit)
        pumpings = await self.records_service.list_pumpings(owner_user_id=owner_user_id, limit=limit)
        growth = await self.records_service.list_growth(owner_user_id=owner_user_id, limit=limit)
        plans = await self.plans_service.list_plans(owner_user_id=owner_user_id, limit=limit)
        tasks = await self.plans_service.list_tasks(owner_user_id=owner_user_id, limit=limit)
        diary_entries = await self.diary_service.list_entries(owner_user_id=owner_user_id, limit=limit)
        devices = await self.devices_service.list_devices(owner_user_id=owner_user_id)
        telemetry = await self.devices_service.list_telemetry_events(owner_user_id=owner_user_id, limit=limit)
        return {
            "records": {
                "feedings": [_feeding_payload(record) for record in feedings],
                "pumpings": [_pumping_payload(record) for record in pumpings],
                "growth": [_growth_payload(record) for record in growth],
            },
            "plans": {
                "plans": [_plan_payload(plan) for plan in plans],
                "tasks": [_task_payload(task) for task in tasks],
            },
            "diary": {
                "entries": [_diary_payload(entry) for entry in diary_entries],
            },
            "devices": {
                "pumps": [_device_payload(device) for device in devices[:limit]],
                "telemetry": [_telemetry_payload(event) for event in telemetry],
            },
        }


class MilkSummaryReadToolHandler:
    def __init__(self, *, records_service: RecordsService, profile_service: ProfileService) -> None:
        self.records_service = records_service
        self.profile_service = profile_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        days = _limit(context.args.get("days"), default=7, max_limit=30)
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        feedings = await self.records_service.list_feedings(owner_user_id=owner_user_id, limit=limit)
        pumpings = await self.records_service.list_pumpings(owner_user_id=owner_user_id, limit=limit)
        trends = await self.records_service.get_milk_trends(owner_user_id=owner_user_id, days=days, include_today=True)
        infants = await self.profile_service.list_infants(owner_user_id=owner_user_id)
        trend_items = [_milk_trend_payload(item) for item in trends.items]
        return {
            "window": {
                "days": days,
                "include_today": True,
            },
            "infants": [_infant_payload(infant) for infant in infants[:limit]],
            "recent_feedings": [_feeding_payload(record) for record in feedings],
            "recent_pumpings": [_pumping_payload(record) for record in pumpings],
            "pumping_trends": trend_items,
            "totals": {
                "recent_feeding_volume_ml": _record_volume_sum(feedings, "volume_ml"),
                "recent_pumped_volume_ml": _record_volume_sum(pumpings, "milk_volume_ml"),
                "trend_pumped_volume_ml": round(sum(float(item["pumped_milk_volume_ml"] or 0) for item in trend_items), 2),
                "trend_pumping_count": sum(int(item["pumping_count"] or 0) for item in trend_items),
            },
        }


class MilkStatusReadToolHandler:
    def __init__(self, *, records_service: RecordsService, profile_service: ProfileService) -> None:
        self.records_service = records_service
        self.profile_service = profile_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        days = _limit(context.args.get("days"), default=7, max_limit=30)
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        feedings = await self.records_service.list_feedings(owner_user_id=owner_user_id, limit=limit)
        pumpings = await self.records_service.list_pumpings(owner_user_id=owner_user_id, limit=limit)
        trends = await self.records_service.get_milk_trends(owner_user_id=owner_user_id, days=days, include_today=True)
        infants = await self.profile_service.list_infants(owner_user_id=owner_user_id)
        trend_items = [_milk_trend_payload(item) for item in trends.items]
        return _milk_status_payload(
            days=days,
            limit=limit,
            feedings=feedings,
            pumpings=pumpings,
            trend_items=trend_items,
            infant_count=len(infants),
        )


class MilkAnalysisReadToolHandler:
    def __init__(self, *, records_service: RecordsService, profile_service: ProfileService) -> None:
        self.records_service = records_service
        self.profile_service = profile_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        days = _limit(context.args.get("days"), default=7, max_limit=30)
        limit = _limit(context.args.get("limit"), default=8, max_limit=20)
        feedings = await self.records_service.list_feedings(owner_user_id=owner_user_id, limit=limit)
        pumpings = await self.records_service.list_pumpings(owner_user_id=owner_user_id, limit=limit)
        growth = await self.records_service.list_growth(owner_user_id=owner_user_id, limit=limit)
        trends = await self.records_service.get_milk_trends(owner_user_id=owner_user_id, days=days, include_today=True)
        infants = await self.profile_service.list_infants(owner_user_id=owner_user_id)
        trend_items = [_milk_trend_payload(item) for item in trends.items]
        status = _milk_status_payload(
            days=days,
            limit=limit,
            feedings=feedings,
            pumpings=pumpings,
            trend_items=trend_items,
            infant_count=len(infants),
        )
        return {
            "window": status["window"],
            "status": status["status"],
            "counts": status["counts"] | {"recent_growth": len(growth)},
            "volumes": status["volumes"],
            "latest": status["latest"],
            "observation_flags": status["observation_flags"],
            "recent_feedings": [_feeding_payload(record) for record in feedings],
            "recent_pumpings": [_pumping_payload(record) for record in pumpings],
            "recent_growth": [_growth_payload(record) for record in growth],
            "pumping_trends": trend_items,
            "analysis": _milk_analysis_payload(status=status, growth=growth),
        }


class GrowthRecordsReadToolHandler:
    def __init__(self, *, records_service: RecordsService) -> None:
        self.records_service = records_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        infant_id = _optional_uuid_arg(context.args, "infant_id")
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        growth = await self.records_service.list_growth(
            owner_user_id=context.actor.user_id,
            infant_id=infant_id,
            limit=limit,
        )
        return {
            "growth": [_growth_payload(record) for record in growth],
            "count": len(growth),
            "infant_id": str(infant_id) if infant_id is not None else "",
        }


class PlansCurrentReadToolHandler:
    def __init__(self, *, plans_service: PlansService) -> None:
        self.plans_service = plans_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        plans = await self.plans_service.list_plans(owner_user_id=owner_user_id, status="active", limit=limit)
        tasks = await self.plans_service.list_tasks(owner_user_id=owner_user_id, limit=limit)
        return {
            "plans": [_plan_payload(plan) for plan in plans],
            "tasks": [_task_payload(task) for task in tasks],
            "counts": {
                "plans": len(plans),
                "tasks": len(tasks),
            },
        }


class PlansCalendarReadToolHandler:
    def __init__(self, *, plans_service: PlansService) -> None:
        self.plans_service = plans_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        limit = _limit(context.args.get("limit"), default=10, max_limit=50)
        task_date = _optional_date_arg(context.args, "task_date")
        status = _text(context.args, "status") or None
        tasks = await self.plans_service.list_tasks(
            owner_user_id=context.actor.user_id,
            task_date=task_date,
            status=status,
            limit=limit,
        )
        return {
            "tasks": [_task_payload(task) for task in tasks],
            "count": len(tasks),
            "filters": {
                "task_date": _date_iso(task_date),
                "status": status or "",
                "limit": limit,
            },
        }


class DiaryRecentReadToolHandler:
    def __init__(self, *, diary_service: DiaryService) -> None:
        self.diary_service = diary_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        entries = await self.diary_service.list_entries(owner_user_id=owner_user_id, limit=limit)
        return {
            "entries": [_diary_payload(entry) for entry in entries],
            "count": len(entries),
        }


class PregnancyPlanContextReadToolHandler:
    def __init__(
        self,
        *,
        profile_service: ProfileService,
        plans_service: PlansService,
        diary_service: DiaryService,
    ) -> None:
        self.profile_service = profile_service
        self.plans_service = plans_service
        self.diary_service = diary_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        profile = await self.profile_service.get_user_profile(user_id=owner_user_id)
        plans = await self.plans_service.list_plans(owner_user_id=owner_user_id, status="active", limit=limit)
        tasks = await self.plans_service.list_tasks(owner_user_id=owner_user_id, limit=limit)
        diary_entries = await self.diary_service.list_entries(owner_user_id=owner_user_id, limit=limit)
        return {
            "profile": _profile_payload(profile=profile, actor_user_id=owner_user_id),
            "plans": [_plan_payload(plan) for plan in plans],
            "tasks": [_task_payload(task) for task in tasks],
            "recent_diary_entries": [_diary_payload(entry) for entry in diary_entries],
            "counts": {
                "plans": len(plans),
                "tasks": len(tasks),
                "recent_diary_entries": len(diary_entries),
            },
        }


class DiaryEntryUpsertProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _diary_entry_apply_payload(context.args)
        entry_date = _text(apply_payload, "entry_date")
        if not entry_date:
            raise ApiError(code="validation_failed", message="entry_date is required.", status=422)
        values = apply_payload.get("values")
        if not isinstance(values, dict) or not values:
            raise ApiError(code="validation_failed", message="At least one diary field is required.", status=422)
        preview_payload = _diary_entry_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=DIARY_ENTRY_UPSERT_ACTION,
            target_type="pregnancy_diary_entry",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:diary-entry",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class MemoryCreateProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _memory_create_apply_payload(context.args)
        content = apply_payload.get("content")
        if not _text(apply_payload, "memory_type"):
            raise ApiError(code="validation_failed", message="memory_type is required.", status=422)
        if not isinstance(content, dict) or not _text(content, "summary"):
            raise ApiError(code="validation_failed", message="content.summary is required.", status=422)
        apply_payload["content"] = validate_memory_write_policy(content)
        preview_payload = _memory_create_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=AGENT_MEMORY_CREATE_ACTION,
            target_type="agent_memory",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:memory-create",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class DevicesPumpStatusReadToolHandler:
    def __init__(self, *, devices_service: DevicesService) -> None:
        self.devices_service = devices_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        devices = await self.devices_service.list_devices(owner_user_id=owner_user_id)
        telemetry = await self.devices_service.list_telemetry_events(owner_user_id=owner_user_id, limit=limit)
        bounded_devices = devices[:limit]
        return {
            "pumps": [_device_payload(device) for device in bounded_devices],
            "telemetry": [_telemetry_payload(event) for event in telemetry],
            "counts": {
                "pumps": len(bounded_devices),
                "telemetry": len(telemetry),
            },
        }


class DeviceGuidanceAssetsReadToolHandler:
    def __init__(self, *, asset_service: ProductAssetService) -> None:
        self.asset_service = asset_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        limit = _limit(context.args.get("limit"), default=10, max_limit=20)
        content_type = _text(context.args, "content_type")
        model = _text(context.args, "model")
        topic = _text(context.args, "topic")
        query = _text(context.args, "query")
        assets = self.asset_service.list_assets(limit=200)
        if content_type:
            assets = [asset for asset in assets if asset.content_type == content_type]
        assets = _filter_guidance_assets(assets=assets, model=model, topic=topic, query=query)
        bounded_assets = assets[:limit]
        return {
            "assets": [_asset_payload(asset) for asset in bounded_assets],
            "count": len(bounded_assets),
            "available_count": len(assets),
            "query_context": {
                "model": model,
                "topic": topic,
                "query": query,
                "measured_nipple_mm": context.args.get("measured_nipple_mm"),
            },
        }


class FileVisionSummaryReadToolHandler:
    def __init__(self, *, vision_service: FileVisionService) -> None:
        self.vision_service = vision_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        file_id = _uuid(_text(context.args, "file_id"), code="validation_failed", field_name="file_id")
        events = await self.vision_service.events_for_owner(file_id=file_id, owner_user_id=context.actor.user_id)
        safe_events = [
            {
                "type": event.type,
                "sequence": event.sequence,
                "payload": _safe_vision_payload(event.payload),
            }
            for event in events
        ]
        summary = ""
        for event in safe_events:
            event_payload = event.get("payload")
            event_summary = _text(event_payload if isinstance(event_payload, dict) else {}, "summary")
            if event_summary:
                summary = event_summary
                break
        return {
            "file_id": str(file_id),
            "summary": summary,
            "events": safe_events,
            "event_count": len(safe_events),
        }


class FeedingRecordProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _feeding_record_apply_payload(context.args)
        if apply_payload.get("volume_ml") is None and apply_payload.get("duration_seconds") is None:
            raise ApiError(code="validation_failed", message="volume_ml or duration_seconds is required.", status=422)
        preview_payload = _feeding_record_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=FEEDING_RECORD_CREATE_ACTION,
            target_type="feeding_record",
            side_effect_level="low",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:feeding-record",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PumpingRecordProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _pumping_record_apply_payload(context.args)
        if apply_payload.get("milk_volume_ml") is None and apply_payload.get("duration_seconds") is None:
            raise ApiError(code="validation_failed", message="milk_volume_ml or duration_seconds is required.", status=422)
        preview_payload = _pumping_record_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PUMPING_RECORD_CREATE_ACTION,
            target_type="pumping_record",
            side_effect_level="low",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:pumping-record",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class FeedingRecordDeleteProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _record_delete_apply_payload(context.args)
        record_id = _text(apply_payload, "record_id")
        if not record_id:
            raise ApiError(code="validation_failed", message="record_id is required.", status=422)
        preview_payload = _record_delete_preview_payload(apply_payload, record_type="feeding_record")
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=FEEDING_RECORD_DELETE_ACTION,
            target_type="feeding_record",
            target_id=record_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:feeding-record-delete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PumpingRecordDeleteProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _record_delete_apply_payload(context.args)
        record_id = _text(apply_payload, "record_id")
        if not record_id:
            raise ApiError(code="validation_failed", message="record_id is required.", status=422)
        preview_payload = _record_delete_preview_payload(apply_payload, record_type="pumping_record")
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PUMPING_RECORD_DELETE_ACTION,
            target_type="pumping_record",
            target_id=record_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:pumping-record-delete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class GrowthRecordProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _growth_record_apply_payload(context.args)
        if not _text(apply_payload, "measured_at"):
            raise ApiError(code="validation_failed", message="measured_at is required.", status=422)
        if not _has_any_growth_measurement(apply_payload):
            raise ApiError(code="validation_failed", message="height_cm, weight_kg, or head_cm is required.", status=422)
        preview_payload = _growth_record_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=GROWTH_RECORD_CREATE_ACTION,
            target_type="growth_record",
            side_effect_level="low",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:growth-record",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class GrowthRecordUpdateProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _growth_record_update_apply_payload(context.args)
        record_id = _text(apply_payload, "record_id")
        if not record_id:
            raise ApiError(code="validation_failed", message="record_id is required.", status=422)
        if not _growth_update_fields(apply_payload):
            raise ApiError(code="validation_failed", message="At least one growth update field is required.", status=422)
        preview_payload = _growth_record_update_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=GROWTH_RECORD_UPDATE_ACTION,
            target_type="growth_record",
            target_id=record_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:growth-record-update",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class GrowthRecordDeleteProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _record_delete_apply_payload(context.args)
        record_id = _text(apply_payload, "record_id")
        if not record_id:
            raise ApiError(code="validation_failed", message="record_id is required.", status=422)
        preview_payload = _record_delete_preview_payload(apply_payload, record_type="growth_record")
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=GROWTH_RECORD_DELETE_ACTION,
            target_type="growth_record",
            target_id=record_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:growth-record-delete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class MilkPlanProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _milk_plan_apply_payload(context.args)
        title = _text(apply_payload, "title")
        if not title:
            raise ApiError(code="validation_failed", message="title is required.", status=422)
        preview_payload = _milk_plan_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=MILK_PLAN_CREATE_ACTION,
            target_type="plan",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:milk-plan",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class MilkPlanPreviewCreateToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        payload = _milk_plan_preview_artifact_payload(context.args)
        if not _text(payload, "title"):
            raise ApiError(code="validation_failed", message="title is required.", status=422)
        artifact = await self.runtime_service.create_artifact(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            artifact_type="milk_plan_preview",
            schema_version="v1",
            status="created",
            payload=payload,
            emit_event=False,
        )
        return {
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "status": artifact.status,
            "title": _text(payload, "title"),
            "summary": _text(payload, "summary"),
            "task_count": len(payload.get("tasks") if isinstance(payload.get("tasks"), list) else []),
            "reminder_count": len(payload.get("reminders") if isinstance(payload.get("reminders"), list) else []),
            DEFERRED_AGENT_EVENTS_KEY: [_deferred_artifact_created_event(artifact)],
        }


class PregnancyPlanProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _pregnancy_plan_apply_payload(context.args)
        title = _text(apply_payload, "title")
        if not title:
            raise ApiError(code="validation_failed", message="title is required.", status=422)
        preview_payload = _pregnancy_plan_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PREGNANCY_PLAN_CREATE_ACTION,
            target_type="plan",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:pregnancy-plan",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PlanTaskCreateProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _plan_task_create_apply_payload(context.args)
        title = _text(apply_payload, "title")
        if not title:
            raise ApiError(code="validation_failed", message="title is required.", status=422)
        preview_payload = _plan_task_create_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_TASK_CREATE_ACTION,
            target_type="plan_task",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:plan-task-create",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PlanTaskCompleteProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _plan_task_complete_apply_payload(context.args)
        task_id = _text(apply_payload, "task_id")
        if not task_id:
            raise ApiError(code="validation_failed", message="task_id is required.", status=422)
        preview_payload = _plan_task_complete_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_TASK_COMPLETE_ACTION,
            target_type="plan_task",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:plan-task-complete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PlanTaskUpdateProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _plan_task_update_apply_payload(context.args)
        task_id = _text(apply_payload, "task_id")
        if not task_id:
            raise ApiError(code="validation_failed", message="task_id is required.", status=422)
        if not _plan_task_update_fields(apply_payload):
            raise ApiError(code="validation_failed", message="At least one task update field is required.", status=422)
        preview_payload = _plan_task_update_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_TASK_UPDATE_ACTION,
            target_type="plan_task",
            target_id=task_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:plan-task-update",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PlanTaskDeleteProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _plan_task_delete_apply_payload(context.args)
        task_id = _text(apply_payload, "task_id")
        if not task_id:
            raise ApiError(code="validation_failed", message="task_id is required.", status=422)
        preview_payload = _plan_task_delete_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_TASK_DELETE_ACTION,
            target_type="plan_task",
            target_id=task_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:plan-task-delete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class PlanDeleteProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _plan_delete_apply_payload(context.args)
        plan_id = _text(apply_payload, "plan_id")
        if not plan_id:
            raise ApiError(code="validation_failed", message="plan_id is required.", status=422)
        preview_payload = _plan_delete_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_DELETE_ACTION,
            target_type="plan",
            target_id=plan_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:plan-delete",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


class MilkReminderProposeToolHandler:
    def __init__(self, *, runtime_service: AgentRuntimeService) -> None:
        self.runtime_service = runtime_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        apply_payload = _milk_reminder_apply_payload(context.args)
        title = _text(apply_payload, "title")
        if not title:
            raise ApiError(code="validation_failed", message="title is required.", status=422)
        preview_payload = _milk_reminder_preview_payload(apply_payload)
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=MILK_REMINDER_CREATE_ACTION,
            target_type="notification",
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key") or f"{context.run_id}:{context.call_id}:milk-reminder",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


def build_default_tool_handlers(
    *,
    profile_service: ProfileService,
    records_service: RecordsService,
    plans_service: PlansService,
    diary_service: DiaryService,
    devices_service: DevicesService,
    asset_service: ProductAssetService,
    file_vision_service: FileVisionService,
    agent_runtime_service: AgentRuntimeService,
) -> dict[str, ToolHandler]:
    return {
        "profile.read": ProfileReadToolHandler(service=profile_service),
        "profile_update": ProfileUpdateToolHandler(service=profile_service),
        "business.context.read": BusinessContextReadToolHandler(
            records_service=records_service,
            plans_service=plans_service,
            diary_service=diary_service,
            devices_service=devices_service,
        ),
        "records.milk_summary.read": MilkSummaryReadToolHandler(
            records_service=records_service,
            profile_service=profile_service,
        ),
        "records.milk_status.read": MilkStatusReadToolHandler(
            records_service=records_service,
            profile_service=profile_service,
        ),
        "records.milk_analysis.read": MilkAnalysisReadToolHandler(
            records_service=records_service,
            profile_service=profile_service,
        ),
        "records.growth.read": GrowthRecordsReadToolHandler(records_service=records_service),
        "plans.current.read": PlansCurrentReadToolHandler(plans_service=plans_service),
        "plans.calendar.read": PlansCalendarReadToolHandler(plans_service=plans_service),
        "diary.recent.read": DiaryRecentReadToolHandler(diary_service=diary_service),
        "pregnancy.plan_context.read": PregnancyPlanContextReadToolHandler(
            profile_service=profile_service,
            plans_service=plans_service,
            diary_service=diary_service,
        ),
        "diary.entry_upsert.propose": DiaryEntryUpsertProposeToolHandler(runtime_service=agent_runtime_service),
        "memory.create.propose": MemoryCreateProposeToolHandler(runtime_service=agent_runtime_service),
        "devices.pump_status.read": DevicesPumpStatusReadToolHandler(devices_service=devices_service),
        "devices.guidance_assets.read": DeviceGuidanceAssetsReadToolHandler(asset_service=asset_service),
        "files.vision_summary.read": FileVisionSummaryReadToolHandler(vision_service=file_vision_service),
        "plans.milk_plan.propose": MilkPlanProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.milk_plan_preview.create": MilkPlanPreviewCreateToolHandler(runtime_service=agent_runtime_service),
        "pregnancy.plan_create.propose": PregnancyPlanProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.task_create.propose": PlanTaskCreateProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.task_complete.propose": PlanTaskCompleteProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.task_update.propose": PlanTaskUpdateProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.task_delete.propose": PlanTaskDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "plans.plan_delete.propose": PlanDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "notifications.milk_reminder.propose": MilkReminderProposeToolHandler(runtime_service=agent_runtime_service),
        "records.feeding_record.propose": FeedingRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "records.pumping_record.propose": PumpingRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "records.feeding_record_delete.propose": FeedingRecordDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "records.pumping_record_delete.propose": PumpingRecordDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "records.growth_record.propose": GrowthRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "records.growth_record_update.propose": GrowthRecordUpdateProposeToolHandler(runtime_service=agent_runtime_service),
        "records.growth_record_delete.propose": GrowthRecordDeleteProposeToolHandler(runtime_service=agent_runtime_service),
        "birth_plan_form_create": LegacyArtifactToolHandler(runtime_service=agent_runtime_service, tool_name="birth_plan_form_create"),
        "labor_communication_card_create": LegacyArtifactToolHandler(runtime_service=agent_runtime_service, tool_name="labor_communication_card_create"),
        "birth_journey_plan_card_create": LegacyArtifactToolHandler(runtime_service=agent_runtime_service, tool_name="birth_journey_plan_card_create"),
        "hospital_bag_form_create": LegacyArtifactToolHandler(runtime_service=agent_runtime_service, tool_name="hospital_bag_form_create"),
        "hospital_bag_card_create": LegacyArtifactToolHandler(runtime_service=agent_runtime_service, tool_name="hospital_bag_card_create"),
        "hospital_bag_cart_update": LegacyArtifactToolHandler(runtime_service=agent_runtime_service, tool_name="hospital_bag_cart_update"),
        "hospital_bag_pump_recommend": LegacyArtifactToolHandler(runtime_service=agent_runtime_service, tool_name="hospital_bag_pump_recommend"),
        "ibclc_consult_card_create": IbclcConsultCardCreateToolHandler(runtime_service=agent_runtime_service),
        "support.ticket.propose": SupportTicketProposeToolHandler(runtime_service=agent_runtime_service),
    }


def _profile_payload(*, profile: UserProfile | None, actor_user_id: UUID) -> dict[str, Any]:
    if profile is None:
        return {
            "user_id": str(actor_user_id),
            "display_name": "",
            "age": None,
            "delivery_date": None,
            "lactation_advice": "",
            "feeding_advice": "",
            "profile_onboarding_complete": False,
            "profile_onboarding_skipped": False,
        }
    return {
        "user_id": str(actor_user_id),
        "display_name": profile.display_name or "",
        "age": profile.age,
        "delivery_date": _date_iso(profile.delivery_date),
        "lactation_advice": profile.lactation_advice or "",
        "feeding_advice": profile.feeding_advice or "",
        "profile_onboarding_complete": bool(profile.profile_onboarding_completed_at or (profile.display_name and profile.age)),
        "profile_onboarding_skipped": bool(profile.profile_onboarding_skipped_at),
    }


def _infant_payload(infant: InfantProfile) -> dict[str, Any]:
    return {
        "id": str(infant.id),
        "owner_user_id": str(infant.owner_user_id),
        "infant_name": infant.infant_name,
        "sex": infant.sex,
        "birth_date": _date_iso(infant.birth_date),
        "status": infant.status,
    }


def _profile_update_values(args: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "display_name" in args:
        display_name = _text(args, "display_name")
        if not display_name:
            raise ApiError(code="validation_failed", message="display_name is required when provided.", status=422)
        values["display_name"] = display_name
    if "age" in args:
        values["age"] = args["age"]
    if "onboarding_skipped" in args:
        values["profile_onboarding_skipped_at"] = datetime.now(timezone.utc) if args["onboarding_skipped"] else None
    return values


def _support_ticket_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "issue_type": _text(args, "issue_type") or "other",
        "issue_summary": _text(args, "issue_summary"),
        "product_model": _text(args, "product_model"),
        "order_number": _text(args, "order_number"),
        "purchase_channel": _text(args, "purchase_channel"),
        "user_contact": _text(args, "user_contact"),
        "urgency": _text(args, "urgency") or "normal",
    }
    extra_payload = args.get("payload")
    if isinstance(extra_payload, dict):
        payload["payload"] = extra_payload
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _support_ticket_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "issue_type": _text(apply_payload, "issue_type") or "other",
        "issue_summary": _text(apply_payload, "issue_summary"),
        "product_model": _text(apply_payload, "product_model"),
        "order_number": _text(apply_payload, "order_number"),
        "purchase_channel": _text(apply_payload, "purchase_channel"),
        "urgency": _text(apply_payload, "urgency") or "normal",
        "has_user_contact": bool(_text(apply_payload, "user_contact")),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _hospital_bag_cart_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    cart_update = args.get("cart_update")
    payload: dict[str, Any] = {"cart_update": cart_update if isinstance(cart_update, dict) else {}}
    summary = _text(args, "summary")
    if summary:
        payload["summary"] = summary
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return payload


def _hospital_bag_cart_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    cart_update = apply_payload.get("cart_update")
    preview = {
        "summary": _text(apply_payload, "summary") or "Update hospital bag cart",
        "cart_update": cart_update if isinstance(cart_update, dict) else {},
    }
    return {key: value for key, value in preview.items() if value not in ("", None, {})}


def _artifact_payload(*, args: dict[str, Any], default_title: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    extra_payload = args.get("payload")
    if isinstance(extra_payload, dict):
        payload.update(extra_payload)
    payload["title"] = _text(args, "title") or default_title
    payload["summary"] = _text(args, "summary")
    sections = args.get("sections")
    if isinstance(sections, list):
        payload["sections"] = sections
    source_context = args.get("source_context")
    if isinstance(source_context, dict):
        payload["source_context"] = source_context
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, [], {})}


def _ibclc_consult_card_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "IBCLC 咨询入口",
        "reason": _text(args, "reason"),
        "feeding_context": _text(args, "feeding_context"),
        "urgency": _text(args, "urgency") or "routine",
        "preferred_language": _text(args, "preferred_language"),
    }
    extra_payload = args.get("payload")
    if isinstance(extra_payload, dict):
        payload["payload"] = extra_payload
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _feeding_record_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "infant_id": _text(args, "infant_id"),
        "feed_time": _text(args, "feed_time"),
        "feed_type": _text(args, "feed_type"),
        "feed_action": _text(args, "feed_action"),
        "volume_ml": _optional_number(args, "volume_ml"),
        "duration_seconds": _optional_int(args, "duration_seconds"),
        "title": _text(args, "title"),
    }
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _feeding_record_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "feed_time": _text(apply_payload, "feed_time"),
        "feed_type": _text(apply_payload, "feed_type"),
        "feed_action": _text(apply_payload, "feed_action"),
        "volume_ml": apply_payload.get("volume_ml"),
        "duration_seconds": apply_payload.get("duration_seconds"),
        "title": _text(apply_payload, "title"),
        "has_infant_id": bool(_text(apply_payload, "infant_id")),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _pumping_record_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "pump_start_time": _text(args, "pump_start_time"),
        "pump_end_time": _text(args, "pump_end_time"),
        "milk_volume_ml": _optional_number(args, "milk_volume_ml"),
        "pump_type": _text(args, "pump_type"),
        "duration_seconds": _optional_int(args, "duration_seconds"),
        "source": _text(args, "source") or "agent",
        "title": _text(args, "title"),
    }
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _pumping_record_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "pump_start_time": _text(apply_payload, "pump_start_time"),
        "pump_end_time": _text(apply_payload, "pump_end_time"),
        "milk_volume_ml": apply_payload.get("milk_volume_ml"),
        "pump_type": _text(apply_payload, "pump_type"),
        "duration_seconds": apply_payload.get("duration_seconds"),
        "source": _text(apply_payload, "source"),
        "title": _text(apply_payload, "title"),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _record_delete_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "record_id": _text(args, "record_id"),
        "reason": _text(args, "reason"),
    }
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _record_delete_preview_payload(apply_payload: dict[str, Any], *, record_type: str) -> dict[str, Any]:
    preview = {
        "record_type": record_type,
        "record_id": _text(apply_payload, "record_id"),
        "reason": _text(apply_payload, "reason"),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _growth_record_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "infant_id": _text(args, "infant_id"),
        "measured_at": _text(args, "measured_at"),
        "height_cm": _optional_number(args, "height_cm"),
        "weight_kg": _optional_number(args, "weight_kg"),
        "head_cm": _optional_number(args, "head_cm"),
    }
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _growth_record_update_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload = _growth_record_apply_payload(args)
    payload["record_id"] = _text(args, "record_id")
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _growth_record_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "measured_at": _text(apply_payload, "measured_at"),
        "height_cm": apply_payload.get("height_cm"),
        "weight_kg": apply_payload.get("weight_kg"),
        "head_cm": apply_payload.get("head_cm"),
        "has_infant_id": bool(_text(apply_payload, "infant_id")),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _growth_record_update_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = _growth_record_preview_payload(apply_payload)
    preview["record_id"] = _text(apply_payload, "record_id")
    preview["fields"] = _growth_update_fields(apply_payload)
    return {key: value for key, value in preview.items() if value not in ("", None, [])}


def _has_any_growth_measurement(payload: dict[str, Any]) -> bool:
    return any(payload.get(key) is not None for key in ("height_cm", "weight_kg", "head_cm"))


def _growth_update_fields(payload: dict[str, Any]) -> list[str]:
    return sorted(key for key in ("infant_id", "measured_at", "height_cm", "weight_kg", "head_cm") if key in payload)


def _milk_plan_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": _text(args, "title"),
        "summary": _text(args, "summary"),
    }
    plan_payload = args.get("payload")
    if isinstance(plan_payload, dict):
        payload["payload"] = plan_payload
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _milk_plan_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "plan_type": "milk_management",
        "title": _text(apply_payload, "title"),
        "summary": _text(apply_payload, "summary"),
        "has_payload": isinstance(apply_payload.get("payload"), dict) and bool(apply_payload.get("payload")),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _milk_plan_preview_artifact_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": _text(args, "title"),
        "summary": _text(args, "summary"),
        "direction": _text(args, "direction") or "unknown",
        "start_date": _text(args, "start_date"),
        "days": _optional_int(args, "days"),
    }
    for key in ("tasks", "reminders"):
        value = args.get(key)
        if isinstance(value, list):
            payload[key] = value
    extra_payload = args.get("payload")
    if isinstance(extra_payload, dict):
        payload["payload"] = extra_payload
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, [], {})}


def _pregnancy_plan_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": _text(args, "title"),
        "summary": _text(args, "summary"),
    }
    plan_payload = args.get("payload")
    if isinstance(plan_payload, dict):
        payload["payload"] = plan_payload
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _pregnancy_plan_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "plan_type": "pregnancy",
        "title": _text(apply_payload, "title"),
        "summary": _text(apply_payload, "summary"),
        "has_payload": isinstance(apply_payload.get("payload"), dict) and bool(apply_payload.get("payload")),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _plan_task_create_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "plan_id": _text(args, "plan_id"),
        "task_date": _text(args, "task_date"),
        "task_time": _text(args, "task_time"),
        "title": _text(args, "title"),
        "description": _text(args, "description"),
    }
    task_payload = args.get("payload")
    if isinstance(task_payload, dict):
        payload["payload"] = task_payload
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _plan_task_create_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "task_date": _text(apply_payload, "task_date"),
        "task_time": _text(apply_payload, "task_time"),
        "title": _text(apply_payload, "title"),
        "description": _truncate(_text(apply_payload, "description"), max_length=240),
        "has_plan_id": bool(_text(apply_payload, "plan_id")),
        "has_payload": isinstance(apply_payload.get("payload"), dict) and bool(apply_payload.get("payload")),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _plan_task_complete_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": _text(args, "task_id"),
        "completed": args.get("completed", True),
    }
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _plan_task_complete_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": _text(apply_payload, "task_id"),
        "completed": bool(apply_payload.get("completed", True)),
    }


def _plan_task_update_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": _text(args, "task_id"),
        "plan_id": _text(args, "plan_id"),
        "task_date": _text(args, "task_date"),
        "task_time": _text(args, "task_time"),
        "title": _text(args, "title"),
        "description": _text(args, "description"),
    }
    task_payload = args.get("payload")
    if isinstance(task_payload, dict):
        payload["payload"] = task_payload
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _plan_task_update_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "task_id": _text(apply_payload, "task_id"),
        "task_date": _text(apply_payload, "task_date"),
        "task_time": _text(apply_payload, "task_time"),
        "title": _text(apply_payload, "title"),
        "description": _truncate(_text(apply_payload, "description"), max_length=240),
        "has_plan_id": bool(_text(apply_payload, "plan_id")),
        "has_payload": isinstance(apply_payload.get("payload"), dict) and bool(apply_payload.get("payload")),
        "fields": _plan_task_update_fields(apply_payload),
    }
    return {key: value for key, value in preview.items() if value not in ("", None, [])}


def _plan_task_update_fields(payload: dict[str, Any]) -> list[str]:
    return sorted(key for key in ("plan_id", "task_date", "task_time", "title", "description", "payload") if key in payload)


def _plan_task_delete_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": _text(args, "task_id"),
        "reason": _text(args, "reason"),
    }
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _plan_task_delete_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "task_id": _text(apply_payload, "task_id"),
        "reason": _text(apply_payload, "reason"),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _plan_delete_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "plan_id": _text(args, "plan_id"),
        "reason": _text(args, "reason"),
    }
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _plan_delete_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "plan_id": _text(apply_payload, "plan_id"),
        "reason": _text(apply_payload, "reason"),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _milk_reminder_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": _text(args, "title"),
        "body": _text(args, "body"),
        "remind_at": _text(args, "remind_at"),
    }
    reminder_payload = args.get("payload")
    if isinstance(reminder_payload, dict):
        payload["payload"] = reminder_payload
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _milk_reminder_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "notification_type": "milk_reminder",
        "title": _text(apply_payload, "title"),
        "body": _text(apply_payload, "body"),
        "remind_at": _text(apply_payload, "remind_at"),
        "has_payload": isinstance(apply_payload.get("payload"), dict) and bool(apply_payload.get("payload")),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _diary_entry_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    values = {key: args[key] for key in _DIARY_ENTRY_VALUE_FIELDS if key in args and args[key] is not None}
    payload: dict[str, Any] = {
        "entry_date": _text(args, "entry_date"),
    }
    if values:
        payload["values"] = values
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _diary_entry_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    values = apply_payload.get("values")
    if not isinstance(values, dict):
        values = {}
    preview: dict[str, Any] = {
        "entry_date": _text(apply_payload, "entry_date"),
        "fields": sorted(values),
        "gestational_week": _text(values, "gestational_week"),
        "mood": _text(values, "mood"),
        "energy_level": _text(values, "energy_level"),
    }
    symptom_tags = values.get("symptom_tags")
    if isinstance(symptom_tags, list):
        preview["symptom_tags"] = symptom_tags
    content = _text(values, "content")
    if content:
        preview["content_summary"] = _truncate(content, max_length=240)
    attachments = values.get("attachments")
    if isinstance(attachments, list):
        preview["attachment_count"] = len(attachments)
    return {key: value for key, value in preview.items() if value not in ("", None, [], {})}


def _memory_create_apply_payload(args: dict[str, Any]) -> dict[str, Any]:
    raw_content = args.get("content")
    content: dict[str, Any] = dict(raw_content) if isinstance(raw_content, dict) else {}
    sensitivity = _text(args, "sensitivity")
    if sensitivity:
        content = dict(content)
        content["sensitivity"] = sensitivity
    payload: dict[str, Any] = {
        "memory_type": _text(args, "memory_type"),
        "content": content,
        "confidence_score": _optional_int(args, "confidence_score") or 0,
    }
    expires_in_days = _optional_int(args, "expires_in_days")
    if expires_in_days is not None:
        payload["expires_in_days"] = expires_in_days
    metadata = _metadata_payload(args)
    if metadata:
        payload["metadata"] = metadata
    return {key: value for key, value in payload.items() if value not in ("", None, {})}


def _memory_create_preview_payload(apply_payload: dict[str, Any]) -> dict[str, Any]:
    content = apply_payload.get("content")
    if not isinstance(content, dict):
        content = {}
    preview = {
        "memory_type": _text(apply_payload, "memory_type"),
        "summary": _truncate(_text(content, "summary"), max_length=240),
        "sensitivity": _text(content, "sensitivity") or "normal",
        "confidence_score": apply_payload.get("confidence_score", 0),
        "expires_in_days": apply_payload.get("expires_in_days"),
    }
    return {key: value for key, value in preview.items() if value not in ("", None)}


def _proposal_result(*, action: Any, preview_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_id": str(action.id),
        "action_type": action.action_type,
        "action_status": action.status,
        "requires_confirmation": _action_requires_confirmation(action),
        "preview_payload": preview_payload,
    }


def _action_requires_confirmation(action: Any) -> bool:
    return str(getattr(action, "status", "") or "") == "confirmation_required"


def _metadata_payload(payload: dict[str, Any]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key in ("thread_id", "locale", "timezone"):
        value = _text(payload, key)
        if value:
            metadata[key] = value
    return metadata


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _uuid(value: str, *, code: str, field_name: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ApiError(
            code=code,
            message=f"{field_name} must be a valid UUID.",
            status=422,
            details={"field": field_name},
        ) from exc


def _optional_uuid_arg(payload: dict[str, Any], key: str) -> UUID | None:
    value = _text(payload, key)
    if not value:
        return None
    return _uuid(value, code="validation_failed", field_name=key)


def _optional_date_arg(payload: dict[str, Any], key: str) -> date | None:
    value = _text(payload, key)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(
            code="validation_failed",
            message=f"{key} must be a valid date.",
            status=422,
            details={"field": key},
        ) from exc


def _safe_vision_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in ("content_type", "original_filename", "size_bytes", "provider", "summary", "event_count"):
        value = payload.get(key)
        if value not in ("", None):
            safe[key] = value
    return safe


def _optional_number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None


def _date_iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _feeding_payload(record: FeedingRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "infant_id": str(record.infant_id) if record.infant_id else None,
        "feed_time": _datetime_iso(record.feed_time),
        "feed_type": record.feed_type,
        "feed_action": record.feed_action,
        "volume_ml": record.volume_ml,
        "duration_seconds": record.duration_seconds,
        "title": record.title,
    }


def _pumping_payload(record: PumpingRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "pump_start_time": _datetime_iso(record.pump_start_time),
        "pump_end_time": _datetime_iso(record.pump_end_time),
        "milk_volume_ml": record.milk_volume_ml,
        "duration_seconds": record.duration_seconds,
        "pump_type": record.pump_type,
        "source": record.source,
        "title": record.title,
    }


def _milk_trend_payload(item: Any) -> dict[str, Any]:
    return {
        "date": _date_iso(item.date),
        "pumped_milk_volume_ml": item.pumped_milk_volume_ml,
        "pumping_count": item.pumping_count,
        "measured_only": item.measured_only,
    }


def _record_volume_sum(records: list[Any], attr_name: str) -> float:
    return round(sum(float(getattr(record, attr_name, 0) or 0) for record in records), 2)


def _milk_status_payload(
    *,
    days: int,
    limit: int,
    feedings: list[FeedingRecord],
    pumpings: list[PumpingRecord],
    trend_items: list[dict[str, Any]],
    infant_count: int,
) -> dict[str, Any]:
    trend_pumped_volume = round(sum(float(item.get("pumped_milk_volume_ml") or 0) for item in trend_items), 2)
    trend_pumping_count = sum(int(item.get("pumping_count") or 0) for item in trend_items)
    days_with_pumping = sum(1 for item in trend_items if int(item.get("pumping_count") or 0) > 0)
    latest_feeding = feedings[0] if feedings else None
    latest_pumping = pumpings[0] if pumpings else None
    flags = _milk_observation_flags(
        feedings=feedings,
        pumpings=pumpings,
        trend_pumping_count=trend_pumping_count,
        infant_count=infant_count,
    )
    return {
        "window": {
            "days": days,
            "limit": limit,
            "include_today": True,
        },
        "status": {
            "data_coverage": _milk_data_coverage(
                has_feedings=bool(feedings),
                has_pumpings=bool(pumpings),
                days=days,
                days_with_pumping=days_with_pumping,
            ),
            "pumping_trend": _milk_trend_direction(trend_items),
            "measured_only": True,
        },
        "counts": {
            "infants": infant_count,
            "recent_feedings": len(feedings),
            "recent_pumpings": len(pumpings),
            "trend_days": len(trend_items),
            "days_with_pumping": days_with_pumping,
            "trend_pumping_count": trend_pumping_count,
        },
        "volumes": {
            "recent_feeding_volume_ml": _record_volume_sum(feedings, "volume_ml"),
            "recent_pumped_volume_ml": _record_volume_sum(pumpings, "milk_volume_ml"),
            "trend_pumped_volume_ml": trend_pumped_volume,
            "average_daily_pumped_volume_ml": round(trend_pumped_volume / days, 2) if days else 0.0,
        },
        "latest": {
            "feeding_at": _datetime_iso(latest_feeding.feed_time) if latest_feeding is not None else None,
            "pumping_at": _datetime_iso(latest_pumping.pump_start_time) if latest_pumping is not None else None,
        },
        "observation_flags": flags,
    }


def _milk_data_coverage(*, has_feedings: bool, has_pumpings: bool, days: int, days_with_pumping: int) -> str:
    if not has_feedings and not has_pumpings and days_with_pumping == 0:
        return "no_recent_data"
    if has_feedings and has_pumpings and days_with_pumping >= min(days, 2):
        return "ready"
    return "limited"


def _milk_trend_direction(trend_items: list[dict[str, Any]]) -> str:
    volumes = [float(item.get("pumped_milk_volume_ml") or 0) for item in trend_items if int(item.get("pumping_count") or 0) > 0]
    if len(volumes) < 2:
        return "insufficient_data"
    delta = volumes[-1] - volumes[0]
    if abs(delta) < 30:
        return "stable"
    return "increasing" if delta > 0 else "decreasing"


def _milk_observation_flags(
    *,
    feedings: list[FeedingRecord],
    pumpings: list[PumpingRecord],
    trend_pumping_count: int,
    infant_count: int,
) -> list[str]:
    flags: list[str] = []
    if infant_count == 0:
        flags.append("no_infant_profile")
    if not feedings:
        flags.append("no_recent_feeding_records")
    if not pumpings:
        flags.append("no_recent_pumping_records")
    if trend_pumping_count == 0:
        flags.append("no_pumping_trend_data")
    return flags


def _milk_analysis_payload(*, status: dict[str, Any], growth: list[GrowthRecord]) -> dict[str, Any]:
    status_payload = status.get("status") if isinstance(status.get("status"), dict) else {}
    flags = status.get("observation_flags") if isinstance(status.get("observation_flags"), list) else []
    data_coverage = _text(status_payload, "data_coverage")
    trend = _text(status_payload, "pumping_trend")
    if "no_infant_profile" in flags:
        pathway = "补充宝宝资料后再判断供需"
    elif data_coverage == "no_recent_data":
        pathway = "先补近期记录"
    elif trend == "decreasing":
        pathway = "评估是否需要追奶或排乳节奏调整"
    elif trend == "increasing":
        pathway = "观察是否需要稳奶或减奶"
    elif data_coverage == "ready":
        pathway = "可以进入追奶/稳奶/减奶方向判断"
    else:
        pathway = "继续补齐关键记录后再判断"
    return {
        "pathway": pathway,
        "data_coverage": data_coverage,
        "pumping_trend": trend,
        "has_recent_growth": bool(growth),
        "missing_inputs": list(flags),
        "recommended_next_step": _milk_analysis_next_step(data_coverage=data_coverage, trend=trend, flags=flags),
    }


def _milk_analysis_next_step(*, data_coverage: str, trend: str, flags: list[Any]) -> str:
    if "no_infant_profile" in flags:
        return "先确认宝宝资料或体重/尿布等摄入信号。"
    if data_coverage == "no_recent_data":
        return "先补一条近期喂养或吸奶记录。"
    if data_coverage == "limited":
        return "只追问当前最影响判断的一项缺失信息。"
    if trend in {"decreasing", "increasing"}:
        return "结合宝宝状态和妈妈乳房/全身状态判断是否进入计划。"
    return "给出简短结论，并询问是否开始制定计划。"


def _growth_payload(record: GrowthRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "infant_id": str(record.infant_id) if record.infant_id else None,
        "measured_at": _datetime_iso(record.measured_at),
        "height_cm": record.height_cm,
        "weight_kg": record.weight_kg,
        "head_cm": record.head_cm,
    }


def _plan_payload(plan: Plan) -> dict[str, Any]:
    return {
        "id": str(plan.id),
        "plan_type": plan.plan_type,
        "title": plan.title,
        "summary": _truncate(plan.summary),
        "status": plan.status,
        "source": plan.source,
        "updated_at": _datetime_iso(plan.updated_at),
    }


def _task_payload(task: PlanTask) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "plan_id": str(task.plan_id) if task.plan_id else None,
        "task_date": _date_iso(task.task_date),
        "task_time": task.task_time,
        "title": task.title,
        "status": task.status,
        "completed_at": _datetime_iso(task.completed_at),
    }


def _diary_payload(entry: PregnancyDiaryEntry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "entry_date": _date_iso(entry.entry_date),
        "gestational_week": entry.gestational_week,
        "mood": entry.mood,
        "energy_level": entry.energy_level,
        "symptom_tags": entry.symptom_tags,
        "content_summary": _truncate(entry.content),
    }


def _device_payload(device: PumpDevice) -> dict[str, Any]:
    return {
        "id": str(device.id),
        "device_id": device.device_id,
        "model": device.model,
        "firmware_version": device.firmware_version,
        "status": device.status,
        "last_seen_at": _datetime_iso(device.last_seen_at),
    }


def _asset_payload(asset: ProductAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "label": asset.label,
        "domain": asset.domain,
        "content_type": asset.content_type,
        "size_bytes": asset.size_bytes,
    }


def _filter_guidance_assets(*, assets: list[ProductAsset], model: str, topic: str, query: str) -> list[ProductAsset]:
    term_groups = _guidance_search_term_groups(model=model, topic=topic, query=query)
    if not term_groups:
        return assets
    matched: list[ProductAsset] = []
    for asset in assets:
        haystack = _normalized_search_term(" ".join([asset.id, asset.label, asset.domain, asset.object_key or ""]))
        if all(any(term in haystack for term in group) for group in term_groups):
            matched.append(asset)
    return matched


def _normalized_search_term(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("-", "")


def _normalized_search_terms(value: object) -> list[str]:
    raw_value = str(value or "").strip().lower()
    if not raw_value:
        return []
    raw_terms = re.findall(r"[\w\u4e00-\u9fff]+", raw_value)
    return [
        term
        for term in (_normalized_search_term(raw_term) for raw_term in raw_terms)
        if term
    ]


def _guidance_search_term_groups(*, model: str, topic: str, query: str) -> list[list[str]]:
    groups: list[list[str]] = []
    model_terms = _normalized_search_terms(model)
    if model_terms:
        groups.append(_expand_guidance_terms(model_terms, aliases=_GUIDANCE_MODEL_ALIASES))
    topic_terms = _normalized_search_terms(topic)
    if topic_terms:
        groups.append(_expand_guidance_terms(topic_terms, aliases=_GUIDANCE_TOPIC_ALIASES))
    groups.extend([term] for term in _normalized_search_terms(query))
    return groups


def _expand_guidance_terms(terms: list[str], *, aliases: dict[str, tuple[str, ...]]) -> list[str]:
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        expanded.extend(aliases.get(term, ()))
    return sorted(set(expanded))


_GUIDANCE_MODEL_ALIASES: dict[str, tuple[str, ...]] = {
    "bp334": ("air1",),
}
_GUIDANCE_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "setup": ("unboxing", "assembly", "quickstart", "quick", "start", "components"),
    "firstuse": ("unboxing", "assembly", "quickstart", "quick", "start", "components"),
    "gettingstarted": ("unboxing", "assembly", "quickstart", "quick", "start", "components"),
    "cleaning": ("clean", "cleanable", "washable", "disinfection", "disinfect"),
    "clean": ("cleaning", "cleanable", "washable", "disinfection", "disinfect"),
    "flange": ("nipple", "measurement", "size"),
    "sizing": ("flange", "nipple", "measurement", "size"),
    "bluetooth": ("pairing", "connection", "appcontrol"),
    "pairing": ("bluetooth", "connection", "appcontrol"),
}


def _telemetry_payload(event: PumpTelemetryEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "device_id": event.device_id,
        "event_type": event.event_type,
        "occurred_at": _datetime_iso(event.occurred_at),
        "payload": event.payload,
    }


def _deferred_artifact_created_event(artifact: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_id": str(artifact.id),
        "artifact_type": artifact.artifact_type,
        "schema_version": artifact.schema_version,
        "status": artifact.status,
        "artifact": {
            "id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "schema_version": artifact.schema_version,
            "status": artifact.status,
            "payload": artifact.payload,
            "raw_payload_ref": artifact.raw_payload_ref,
        },
    }
    if isinstance(artifact.payload, dict):
        payload.update(
            {
                key: value
                for key, value in artifact.payload.items()
                if key in {"form", "card", "card_json", "cart_update", "summary"}
            }
        )
    return {"event_type": "artifact.created", "payload": payload}


def _limit(value: Any, *, default: int, max_limit: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, max_limit))


def _truncate(value: str, *, max_length: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + "..."
