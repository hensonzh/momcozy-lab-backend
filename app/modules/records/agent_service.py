from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...core.errors import ApiError
from ..audit import request_hash
from ..baby.repository import BabyRecordRepository
from .agent_contracts import (
    AgentLactationRecordApplyPayload,
    AgentMilkAnalysisSnapshot,
)
from .milk_analysis_schema import (
    MilkAnalysisCounts,
    MilkAnalysisFeedingRecord,
    MilkAnalysisGrowthRecord,
    MilkAnalysisInterpretation,
    MilkAnalysisLatestEvents,
    MilkAnalysisPumpingRecord,
    MilkAnalysisPumpingRhythm,
    MilkAnalysisStatus,
    MilkAnalysisTrendDay,
    MilkAnalysisVolumes,
    MilkAnalysisWindow,
)


AGENT_LACTATION_RECORD_IDEMPOTENCY_SCOPE = "internal.agent.lactation.record"
LACTATION_TIMELINE_CHANGED_EVENT = "lactation.timeline.changed"
_APPLIED_OPERATIONS = {
    "create": "created",
    "update": "updated",
    "delete": "deleted",
}


@dataclass(frozen=True)
class AgentLactationRecordWriteResult:
    resource_type: str
    resource_id: str
    details: dict[str, Any]
    application_events: tuple[dict[str, Any], ...]


class AgentLactationReadService:
    """Product-owned projection for the Runtime's lactation read tools."""

    def __init__(
        self,
        *,
        records_service: Any,
        baby_repository: BabyRecordRepository,
    ) -> None:
        self.records_service = records_service
        self.baby_repository = baby_repository

    async def read_milk_analysis_snapshot(
        self,
        *,
        owner_user_id: UUID,
        as_of_date: date,
        timezone_name: str,
        days: int,
        detail_limit: int,
    ) -> AgentMilkAnalysisSnapshot:
        if days < 1 or days > 30:
            raise ApiError(
                code="validation_failed",
                message="days must be between 1 and 30.",
                status=422,
            )
        if detail_limit < 1 or detail_limit > 20:
            raise ApiError(
                code="validation_failed",
                message="limit must be between 1 and 20.",
                status=422,
            )
        local_timezone = _timezone(timezone_name)
        first_day = as_of_date - timedelta(days=days - 1)
        start_at = datetime.combine(
            first_day,
            time.min,
            tzinfo=local_timezone,
        ).astimezone(timezone.utc)
        end_at = datetime.combine(
            as_of_date + timedelta(days=1),
            time.min,
            tzinfo=local_timezone,
        ).astimezone(timezone.utc)

        feedings = await self.records_service.list_feedings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=100,
        )
        pumpings = await self.records_service.list_pumpings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=100,
        )
        growth = await self.records_service.list_growth(
            owner_user_id=owner_user_id,
            limit=detail_limit,
        )
        trends = await self.records_service.get_milk_trends(
            owner_user_id=owner_user_id,
            start_date=first_day,
            days=days,
            include_today=True,
        )
        infant_count = await self.baby_repository.profile_count(
            owner_user_id=owner_user_id
        )
        trend_items = [
            MilkAnalysisTrendDay.model_validate(item, from_attributes=True)
            for item in trends.items
        ]
        status, counts, volumes, latest, flags = _milk_status(
            days=days,
            infant_count=infant_count,
            feedings=feedings,
            pumpings=pumpings,
            trend_items=trend_items,
        )
        pumping_payloads = [
            MilkAnalysisPumpingRecord.model_validate(
                record,
                from_attributes=True,
            )
            for record in pumpings
        ]
        return AgentMilkAnalysisSnapshot(
            as_of_date=as_of_date,
            timezone=local_timezone.key,
            detail_level="detailed",
            window=MilkAnalysisWindow(
                days=days,
                limit=detail_limit,
                include_today=True,
            ),
            status=status,
            counts=counts.model_copy(update={"recent_growth": len(growth)}),
            volumes=volumes,
            latest=latest,
            observation_flags=flags,
            recent_feedings=[
                MilkAnalysisFeedingRecord.model_validate(
                    record,
                    from_attributes=True,
                )
                for record in feedings[:detail_limit]
            ],
            recent_pumpings=pumping_payloads[:detail_limit],
            pumping_rhythm=_pumping_rhythm(
                pumpings=pumpings,
                local_timezone=local_timezone,
            ),
            recent_growth=[
                MilkAnalysisGrowthRecord.model_validate(
                    record,
                    from_attributes=True,
                )
                for record in growth
            ],
            pumping_trends=trend_items,
            analysis=_milk_interpretation(
                status=status,
                flags=flags,
                has_recent_growth=bool(growth),
            ),
        )


