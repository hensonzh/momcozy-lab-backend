"""Bounded, owner-scoped topical record projection; notes and histories stay private."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ..baby.models import BabyRecord
from ..records.models import FeedingRecord, GrowthRecord, PumpingRecord
from .me_models import MotherObservation
from .repository import ProfileRepository

Topic = Literal["feeding", "pumping", "diaper", "pain", "growth"]
Source = Literal["baby_records", "feeding_records", "pumping_records", "mother_observations", "growth_records"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopicalRecord(StrictModel):
    source: Source
    kind: Topic
    record_type: Literal["event", "daily_summary"] | None = None
    occurred_at: datetime | None = None
    recorded_on: date | None = None
    method: str | None = None
    side: str | None = None
    volume_ml: float | None = None
    duration_minutes: float | None = None
    diaper_kind: str | None = None
    wet_count: int | None = None
    stool_count: int | None = None
    color: str | None = None
    consistency: str | None = None
    signs: list[str] | None = None
    pain_score: int | None = None
    phase: str | None = None
    impact: str | None = None
    metric: str | None = None
    value: float | None = None
    weight_kg: float | None = None
    height_cm: float | None = None
    head_circumference_cm: float | None = None


class TopicalRecordsReadOutput(StrictModel):
    topic: Topic
    infant_id: UUID | None
    start_date: date
    end_date: date
    timezone: str
    items: list[TopicalRecord]
    has_more: bool
    coverage: Literal["recorded_entries_only"] = "recorded_entries_only"


class TopicalRecordsQuery(StrictModel):
    actor_user_id: UUID
    topic: Topic
    infant_id: UUID | None = None
    start_date: date
    end_date: date
    timezone: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def valid_scope(self) -> TopicalRecordsQuery:
        if self.start_date > self.end_date or (self.end_date - self.start_date).days >= 30:
            raise ValueError("Use a chronological window of at most 30 calendar days.")
        if (self.topic in {"feeding", "diaper", "growth"}) != (self.infant_id is not None):
            raise ValueError("Infant topics require infant_id; mother topics must not include one.")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use an IANA timezone.") from exc
        return self


class TopicalRecordsService:
    def __init__(self, session: AsyncSession, profiles: ProfileRepository) -> None:
        self.session = session
        self.profiles = profiles

    async def read(self, query: TopicalRecordsQuery) -> TopicalRecordsReadOutput:
        owner, infant_id = query.actor_user_id, query.infant_id
        if infant_id is not None and not await self.profiles.is_current_delivery_infant(owner_user_id=owner, infant_id=infant_id):
            raise ApiError(code="not_found", message="Current baby was not found.", status=404)
        zone = ZoneInfo(query.timezone)
        start = datetime.combine(query.start_date, time.min, tzinfo=zone).astimezone(timezone.utc)
        end = datetime.combine(query.end_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
        rows: list[tuple[datetime, str, TopicalRecord]] = []
        cap = query.limit + 1
        if query.topic in {"feeding", "diaper", "growth"}:
            assert infant_id is not None
            statement = select(BabyRecord).where(
                BabyRecord.owner_user_id == owner,
                BabyRecord.baby_id == infant_id,
                BabyRecord.deleted_at.is_(None),
                BabyRecord.kind == query.topic,
            )
            if query.topic == "growth":
                statement = statement.where(BabyRecord.recorded_on >= query.start_date, BabyRecord.recorded_on <= query.end_date).order_by(
                    BabyRecord.recorded_on.desc(), BabyRecord.updated_at.desc(), BabyRecord.id.desc()
                )
            else:
                statement = statement.where(BabyRecord.occurred_at >= start, BabyRecord.occurred_at < end).order_by(
                    BabyRecord.occurred_at.desc(), BabyRecord.id.desc()
                )
            for item in await self.session.scalars(statement.limit(cap)):
                data = item.data
                if query.topic == "feeding":
                    record = TopicalRecord(
                        source="baby_records",
                        kind="feeding",
                        occurred_at=item.occurred_at,
                        method=data.get("method"),
                        side=data.get("side"),
                        volume_ml=data.get("volume_ml"),
                        duration_minutes=data.get("duration_minutes"),
                    )
                elif query.topic == "diaper":
                    record = TopicalRecord(
                        source="baby_records",
                        kind="diaper",
                        record_type="event",
                        occurred_at=item.occurred_at,
                        diaper_kind=data.get("diaper_kind"),
                        color=data.get("color"),
                        consistency=data.get("consistency"),
                        signs=data.get("signs"),
                    )
                else:
                    record = TopicalRecord(
                        source="baby_records",
                        kind="growth",
                        recorded_on=item.recorded_on,
                        metric=data.get("metric"),
                        value=data.get("value"),
                    )
                sort_time = item.occurred_at or datetime.combine(cast(date, item.recorded_on), time.min, tzinfo=zone).astimezone(
                    timezone.utc
                )
                rows.append((sort_time, str(item.id), record))
        if query.topic == "diaper":
            assert infant_id is not None
            # Daily counts are distinct from individual diaper events. Never add
            # them together: users may have recorded the same care twice.
            summary_statement = (
                select(BabyRecord)
                .where(
                    BabyRecord.owner_user_id == owner,
                    BabyRecord.baby_id == infant_id,
                    BabyRecord.deleted_at.is_(None),
                    BabyRecord.kind == "daily_status",
                    BabyRecord.recorded_on >= query.start_date,
                    BabyRecord.recorded_on <= query.end_date,
                )
                .order_by(BabyRecord.recorded_on.desc(), BabyRecord.updated_at.desc(), BabyRecord.id.desc())
                .limit(cap)
            )
            for summary in await self.session.scalars(summary_statement):
                data = summary.data
                rows.append(
                    (
                        datetime.combine(cast(date, summary.recorded_on), time.min, tzinfo=zone).astimezone(timezone.utc),
                        str(summary.id),
                        TopicalRecord(
                            source="baby_records",
                            kind="diaper",
                            record_type="daily_summary",
                            recorded_on=summary.recorded_on,
                            wet_count=data.get("wet_count"),
                            stool_count=data.get("stool_count"),
                            color=data.get("color"),
                            consistency=data.get("consistency"),
                        ),
                    )
                )
        if query.topic == "feeding":
            assert infant_id is not None
            feeding_statement = (
                select(FeedingRecord)
                .where(
                    FeedingRecord.owner_user_id == owner,
                    FeedingRecord.infant_id == infant_id,
                    FeedingRecord.status == "active",
                    FeedingRecord.deleted_at.is_(None),
                    FeedingRecord.feed_time >= start,
                    FeedingRecord.feed_time < end,
                )
                .order_by(FeedingRecord.feed_time.desc(), FeedingRecord.id.desc())
                .limit(cap)
            )
            for feeding in await self.session.scalars(feeding_statement):
                rows.append(
                    (
                        feeding.feed_time,
                        str(feeding.id),
                        TopicalRecord(
                            source="feeding_records",
                            kind="feeding",
                            occurred_at=feeding.feed_time,
                            method=feeding.feed_type,
                            volume_ml=feeding.volume_ml,
                            duration_minutes=feeding.duration_seconds / 60 if feeding.duration_seconds is not None else None,
                        ),
                    )
                )
        elif query.topic == "pumping":
            pumping_statement = (
                select(PumpingRecord)
                .where(
                    PumpingRecord.owner_user_id == owner,
                    PumpingRecord.status == "active",
                    PumpingRecord.deleted_at.is_(None),
                    PumpingRecord.pump_start_time >= start,
                    PumpingRecord.pump_start_time < end,
                )
                .order_by(PumpingRecord.pump_start_time.desc(), PumpingRecord.id.desc())
                .limit(cap)
            )
            for pumping in await self.session.scalars(pumping_statement):
                rows.append(
                    (
                        pumping.pump_start_time,
                        str(pumping.id),
                        TopicalRecord(
                            source="pumping_records",
                            kind="pumping",
                            occurred_at=pumping.pump_start_time,
                            volume_ml=pumping.milk_volume_ml,
                            duration_minutes=pumping.duration_seconds / 60 if pumping.duration_seconds is not None else None,
                        ),
                    )
                )
        elif query.topic == "pain":
            pain_statement = (
                select(MotherObservation)
                .where(
                    MotherObservation.owner_user_id == owner,
                    MotherObservation.kind == "pain",
                    MotherObservation.occurred_at >= start,
                    MotherObservation.occurred_at < end,
                )
                .order_by(MotherObservation.occurred_at.desc(), MotherObservation.id.desc())
                .limit(cap)
            )
            for pain in await self.session.scalars(pain_statement):
                fields = pain.fields
                rows.append(
                    (
                        pain.occurred_at,
                        str(pain.id),
                        TopicalRecord(
                            source="mother_observations",
                            kind="pain",
                            occurred_at=pain.occurred_at,
                            pain_score=fields.get("pain"),
                            side=fields.get("side"),
                            phase=fields.get("phase"),
                            impact=fields.get("impact"),
                        ),
                    )
                )
        elif query.topic == "growth":
            assert infant_id is not None
            growth_statement = (
                select(GrowthRecord)
                .where(
                    GrowthRecord.owner_user_id == owner,
                    GrowthRecord.infant_id == infant_id,
                    GrowthRecord.status == "active",
                    GrowthRecord.deleted_at.is_(None),
                    GrowthRecord.measured_at >= start,
                    GrowthRecord.measured_at < end,
                )
                .order_by(GrowthRecord.measured_at.desc(), GrowthRecord.id.desc())
                .limit(cap)
            )
            for growth in await self.session.scalars(growth_statement):
                rows.append(
                    (
                        growth.measured_at,
                        str(growth.id),
                        TopicalRecord(
                            source="growth_records",
                            kind="growth",
                            occurred_at=growth.measured_at,
                            recorded_on=growth.measured_at.astimezone(zone).date(),
                            weight_kg=growth.weight_kg,
                            height_cm=growth.height_cm,
                            head_circumference_cm=growth.head_cm,
                        ),
                    )
                )
        rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return TopicalRecordsReadOutput(
            topic=query.topic,
            infant_id=infant_id,
            start_date=query.start_date,
            end_date=query.end_date,
            timezone=query.timezone,
            items=[record for _, _, record in rows[: query.limit]],
            has_more=len(rows) > query.limit,
        )
