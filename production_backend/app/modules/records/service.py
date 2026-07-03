from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import FeedingRecord, GrowthRecord, PumpingRecord
from .repository import RecordsRepository
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
        if volume_ml is None and duration_seconds is None:
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
            infant_id=infant_id,
            feed_time=feed_time,
            feed_type=feed_type,
            feed_action=feed_action,
            volume_ml=volume_ml,
            duration_seconds=duration_seconds,
            title=title,
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
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_feedings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )

    async def delete_feeding(self, *, owner_user_id: UUID, record_id: UUID, request_id: str = "") -> None:
        deleted = await self.repository.soft_delete_feeding(
            owner_user_id=owner_user_id,
            record_id=record_id,
            deleted_at=_utcnow(),
        )
        if deleted is None:
            raise ApiError(code="not_found", message="Feeding record not found.", status=404)
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
        if milk_volume_ml is None and duration_seconds is None:
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
            pump_start_time=pump_start_time,
            pump_end_time=pump_end_time,
            milk_volume_ml=milk_volume_ml,
            pump_type=pump_type,
            duration_seconds=duration_seconds,
            source=source,
            title=title,
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

    async def list_pumpings(
        self,
        *,
        owner_user_id: UUID,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[PumpingRecord]:
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_pumpings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )

    async def get_milk_trends(
        self,
        *,
        owner_user_id: UUID,
        start_date: date | None = None,
        days: int = 30,
        include_today: bool = True,
    ) -> MilkTrendListResponse:
        if days < 1 or days > 90:
            raise ApiError(code="validation_failed", message="days must be between 1 and 90.", status=422)
        first_day = start_date or _default_trend_start_date(days=days, include_today=include_today)
        start_at = datetime.combine(first_day, time.min, tzinfo=timezone.utc)
        end_at = start_at + timedelta(days=days)
        pumpings = await self.repository.list_pumpings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=1000,
        )
        totals_by_day: dict[date, float] = {}
        counts_by_day: dict[date, int] = {}
        for pumping in pumpings:
            pump_day = _as_utc_date(pumping.pump_start_time)
            if pump_day < first_day or pump_day >= first_day + timedelta(days=days):
                continue
            totals_by_day[pump_day] = totals_by_day.get(pump_day, 0.0) + float(pumping.milk_volume_ml or 0)
            counts_by_day[pump_day] = counts_by_day.get(pump_day, 0) + 1
        items = [
            MilkTrendDayRead(
                date=first_day + timedelta(days=offset),
                pumped_milk_volume_ml=round(totals_by_day.get(first_day + timedelta(days=offset), 0.0), 2),
                pumping_count=counts_by_day.get(first_day + timedelta(days=offset), 0),
                measured_only=True,
            )
            for offset in range(days)
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
        if height_cm is None and weight_kg is None and head_cm is None:
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
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        if infant_id is not None and not await self.repository.infant_belongs_to_owner(
            infant_id=infant_id,
            owner_user_id=owner_user_id,
        ):
            raise ApiError(code="owner_scope_violation", message="Infant profile is outside the current user scope.", status=403)
        return await self.repository.list_growth(owner_user_id=owner_user_id, infant_id=infant_id, limit=limit)

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


def _default_trend_start_date(*, days: int, include_today: bool) -> date:
    end_day = _utcnow().date() if include_today else _utcnow().date() - timedelta(days=1)
    return end_day - timedelta(days=days - 1)


def _as_utc_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date()