class AgentLactationRecordWriteService:
    """Product-owned, action-bound mutation boundary for lactation records."""

    def __init__(
        self,
        *,
        records_service: Any,
        idempotency_service: Any,
        audit_service: Any | None = None,
    ) -> None:
        self.records_service = records_service
        self.idempotency_service = idempotency_service
        self.audit_service = audit_service

    async def apply_idempotent(
        self,
        *,
        owner_user_id: UUID,
        payload: AgentLactationRecordApplyPayload | dict[str, Any],
        idempotency_key: str,
        action_id: UUID,
        run_id: UUID,
        actor_service: str,
        request_id: str,
    ) -> AgentLactationRecordWriteResult:
        command = (
            payload
            if isinstance(payload, AgentLactationRecordApplyPayload)
            else AgentLactationRecordApplyPayload.model_validate(payload)
        )
        expected_key = f"agent-action:{action_id}"
        if idempotency_key != expected_key:
            raise ApiError(
                code="validation_failed",
                message="Idempotency-Key must be bound to action_id.",
                status=422,
            )
        normalized_actor_service = actor_service.strip()
        if not normalized_actor_service:
            raise ApiError(
                code="validation_failed",
                message="Service actor is required.",
                status=422,
            )

        action_type = _action_type(command)
        decision = await self.idempotency_service.reserve(
            actor_user_id=owner_user_id,
            scope=AGENT_LACTATION_RECORD_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            request_hash=request_hash(
                {
                    "actor": {
                        "type": "service",
                        "service": normalized_actor_service,
                        "user_id": str(owner_user_id),
                    },
                    "action_id": str(action_id),
                    "run_id": str(run_id),
                    "action_type": action_type,
                    "payload": command.model_dump(
                        mode="json",
                        exclude_unset=True,
                    ),
                }
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        if decision.status == "replay":
            return _replay_result(decision.record.response_ref)

        resource_id = await self._apply(
            owner_user_id=owner_user_id,
            command=command,
        )
        resource_type = f"{command.item_type}_record"
        operation = _APPLIED_OPERATIONS[command.operation]
        details = {
            "operation": operation,
            "item_type": command.item_type,
            "fields": _changed_fields(command),
        }
        application_events = (
            {
                "type": LACTATION_TIMELINE_CHANGED_EVENT,
                "payload": {
                    "operation": operation,
                    "item_type": command.item_type,
                    "record_id": resource_id,
                    "source": "agent_action",
                },
            },
        )
        result = AgentLactationRecordWriteResult(
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            application_events=application_events,
        )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=None,
                actor_type="service",
                actor_service=normalized_actor_service,
                action=action_type,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                details={
                    **details,
                    "owner_user_id": str(owner_user_id),
                    "action_id": str(action_id),
                    "run_id": str(run_id),
                },
            )
        await self.idempotency_service.mark_completed(
            record=decision.record,
            response_ref=_response_ref(result),
        )
        return result

    async def _apply(
        self,
        *,
        owner_user_id: UUID,
        command: AgentLactationRecordApplyPayload,
    ) -> str:
        if command.operation == "delete":
            assert command.record_id is not None
            delete = getattr(
                self.records_service,
                f"delete_{command.item_type}",
            )
            await delete(
                owner_user_id=owner_user_id,
                record_id=command.record_id,
                request_id="",
            )
            return str(command.record_id)

        values = command.model_dump(exclude_unset=True)
        values.pop("operation", None)
        values.pop("item_type", None)
        values.pop("reason", None)
        record_id = values.pop("record_id", None)
        _map_timeline_fields(values=values, item_type=command.item_type)

        if command.operation == "create":
            create = getattr(
                self.records_service,
                f"create_{command.item_type}",
            )
            if command.item_type in {"feeding", "growth"}:
                values.setdefault("infant_id", None)
            if command.item_type == "pumping":
                values["source"] = str(values.get("source") or "agent").strip()
            for field in ("title", "feed_type", "feed_action", "pump_type"):
                if field in values:
                    values[field] = str(values[field] or "").strip()
            record = await create(
                owner_user_id=owner_user_id,
                request_id="",
                **values,
            )
            return str(record.id)

        assert record_id is not None
        update = getattr(
            self.records_service,
            f"update_{command.item_type}",
        )
        for field in ("title", "feed_type", "feed_action", "pump_type", "source"):
            if field in values and values[field] is not None:
                values[field] = str(values[field]).strip()
        record = await update(
            owner_user_id=owner_user_id,
            record_id=record_id,
            updates=values,
            request_id="",
        )
        return str(record.id)


def _milk_status(
    *,
    days: int,
    infant_count: int,
    feedings: list[Any],
    pumpings: list[Any],
    trend_items: list[MilkAnalysisTrendDay],
) -> tuple[
    MilkAnalysisStatus,
    MilkAnalysisCounts,
    MilkAnalysisVolumes,
    MilkAnalysisLatestEvents,
    list[str],
]:
    trend_pumped_volume = round(
        sum(item.pumped_milk_volume_ml for item in trend_items),
        2,
    )
    trend_pumping_count = sum(item.pumping_count for item in trend_items)
    days_with_pumping = sum(item.pumping_count > 0 for item in trend_items)
    flags: list[str] = []
    if infant_count == 0:
        flags.append("no_infant_profile")
    if not feedings:
        flags.append("no_recent_feeding_records")
    if not pumpings:
        flags.append("no_recent_pumping_records")
    if trend_pumping_count == 0:
        flags.append("no_pumping_trend_data")
    return (
        MilkAnalysisStatus(
            data_coverage=_data_coverage(
                has_feedings=bool(feedings),
                has_pumpings=bool(pumpings),
                days=days,
                days_with_pumping=days_with_pumping,
            ),
            pumping_trend=_trend_direction(trend_items),
            measured_only=True,
        ),
        MilkAnalysisCounts(
            infants=infant_count,
            recent_feedings=len(feedings),
            recent_pumpings=len(pumpings),
            trend_days=len(trend_items),
            days_with_pumping=days_with_pumping,
            trend_pumping_count=trend_pumping_count,
        ),
        MilkAnalysisVolumes(
            recent_feeding_volume_ml=_sum(feedings, "volume_ml"),
            recent_pumped_volume_ml=_sum(pumpings, "milk_volume_ml"),
            trend_pumped_volume_ml=trend_pumped_volume,
            average_daily_pumped_volume_ml=round(
                trend_pumped_volume / days,
                2,
            ),
        ),
        MilkAnalysisLatestEvents(
            feeding_at=feedings[0].feed_time if feedings else None,
            pumping_at=pumpings[0].pump_start_time if pumpings else None,
        ),
        flags,
    )


def _data_coverage(
    *,
    has_feedings: bool,
    has_pumpings: bool,
    days: int,
    days_with_pumping: int,
) -> str:
    if not has_feedings and not has_pumpings and days_with_pumping == 0:
        return "no_recent_data"
    if has_feedings and has_pumpings and days_with_pumping >= min(days, 2):
        return "ready"
    return "limited"


def _trend_direction(trend_items: list[MilkAnalysisTrendDay]) -> str:
    volumes = [
        item.pumped_milk_volume_ml
        for item in trend_items
        if item.pumping_count > 0
    ]
    if len(volumes) < 2:
        return "insufficient_data"
    delta = volumes[-1] - volumes[0]
    if abs(delta) < 30:
        return "stable"
    return "increasing" if delta > 0 else "decreasing"


def _milk_interpretation(
    *,
    status: MilkAnalysisStatus,
    flags: list[str],
    has_recent_growth: bool,
) -> MilkAnalysisInterpretation:
    if "no_infant_profile" in flags:
        pathway = "补充宝宝资料后再判断供需"
    elif status.data_coverage == "no_recent_data":
        pathway = "先补近期记录"
    elif status.pumping_trend == "decreasing":
        pathway = "评估是否需要追奶或排乳节奏调整"
    elif status.pumping_trend == "increasing":
        pathway = "观察是否需要稳奶或减奶"
    elif status.data_coverage == "ready":
        pathway = "可以进入追奶/稳奶/减奶方向判断"
    else:
        pathway = "继续补齐关键记录后再判断"
    if "no_infant_profile" in flags:
        next_step = "先确认宝宝资料或体重/尿布等摄入信号。"
    elif status.data_coverage == "no_recent_data":
        next_step = "先补一条近期喂养或吸奶记录。"
    elif status.data_coverage == "limited":
        next_step = "只追问当前最影响判断的一项缺失信息。"
    elif status.pumping_trend in {"decreasing", "increasing"}:
        next_step = "结合宝宝状态和妈妈乳房/全身状态判断是否进入计划。"
    else:
        next_step = "给出简短结论，并询问是否开始制定计划。"
    return MilkAnalysisInterpretation(
        pathway=pathway,
        data_coverage=status.data_coverage,
        pumping_trend=status.pumping_trend,
        has_recent_growth=has_recent_growth,
        missing_inputs=flags,
        recommended_next_step=next_step,
    )


def _pumping_rhythm(
    *,
    pumpings: list[Any],
    local_timezone: ZoneInfo,
) -> MilkAnalysisPumpingRhythm:
    values_by_date: dict[date, list[str]] = {}
    for pumping in pumpings:
        value = pumping.pump_start_time
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        local_value = value.astimezone(local_timezone)
        values_by_date.setdefault(local_value.date(), []).append(
            local_value.strftime("%H:%M")
        )
    if not values_by_date:
        return MilkAnalysisPumpingRhythm(
            timezone=local_timezone.key,
            representative_date=None,
            representative_times=[],
        )
    representative_date = max(
        values_by_date,
        key=lambda value: (len(values_by_date[value]), value),
    )
    return MilkAnalysisPumpingRhythm(
        timezone=local_timezone.key,
        representative_date=representative_date,
        representative_times=sorted(
            set(values_by_date[representative_date])
        )[:10],
    )


def _timezone(value: str) -> ZoneInfo:
    normalized = value.strip() or "UTC"
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ApiError(
            code="validation_failed",
            message="A valid IANA timezone is required.",
            status=422,
        ) from exc


def _sum(records: list[Any], field: str) -> float:
    return round(
        sum(float(getattr(record, field, 0) or 0) for record in records),
        2,
    )


def _action_type(command: AgentLactationRecordApplyPayload) -> str:
    return (
        f"records.{command.item_type}_record."
        f"{command.operation}"
    )


def _map_timeline_fields(
    *,
    values: dict[str, Any],
    item_type: str,
) -> None:
    occurred_at = values.pop("occurred_at", None)
    if occurred_at is not None:
        values[
            {
                "feeding": "feed_time",
                "pumping": "pump_start_time",
                "growth": "measured_at",
            }[item_type]
        ] = occurred_at
    if "ended_at" in values:
        values["pump_end_time"] = values.pop("ended_at")


def _changed_fields(command: AgentLactationRecordApplyPayload) -> list[str]:
    return sorted(
        set(command.model_fields_set)
        - {"operation", "item_type", "record_id", "reason"}
    )


def _response_ref(result: AgentLactationRecordWriteResult) -> str:
    return json.dumps(
        {
            "resource_type": result.resource_type,
            "resource_id": result.resource_id,
            "details": result.details,
            "application_events": list(result.application_events),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _replay_result(response_ref: str) -> AgentLactationRecordWriteResult:
    if not response_ref:
        raise ApiError(
            code="idempotency_in_progress",
            message="Lactation record action is still in progress.",
            status=409,
        )
    try:
        parsed = json.loads(response_ref)
        resource_type = str(parsed["resource_type"])
        if resource_type not in {
            "feeding_record",
            "pumping_record",
            "growth_record",
        }:
            raise ValueError("invalid resource_type")
        resource_id = str(parsed["resource_id"])
        UUID(resource_id)
        details = dict(parsed["details"])
        application_events = tuple(
            dict(event) for event in parsed["application_events"]
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ApiError(
            code="conflict",
            message="Lactation record action replay identity is invalid.",
            status=409,
        ) from exc
    return AgentLactationRecordWriteResult(
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        application_events=application_events,
    )
