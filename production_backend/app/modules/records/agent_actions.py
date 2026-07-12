from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ...workers.errors import PermanentJobError
from ..agent_runtime.actions.executor import AgentActionApplyResult
from ..agent_runtime.models import AgentAction
from .service import RecordsService


FEEDING_RECORD_CREATE_ACTION = "records.feeding_record.create"
PUMPING_RECORD_CREATE_ACTION = "records.pumping_record.create"
FEEDING_RECORD_DELETE_ACTION = "records.feeding_record.delete"
PUMPING_RECORD_DELETE_ACTION = "records.pumping_record.delete"
GROWTH_RECORD_CREATE_ACTION = "records.growth_record.create"
GROWTH_RECORD_UPDATE_ACTION = "records.growth_record.update"
GROWTH_RECORD_DELETE_ACTION = "records.growth_record.delete"


class FeedingRecordCreateActionHandler:
    def __init__(self, *, service: RecordsService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        feed_time = _required_datetime(payload, "feed_time", "missing_feed_time", "invalid_feed_time")
        feed_type = _required_text(payload, "feed_type", "missing_feed_type")
        volume_ml = _optional_number(payload, "volume_ml", "invalid_volume_ml")
        duration_seconds = _optional_int(payload, "duration_seconds", "invalid_duration_seconds")
        if volume_ml is None and duration_seconds is None:
            raise PermanentJobError("missing_feeding_quantity")

        try:
            record = await self.service.create_feeding(
                owner_user_id=action.actor_user_id,
                infant_id=_optional_uuid(payload, "infant_id", "invalid_infant_id"),
                feed_time=feed_time,
                feed_type=feed_type,
                feed_action=_text(payload, "feed_action"),
                volume_ml=volume_ml,
                duration_seconds=duration_seconds,
                title=_text(payload, "title"),
                request_id=f"agent-action:{action.id}",
                idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="feeding_record",
            resource_id=str(record.id),
            details={
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


class PumpingRecordCreateActionHandler:
    def __init__(self, *, service: RecordsService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        pump_start_time = _required_datetime(
            payload,
            "pump_start_time",
            "missing_pump_start_time",
            "invalid_pump_start_time",
        )
        milk_volume_ml = _optional_number(payload, "milk_volume_ml", "invalid_milk_volume_ml")
        duration_seconds = _optional_int(payload, "duration_seconds", "invalid_duration_seconds")
        if milk_volume_ml is None and duration_seconds is None:
            raise PermanentJobError("missing_pumping_quantity")

        try:
            record = await self.service.create_pumping(
                owner_user_id=action.actor_user_id,
                pump_start_time=pump_start_time,
                pump_end_time=_optional_datetime(payload, "pump_end_time", "invalid_pump_end_time"),
                milk_volume_ml=milk_volume_ml,
                pump_type=_text(payload, "pump_type"),
                duration_seconds=duration_seconds,
                source=_text(payload, "source") or "agent",
                title=_text(payload, "title"),
                request_id=f"agent-action:{action.id}",
                idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="pumping_record",
            resource_id=str(record.id),
            details={
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


class FeedingRecordDeleteActionHandler:
    def __init__(self, *, service: RecordsService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        record_id = _required_uuid(payload, "record_id", "missing_record_id", "invalid_record_id")
        try:
            await self.service.delete_feeding(
                owner_user_id=action.actor_user_id,
                record_id=record_id,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        return AgentActionApplyResult(
            resource_type="feeding_record",
            resource_id=str(record_id),
            details={"agent_action_id": str(action.id), "agent_run_id": str(action.run_id)},
        )


class PumpingRecordDeleteActionHandler:
    def __init__(self, *, service: RecordsService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        record_id = _required_uuid(payload, "record_id", "missing_record_id", "invalid_record_id")
        try:
            await self.service.delete_pumping(
                owner_user_id=action.actor_user_id,
                record_id=record_id,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        return AgentActionApplyResult(
            resource_type="pumping_record",
            resource_id=str(record_id),
            details={"agent_action_id": str(action.id), "agent_run_id": str(action.run_id)},
        )


class GrowthRecordCreateActionHandler:
    def __init__(self, *, service: RecordsService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        measured_at = _required_datetime(payload, "measured_at", "missing_measured_at", "invalid_measured_at")
        height_cm = _optional_number(payload, "height_cm", "invalid_height_cm")
        weight_kg = _optional_number(payload, "weight_kg", "invalid_weight_kg")
        head_cm = _optional_number(payload, "head_cm", "invalid_head_cm")
        if height_cm is None and weight_kg is None and head_cm is None:
            raise PermanentJobError("missing_growth_measurement")

        try:
            record = await self.service.create_growth(
                owner_user_id=action.actor_user_id,
                infant_id=_optional_uuid(payload, "infant_id", "invalid_infant_id"),
                measured_at=measured_at,
                height_cm=height_cm,
                weight_kg=weight_kg,
                head_cm=head_cm,
                request_id=f"agent-action:{action.id}",
                idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="growth_record",
            resource_id=str(record.id),
            details={"agent_action_id": str(action.id), "agent_run_id": str(action.run_id)},
        )


class GrowthRecordUpdateActionHandler:
    def __init__(self, *, service: RecordsService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        record_id = _required_uuid(payload, "record_id", "missing_record_id", "invalid_record_id")
        updates = _growth_updates(payload)
        if not updates:
            raise PermanentJobError("missing_growth_update")
        try:
            record = await self.service.update_growth(
                owner_user_id=action.actor_user_id,
                record_id=record_id,
                updates=updates,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        return AgentActionApplyResult(
            resource_type="growth_record",
            resource_id=str(record.id),
            details={"agent_action_id": str(action.id), "agent_run_id": str(action.run_id), "fields": sorted(updates)},
        )


class GrowthRecordDeleteActionHandler:
    def __init__(self, *, service: RecordsService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        record_id = _required_uuid(payload, "record_id", "missing_record_id", "invalid_record_id")
        try:
            await self.service.delete_growth(
                owner_user_id=action.actor_user_id,
                record_id=record_id,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        return AgentActionApplyResult(
            resource_type="growth_record",
            resource_id=str(record_id),
            details={"agent_action_id": str(action.id), "agent_run_id": str(action.run_id)},
        )


def _required_text(payload: dict[str, Any], key: str, code: str) -> str:
    value = _text(payload, key)
    if not value:
        raise PermanentJobError(code)
    return value


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _optional_uuid(payload: dict[str, Any], key: str, code: str) -> UUID | None:
    value = _text(payload, key)
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise PermanentJobError(code) from exc


def _required_uuid(payload: dict[str, Any], key: str, missing_code: str, invalid_code: str) -> UUID:
    value = _text(payload, key)
    if not value:
        raise PermanentJobError(missing_code)
    try:
        return UUID(value)
    except ValueError as exc:
        raise PermanentJobError(invalid_code) from exc


def _required_datetime(payload: dict[str, Any], key: str, missing_code: str, invalid_code: str) -> datetime:
    value = _optional_datetime(payload, key, invalid_code)
    if value is None:
        raise PermanentJobError(missing_code)
    return value


def _optional_datetime(payload: dict[str, Any], key: str, code: str) -> datetime | None:
    value = _text(payload, key)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PermanentJobError(code) from exc


def _optional_number(payload: dict[str, Any], key: str, code: str) -> float | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise PermanentJobError(code)
    if isinstance(value, int | float):
        parsed = float(value)
        if parsed < 0:
            raise PermanentJobError(code)
        return parsed
    raise PermanentJobError(code)


def _optional_int(payload: dict[str, Any], key: str, code: str) -> int | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise PermanentJobError(code)
    if isinstance(value, int):
        if value < 0:
            raise PermanentJobError(code)
        return value
    raise PermanentJobError(code)


def _growth_updates(payload: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    infant_id = _text(payload, "infant_id")
    if infant_id:
        updates["infant_id"] = _optional_uuid(payload, "infant_id", "invalid_infant_id")
    measured_at = _optional_datetime(payload, "measured_at", "invalid_measured_at")
    if measured_at is not None:
        updates["measured_at"] = measured_at
    for key, code in (("height_cm", "invalid_height_cm"), ("weight_kg", "invalid_weight_kg"), ("head_cm", "invalid_head_cm")):
        if key in payload:
            updates[key] = _optional_number(payload, key, code)
    return updates
