from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from ....core.errors import ApiError
from ...devices.models import PumpDevice, PumpTelemetryEvent
from ...devices.service import DevicesService
from ...diary.models import PregnancyDiaryEntry
from ...diary.service import DiaryService
from ...plans.models import Plan, PlanTask
from ...plans.service import PlansService
from ...profiles.models import InfantProfile, UserProfile
from ...profiles.service import ProfileService
from ...records.models import FeedingRecord, GrowthRecord, PumpingRecord
from ...records.agent_actions import FEEDING_RECORD_CREATE_ACTION, PUMPING_RECORD_CREATE_ACTION
from ...records.service import RecordsService
from ...hospital_bag import HOSPITAL_BAG_CART_UPDATE_ACTION
from ...support.agent_actions import SUPPORT_TICKET_CREATE_ACTION
from ..service import AgentRuntimeService
from .executor import ToolHandler, ToolHandlerContext


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
            "requires_confirmation": True,
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
            "requires_confirmation": True,
            "preview_payload": preview_payload,
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
    def __init__(self, *, records_service: RecordsService) -> None:
        self.records_service = records_service

    async def __call__(self, context: ToolHandlerContext) -> dict[str, Any]:
        owner_user_id = context.actor.user_id
        days = _limit(context.args.get("days"), default=7, max_limit=30)
        limit = _limit(context.args.get("limit"), default=5, max_limit=20)
        feedings = await self.records_service.list_feedings(owner_user_id=owner_user_id, limit=limit)
        pumpings = await self.records_service.list_pumpings(owner_user_id=owner_user_id, limit=limit)
        trends = await self.records_service.get_milk_trends(owner_user_id=owner_user_id, days=days, include_today=True)
        trend_items = [_milk_trend_payload(item) for item in trends.items]
        return {
            "window": {
                "days": days,
                "include_today": True,
            },
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


def build_default_tool_handlers(
    *,
    profile_service: ProfileService,
    records_service: RecordsService,
    plans_service: PlansService,
    diary_service: DiaryService,
    devices_service: DevicesService,
    agent_runtime_service: AgentRuntimeService,
) -> dict[str, ToolHandler]:
    return {
        "profile.read": ProfileReadToolHandler(service=profile_service),
        "business.context.read": BusinessContextReadToolHandler(
            records_service=records_service,
            plans_service=plans_service,
            diary_service=diary_service,
            devices_service=devices_service,
        ),
        "records.milk_summary.read": MilkSummaryReadToolHandler(records_service=records_service),
        "records.feeding_record.propose": FeedingRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "records.pumping_record.propose": PumpingRecordProposeToolHandler(runtime_service=agent_runtime_service),
        "hospital_bag.cart_update.propose": HospitalBagCartUpdateProposeToolHandler(runtime_service=agent_runtime_service),
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
            "daily_summary": "",
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
        "daily_summary": profile.daily_summary or "",
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


def _proposal_result(*, action: Any, preview_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_id": str(action.id),
        "action_type": action.action_type,
        "action_status": action.status,
        "requires_confirmation": True,
        "preview_payload": preview_payload,
    }


def _metadata_payload(payload: dict[str, Any]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key in ("thread_id", "locale", "timezone"):
        value = _text(payload, key)
        if value:
            metadata[key] = value
    return metadata


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


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


def _telemetry_payload(event: PumpTelemetryEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "device_id": event.device_id,
        "event_type": event.event_type,
        "occurred_at": _datetime_iso(event.occurred_at),
        "payload": event.payload,
    }


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
