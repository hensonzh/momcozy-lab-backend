"""Owner-scoped durable stool-analysis result and event ledger."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ..baby.profile_models import BabyProfile
from ..users.models import User
from .models import StoolAnalysis, StoolAnalysisEvent, StoolAnalysisPhoto
from .transport_contract import SpecialtyTransportEvent, StoolAnalysisResult, result_field_value


def _unavailable() -> ApiError:
    return ApiError(code="stool_analysis_context_unavailable", message="Analysis context is unavailable.", status=409)


def _conflict() -> ApiError:
    return ApiError(code="stool_analysis_event_conflict", message="Analysis event conflicts with current state.", status=409)


class StoolAnalysisEventLedger:
    """All result validation and state changes occur in the caller's DB transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _analysis(self, *, owner: UUID, analysis_id: UUID, lock: bool) -> StoolAnalysis:
        statement = (select(StoolAnalysis)
            .join(User, User.id == StoolAnalysis.owner_user_id)
            .join(BabyProfile, BabyProfile.id == StoolAnalysis.baby_id)
            .where(StoolAnalysis.id == analysis_id, StoolAnalysis.owner_user_id == owner,
                   User.status == "active", User.deleted_at.is_(None),
                   BabyProfile.owner_user_id == owner, BabyProfile.deleted_at.is_(None)))
        if lock:
            statement = statement.with_for_update(of=StoolAnalysis)
        analysis = await self.session.scalar(statement)
        if analysis is None or analysis.revoked_at is not None or analysis.deleted_at is not None:
            raise _unavailable()
        return analysis

    async def get_analysis(self, *, owner: UUID, analysis_id: UUID) -> StoolAnalysis:
        return await self._analysis(owner=owner, analysis_id=analysis_id, lock=False)

    async def list_for_thread(self, *, owner: UUID, thread_id: UUID,
                              cursor: UUID | None = None, limit: int = 50) -> tuple[list[StoolAnalysis], UUID | None]:
        if not 1 <= limit <= 50:
            raise ValueError("Invalid analysis page limit")
        pivot = await self._analysis(owner=owner, analysis_id=cursor, lock=False) if cursor else None
        if pivot is not None and pivot.thread_id != thread_id:
            raise _unavailable()
        statement = (select(StoolAnalysis)
            .join(User, User.id == StoolAnalysis.owner_user_id)
            .join(BabyProfile, BabyProfile.id == StoolAnalysis.baby_id)
            .where(StoolAnalysis.owner_user_id == owner, StoolAnalysis.thread_id == thread_id,
                   StoolAnalysis.revoked_at.is_(None), StoolAnalysis.deleted_at.is_(None),
                   User.status == "active", User.deleted_at.is_(None),
                   BabyProfile.owner_user_id == owner, BabyProfile.deleted_at.is_(None)))
        if pivot is not None:
            statement = statement.where(or_(
                StoolAnalysis.created_at < pivot.created_at,
                and_(StoolAnalysis.created_at == pivot.created_at, StoolAnalysis.id < pivot.id),
            ))
        rows = list((await self.session.scalars(statement.order_by(
            StoolAnalysis.created_at.desc(), StoolAnalysis.id.desc(),
        ).limit(limit + 1))).all())
        return rows[:limit], rows[limit - 1].id if len(rows) > limit else None

    async def append(self, *, owner: UUID, event: SpecialtyTransportEvent,
                     now: datetime | None = None) -> StoolAnalysisEvent:
        analysis = await self._analysis(owner=owner, analysis_id=event.analysis_id, lock=True)
        existing = await self.session.scalar(select(StoolAnalysisEvent).where(
            StoolAnalysisEvent.analysis_id == event.analysis_id,
            StoolAnalysisEvent.sequence == event.sequence,
        ))
        payload = dict(event.payload) if event.payload is not None else None
        if existing is not None:
            if (existing.event_type == event.event_type and existing.schema_version == event.schema_version
                    and existing.payload == payload):
                return existing
            raise _conflict()
        if analysis.status not in ("queued", "running"):
            raise _conflict()
        last = await self.session.scalar(select(StoolAnalysisEvent).where(
            StoolAnalysisEvent.analysis_id == event.analysis_id,
        ).order_by(StoolAnalysisEvent.sequence.desc()).limit(1))
        if event.sequence != (last.sequence + 1 if last is not None else 1):
            raise _conflict()
        allowed = {
            None: {"stool.card.created"},
            "stool.card.created": {"stool.analysis.started", "stool.analysis.failed", "stool.analysis.cancelled"},
            "stool.analysis.started": {"stool.field.ready", "stool.analysis.failed", "stool.analysis.cancelled"},
            "stool.field.ready": {"stool.field.ready", "stool.analysis.completed", "stool.analysis.failed", "stool.analysis.cancelled"},
        }
        if event.event_type not in allowed.get(last.event_type if last else None, set()):
            raise _conflict()

        validated_result: StoolAnalysisResult | None = None
        if event.event_type == "stool.analysis.completed":
            try:
                validated_result = StoolAnalysisResult.model_validate((payload or {})["result"])
            except Exception as exc:
                raise _conflict() from exc
            fields = list((await self.session.scalars(select(StoolAnalysisEvent).where(
                StoolAnalysisEvent.analysis_id == event.analysis_id,
                StoolAnalysisEvent.event_type == "stool.field.ready",
            ).order_by(StoolAnalysisEvent.sequence))).all())
            projected = {
                row.payload["field"]: row.payload["value"]
                for row in fields if isinstance(row.payload, dict)
            }
            if set(projected) != {"color", "consistency", "findings", "risk_level", "summary", "care_advice"}:
                raise _conflict()
            if any(projected[field] != result_field_value(validated_result, field) for field in projected):
                raise _conflict()
            photo_count = len(list((await self.session.scalars(select(StoolAnalysisPhoto).where(
                StoolAnalysisPhoto.analysis_id == event.analysis_id,
            ))).all()))
            if photo_count != len(validated_result.photo_observations):
                raise _conflict()
            if (validated_result.color == "black" and analysis.baby_birth_date is not None
                    and analysis.context_captured_at is not None):
                captured = analysis.context_captured_at.date()
                if ((captured - analysis.baby_birth_date).days > 3
                        and "suspected_tarry_black_after_meconium" not in validated_result.findings):
                    raise _conflict()

        entry = StoolAnalysisEvent(
            analysis_id=event.analysis_id, sequence=event.sequence,
            schema_version=event.schema_version, event_type=event.event_type, payload=payload,
        )
        self.session.add(entry)
        at = now or datetime.now(timezone.utc)
        if event.event_type == "stool.analysis.started":
            analysis.status = "running"
        elif event.event_type == "stool.analysis.completed" and validated_result is not None:
            analysis.status = "completed"
            analysis.result_schema_version = validated_result.schema_version
            analysis.final_result = validated_result.model_dump(mode="json")
            analysis.result_validated_at = at
            analysis.completed_at = at
            analysis.run_lease_until = None
            analysis.context_expires_at = None  # Per-call authorization and deletion, not a fixed follow-up TTL.
        elif event.event_type == "stool.analysis.failed":
            analysis.status = "failed"
            analysis.run_lease_until = None
        elif event.event_type == "stool.analysis.cancelled":
            analysis.status = "cancelled"
            analysis.run_lease_until = None
        await self.session.flush()
        return entry

    async def list_after(self, *, owner: UUID, analysis_id: UUID, after: int,
                         limit: int = 50) -> list[StoolAnalysisEvent]:
        if after < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid event cursor or limit")
        await self._analysis(owner=owner, analysis_id=analysis_id, lock=False)
        return list((await self.session.scalars(select(StoolAnalysisEvent).where(
            StoolAnalysisEvent.analysis_id == analysis_id, StoolAnalysisEvent.sequence > after,
        ).order_by(StoolAnalysisEvent.sequence).limit(limit))).all())


# Compatibility for existing imports while callers migrate to the accurate name.
StoolNonmedicalEventLedger = StoolAnalysisEventLedger
