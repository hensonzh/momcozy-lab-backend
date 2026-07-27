from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from . import domain
from .models import FeedingRecord, GrowthRecord, PumpingRecord
from .repository import LatestGrowthMeasurement, RecordsRepository
from .schemas import MilkTrendDayRead, MilkTrendListResponse


FEEDING_CREATE_IDEMPOTENCY_SCOPE = "records.feeding.create"
PUMPING_CREATE_IDEMPOTENCY_SCOPE = "records.pumping.create"
GROWTH_CREATE_IDEMPOTENCY_SCOPE = "records.growth.create"


class RecordsService:
    def __init__(
        self,
        *,
        repository: RecordsRepository,
        audit_service: AuditService | None = None,
        idempotency_service: IdempotencyService | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.idempotency_service = idempotency_service

    async def create_feeding(
        self,
        *,
        owner_user_id: UUID,
        infant_id: UUID | None,
        plan_task_id: UUID | None = None,
        feed_time: datetime,
        feed_type: str,
        feed_action: str = "",
        volume_ml: float | None = None,
        duration_seconds: int | None = None,
        title: str = "",
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> FeedingRecord:
        if infant_id is not None and not await self.repository.infant_belongs_to_owner(
            infant_id=infant_id,
            owner_user_id=owner_user_id,
        ):
            raise ApiError(code="owner_scope_violation", message="Infant profile is outside the current user scope.", status=403)
        await self._validate_plan_task_owner(
            plan_task_id=plan_task_id,
            owner_user_id=owner_user_id,
            expected_record_kind="feeding",
        )
        if not domain.has_feeding_measurement(volume_ml=volume_ml, duration_seconds=duration_seconds):
            raise ApiError(code="validation_failed", message="volume_ml or duration_seconds is required.", status=422)

        idempotency_record = None
        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=FEEDING_CREATE_IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash(
                    {
                        "infant_id": str(infant_id or ""),
                        "plan_task_id": str(plan_task_id or ""),
                        "feed_time": feed_time.isoformat(),
                        "feed_type": feed_type,
                        "feed_action": feed_action,
                        "volume_ml": volume_ml,
                        "duration_seconds": duration_seconds,
                        "title": title,
                    }
                ),
                expires_at=_utcnow() + timedelta(hours=24),
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                return await self._replay_feeding(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        record = await self.repository.create_feeding(
            owner_user_id=owner_user_id,
            plan_task_id=plan_task_id,
            infant_id=infant_id,
            feed_time=feed_time,
            feed_type=feed_type,
            feed_action=feed_action,
            volume_ml=volume_ml,
            duration_seconds=duration_seconds,
            title=title,
        )
        await self._complete_linked_plan_task(plan_task_id=plan_task_id, owner_user_id=owner_user_id)
        await self._audit_linked_plan_task(
            plan_task_id=plan_task_id,
            owner_user_id=owner_user_id,
            request_id=request_id,
        )
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(record.id))
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.feeding.create",
                resource_type="feeding_record",
                resource_id=str(record.id),
                request_id=request_id,
                details={"feed_type": feed_type},
            )
        return record

    async def list_feedings(
        self,
        *,
        owner_user_id: UUID,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[FeedingRecord]:
        if not domain.is_valid_list_limit(limit):
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_feedings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )

    async def update_feeding(
        self,
        *,
        owner_user_id: UUID,
        record_id: UUID,
        updates: dict[str, Any],
        request_id: str = "",
    ) -> FeedingRecord:
        if domain.unsupported_feeding_update_fields(updates):
            raise ApiError(code="validation_failed", message="Unsupported feeding update fields.", status=422)
        if not updates:
            raise ApiError(code="validation_failed", message="At least one feeding field is required.", status=422)
        record = await self.repository.get_feeding_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            raise ApiError(code="not_found", message="Feeding record not found.", status=404)
        infant_id = updates.get("infant_id", record.infant_id)
        if infant_id is not None and not await self.repository.infant_belongs_to_owner(
            infant_id=infant_id,
            owner_user_id=owner_user_id,
        ):
            raise ApiError(code="owner_scope_violation", message="Infant profile is outside the current user scope.", status=403)
        plan_task_id = updates.get("plan_task_id", record.plan_task_id)
        if "plan_task_id" in updates:
            await self._validate_plan_task_owner(
                plan_task_id=plan_task_id,
                owner_user_id=owner_user_id,
                expected_record_kind="feeding",
            )
        if not domain.has_feeding_measurement(
            volume_ml=updates.get("volume_ml", record.volume_ml),
            duration_seconds=updates.get("duration_seconds", record.duration_seconds),
        ):
            raise ApiError(code="validation_failed", message="volume_ml or duration_seconds is required.", status=422)
        updated = await self.repository.update_feeding(
            owner_user_id=owner_user_id,
            record_id=record_id,
            updates=updates,
        )
        if updated is None:
            raise ApiError(code="not_found", message="Feeding record not found.", status=404)
        if "plan_task_id" in updates:
            await self._complete_linked_plan_task(plan_task_id=plan_task_id, owner_user_id=owner_user_id)
            await self._audit_linked_plan_task(
                plan_task_id=plan_task_id,
                owner_user_id=owner_user_id,
                request_id=request_id,
            )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.feeding.update",
                resource_type="feeding_record",
                resource_id=str(record_id),
                request_id=request_id,
                details={"fields": sorted(updates)},
            )
        return updated

    async def delete_feeding(self, *, owner_user_id: UUID, record_id: UUID, request_id: str = "") -> None:
        deleted = await self.repository.soft_delete_feeding(
            owner_user_id=owner_user_id,
            record_id=record_id,
            deleted_at=_utcnow(),
        )
        if deleted is None:
            raise ApiError(code="not_found", message="Feeding record not found.", status=404)
        await self._restore_linked_plan_task_after_record_delete(
            plan_task_id=deleted.plan_task_id,
            owner_user_id=owner_user_id,
            request_id=request_id,
        )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.feeding.delete",
                resource_type="feeding_record",
                resource_id=str(record_id),
                request_id=request_id,
            )

    async def create_pumping(
        self,
        *,
        owner_user_id: UUID,
        plan_task_id: UUID | None = None,
        pump_start_time: datetime,
        pump_end_time: datetime | None = None,
        milk_volume_ml: float | None = None,
        pump_type: str = "",
        duration_seconds: int | None = None,
        source: str = "manual",
        title: str = "",
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> PumpingRecord:
        await self._validate_plan_task_owner(
            plan_task_id=plan_task_id,
            owner_user_id=owner_user_id,
            expected_record_kind="pumping",
        )
        if not domain.has_pumping_measurement(milk_volume_ml=milk_volume_ml, duration_seconds=duration_seconds):
            raise ApiError(code="validation_failed", message="milk_volume_ml or duration_seconds is required.", status=422)

        idempotency_record = None
        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=PUMPING_CREATE_IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash(
                    {
                        "pump_start_time": pump_start_time.isoformat(),
                        "plan_task_id": str(plan_task_id or ""),
                        "pump_end_time": pump_end_time.isoformat() if pump_end_time else "",
                        "milk_volume_ml": milk_volume_ml,
                        "pump_type": pump_type,
                        "duration_seconds": duration_seconds,
                        "source": source,
                        "title": title,
                    }
                ),
                expires_at=_utcnow() + timedelta(hours=24),
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                return await self._replay_pumping(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        record = await self.repository.create_pumping(
            owner_user_id=owner_user_id,
            plan_task_id=plan_task_id,
            pump_start_time=pump_start_time,
            pump_end_time=pump_end_time,
            milk_volume_ml=milk_volume_ml,
            pump_type=pump_type,
            duration_seconds=duration_seconds,
            source=source,
            title=title,
        )
        await self._complete_linked_plan_task(plan_task_id=plan_task_id, owner_user_id=owner_user_id)
        await self._audit_linked_plan_task(
            plan_task_id=plan_task_id,
            owner_user_id=owner_user_id,
            request_id=request_id,
        )
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(record.id))
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.pumping.create",
                resource_type="pumping_record",
                resource_id=str(record.id),
                request_id=request_id,
                details={"source": source},
            )
        return record

    async def _validate_plan_task_owner(
        self,
        *,
        plan_task_id: UUID | None,
        owner_user_id: UUID,
        expected_record_kind: str,
    ) -> None:
        if plan_task_id is None:
            return
        task = await self.repository.get_plan_task_for_owner(
            plan_task_id=plan_task_id,
            owner_user_id=owner_user_id,
        )
        if task is None:
            raise ApiError(
                code="owner_scope_violation",
                message="Plan task is outside the current user scope.",
                status=403,
            )
        task_record_kind = _task_record_kind(task.payload)
        if task_record_kind is not None and task_record_kind != expected_record_kind:
            raise ApiError(
                code="task_record_type_mismatch",
                message=f"A {expected_record_kind} record cannot be linked to a {task_record_kind} task.",
                status=409,
            )

    async def _complete_linked_plan_task(self, *, plan_task_id: UUID | None, owner_user_id: UUID) -> None:
        if plan_task_id is None:
            return
        completed = await self.repository.complete_plan_task(
            plan_task_id=plan_task_id,
            owner_user_id=owner_user_id,
        )
        if not completed:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)

    async def _audit_linked_plan_task(
        self,
        *,
        plan_task_id: UUID | None,
        owner_user_id: UUID,
        request_id: str,
    ) -> None:
        if plan_task_id is None or self.audit_service is None:
            return
        await self.audit_service.record(
            actor_user_id=owner_user_id,
            action="plans.tasks.complete_from_record",
            resource_type="plan_task",
            resource_id=str(plan_task_id),
            request_id=request_id,
        )

    async def _restore_linked_plan_task_after_record_delete(
        self,
        *,
        plan_task_id: UUID | None,
        owner_user_id: UUID,
        request_id: str,
    ) -> None:
        if plan_task_id is None:
            return
        restored = await self.repository.restore_plan_task_if_unrecorded(
            plan_task_id=plan_task_id,
            owner_user_id=owner_user_id,
        )
        if restored and self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="plans.tasks.restore_from_record_delete",
                resource_type="plan_task",
                resource_id=str(plan_task_id),
                request_id=request_id,
            )

    async def list_pumpings(
        self,
        *,
        owner_user_id: UUID,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[PumpingRecord]:
        if not domain.is_valid_list_limit(limit):
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_pumpings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )

    async def update_pumping(
        self,
        *,
        owner_user_id: UUID,
        record_id: UUID,
        updates: dict[str, Any],
        request_id: str = "",
    ) -> PumpingRecord:
        if domain.unsupported_pumping_update_fields(updates):
            raise ApiError(code="validation_failed", message="Unsupported pumping update fields.", status=422)
        if not updates:
            raise ApiError(code="validation_failed", message="At least one pumping field is required.", status=422)
        record = await self.repository.get_pumping_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            raise ApiError(code="not_found", message="Pumping record not found.", status=404)
        plan_task_id = updates.get("plan_task_id", record.plan_task_id)
        if "plan_task_id" in updates:
            await self._validate_plan_task_owner(
                plan_task_id=plan_task_id,
                owner_user_id=owner_user_id,
                expected_record_kind="pumping",
            )
        if not domain.has_pumping_measurement(
            milk_volume_ml=updates.get("milk_volume_ml", record.milk_volume_ml),
            duration_seconds=updates.get("duration_seconds", record.duration_seconds),
        ):
            raise ApiError(code="validation_failed", message="milk_volume_ml or duration_seconds is required.", status=422)
        updated = await self.repository.update_pumping(
            owner_user_id=owner_user_id,
            record_id=record_id,
            updates=updates,
        )
        if updated is None:
            raise ApiError(code="not_found", message="Pumping record not found.", status=404)
        if "plan_task_id" in updates:
            await self._complete_linked_plan_task(plan_task_id=plan_task_id, owner_user_id=owner_user_id)
            await self._audit_linked_plan_task(
                plan_task_id=plan_task_id,
                owner_user_id=owner_user_id,
                request_id=request_id,
            )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.pumping.update",
                resource_type="pumping_record",
                resource_id=str(record_id),
                request_id=request_id,
                details={"fields": sorted(updates)},
            )
        return updated

    async def get_milk_trends(
        self,
        *,
        owner_user_id: UUID,
        start_date: date | None = None,
        days: int = 30,
        include_today: bool = True,
    ) -> MilkTrendListResponse:
        if not domain.is_valid_trend_days(days):
            raise ApiError(code="validation_failed", message="days must be between 1 and 90.", status=422)
        first_day = start_date or domain.default_trend_start_date(now=_utcnow(), days=days, include_today=include_today)
        start_at, end_at = domain.trend_datetime_window(first_day=first_day, days=days)
        pumpings = await self.repository.list_pumpings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=1000,
        )
        trend_days = domain.build_measured_milk_trend_days(pumpings=pumpings, first_day=first_day, days=days)
        items = [
            MilkTrendDayRead(
                date=trend_day.date,
                pumped_milk_volume_ml=trend_day.pumped_milk_volume_ml,
                pumping_count=trend_day.pumping_count,
                measured_only=True,
            )
            for trend_day in trend_days
        ]
        return MilkTrendListResponse(items=items, days=days, include_today=include_today)

    async def delete_pumping(self, *, owner_user_id: UUID, record_id: UUID, request_id: str = "") -> None:
        deleted = await self.repository.soft_delete_pumping(
            owner_user_id=owner_user_id,
            record_id=record_id,
            deleted_at=_utcnow(),
        )
        if deleted is None:
            raise ApiError(code="not_found", message="Pumping record not found.", status=404)
        await self._restore_linked_plan_task_after_record_delete(
            plan_task_id=deleted.plan_task_id,
            owner_user_id=owner_user_id,
            request_id=request_id,
        )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.pumping.delete",
                resource_type="pumping_record",
                resource_id=str(record_id),
                request_id=request_id,
            )

    async def create_growth(
        self,
        *,
        owner_user_id: UUID,
        infant_id: UUID | None,
        measured_at: datetime,
        height_cm: float | None = None,
        weight_kg: float | None = None,
        head_cm: float | None = None,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> GrowthRecord:
        if infant_id is not None and not await self.repository.infant_belongs_to_owner(
            infant_id=infant_id,
            owner_user_id=owner_user_id,
        ):
            raise ApiError(code="owner_scope_violation", message="Infant profile is outside the current user scope.", status=403)
        if not domain.has_growth_measurement(height_cm=height_cm, weight_kg=weight_kg, head_cm=head_cm):
            raise ApiError(code="validation_failed", message="height_cm, weight_kg, or head_cm is required.", status=422)

        idempotency_record = None
        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=GROWTH_CREATE_IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash(
                    {
                        "infant_id": str(infant_id or ""),
                        "measured_at": measured_at.isoformat(),
                        "height_cm": height_cm,
                        "weight_kg": weight_kg,
                        "head_cm": head_cm,
                    }
                ),
                expires_at=_utcnow() + timedelta(hours=24),
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                return await self._replay_growth(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        record = await self.repository.create_growth(
            owner_user_id=owner_user_id,
            infant_id=infant_id,
            measured_at=measured_at,
            height_cm=height_cm,
            weight_kg=weight_kg,
            head_cm=head_cm,
        )
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(record.id))
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.growth.create",
                resource_type="growth_record",
                resource_id=str(record.id),
                request_id=request_id,
            )
        return record

    async def list_growth(
        self,
        *,
        owner_user_id: UUID,
        infant_id: UUID | None = None,
        limit: int = 50,
    ) -> list[GrowthRecord]:
        if not domain.is_valid_list_limit(limit):
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        if infant_id is not None and not await self.repository.infant_belongs_to_owner(
            infant_id=infant_id,
            owner_user_id=owner_user_id,
        ):
            raise ApiError(code="owner_scope_violation", message="Infant profile is outside the current user scope.", status=403)
        return await self.repository.list_growth(owner_user_id=owner_user_id, infant_id=infant_id, limit=limit)

    async def list_growth_in_range(
        self,
        *,
        owner_user_id: UUID,
        start_at: datetime,
        end_at: datetime,
        limit: int,
    ) -> list[GrowthRecord]:
        if not domain.is_valid_list_limit(limit):
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        if end_at <= start_at:
            raise ApiError(code="validation_failed", message="end_at must be later than start_at.", status=422)
        return await self.repository.list_growth_in_range(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )

    async def list_latest_growth_by_infant_ids(
        self,
        *,
        owner_user_id: UUID,
        infant_ids: list[UUID],
    ) -> dict[UUID, LatestGrowthMeasurement]:
        if len(infant_ids) > 10 or len(set(infant_ids)) != len(infant_ids):
            raise ApiError(
                code="validation_failed",
                message="infant_ids must contain at most 10 unique IDs.",
                status=422,
            )
        return await self.repository.list_latest_growth_by_infant_ids(
            owner_user_id=owner_user_id,
            infant_ids=infant_ids,
        )

    async def update_growth(
        self,
        *,
        owner_user_id: UUID,
        record_id: UUID,
        updates: dict[str, Any],
        request_id: str = "",
    ) -> GrowthRecord:
        unknown_fields = domain.unsupported_growth_update_fields(updates)
        if unknown_fields:
            raise ApiError(code="validation_failed", message="Unsupported growth update fields.", status=422)
        if not updates:
            raise ApiError(code="validation_failed", message="At least one growth field is required.", status=422)

        record = await self.repository.get_growth_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            raise ApiError(code="not_found", message="Growth record not found.", status=404)
        infant_id = updates.get("infant_id", record.infant_id)
        if infant_id is not None and not await self.repository.infant_belongs_to_owner(
            infant_id=infant_id,
            owner_user_id=owner_user_id,
        ):
            raise ApiError(code="owner_scope_violation", message="Infant profile is outside the current user scope.", status=403)

        next_height_cm, next_weight_kg, next_head_cm = domain.growth_measurements_after_update(
            current_height_cm=record.height_cm,
            current_weight_kg=record.weight_kg,
            current_head_cm=record.head_cm,
            updates=updates,
        )
        if not domain.has_growth_measurement(height_cm=next_height_cm, weight_kg=next_weight_kg, head_cm=next_head_cm):
            raise ApiError(code="validation_failed", message="height_cm, weight_kg, or head_cm is required.", status=422)

        updated = await self.repository.update_growth(
            owner_user_id=owner_user_id,
            record_id=record_id,
            updates=updates,
        )
        if updated is None:
            raise ApiError(code="not_found", message="Growth record not found.", status=404)
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.growth.update",
                resource_type="growth_record",
                resource_id=str(record_id),
                request_id=request_id,
                details={"fields": sorted(updates)},
            )
        return updated

    async def delete_growth(self, *, owner_user_id: UUID, record_id: UUID, request_id: str = "") -> None:
        deleted = await self.repository.soft_delete_growth(
            owner_user_id=owner_user_id,
            record_id=record_id,
            deleted_at=_utcnow(),
        )
        if deleted is None:
            raise ApiError(code="not_found", message="Growth record not found.", status=404)
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.growth.delete",
                resource_type="growth_record",
                resource_id=str(record_id),
                request_id=request_id,
            )

    async def _replay_feeding(self, *, owner_user_id: UUID, response_ref: str) -> FeedingRecord:
        record_id = parse_idempotency_response_ref(response_ref)
        record = await self.repository.get_feeding_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return record

    async def _replay_pumping(self, *, owner_user_id: UUID, response_ref: str) -> PumpingRecord:
        record_id = parse_idempotency_response_ref(response_ref)
        record = await self.repository.get_pumping_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return record

    async def _replay_growth(self, *, owner_user_id: UUID, response_ref: str) -> GrowthRecord:
        record_id = parse_idempotency_response_ref(response_ref)
        record = await self.repository.get_growth_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return record


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _task_record_kind(payload: dict[str, Any]) -> str | None:
    aliases = {
        "feed": "feeding",
        "feeding": "feeding",
        "breastfeed": "feeding",
        "breastfeeding": "feeding",
        "bottle_feed": "feeding",
        "pump": "pumping",
        "pumping": "pumping",
        "breast_pump": "pumping",
        "milk_expression": "pumping",
        "expression": "pumping",
        "喂养": "feeding",
        "喂奶": "feeding",
        "吸奶": "pumping",
        "泵奶": "pumping",
    }
    for key in ("task_type", "record_type", "type", "kind"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in aliases:
            return aliases[normalized]
    return None
