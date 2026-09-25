from __future__ import annotations

from ..baby.profile_models import BabyProfile

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ...infrastructure.care_report_contract import ReportGenerationInput, ReportSource
from ...infrastructure.care_report_sources_contract import ReportSourceQuery, ReportSourcesRead, ReportThreadSource
from ..consultations.models import CareConsentRevision, CareIntakeRevision
from ..consultations.room_models import CareConsultation
from ..documentation.models import CarePlanDraft, CarePlanPublication
from ..lactation.models import LactationRecord
from ..baby.models import BabyRecord
from .baby_source_text import baby_record_text
from .access import ReportAccess
from .models import CareConversationLink
from .schemas import ReportPurpose, ReportSnapshot
from .sharing import sharing_windows
from .source_text import intake_text, lactation_text, plan_text


@dataclass(frozen=True)
class PreparedSources:
    authority: tuple[UUID | None, int, int]
    episode_id: UUID
    purpose: ReportPurpose
    day: date
    timezone: str
    cutoff: datetime
    query: ReportSourceQuery
    sources: list[ReportSource]
    available_count: int


def source_text(value: Any, *, budget: int = 12000) -> tuple[str, bool]:
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=lambda value: value.isoformat(), separators=(',', ':'))
    encoded = raw.encode('utf-8')
    return encoded[:budget].decode('utf-8', errors='ignore'), len(encoded) > budget


async def prepare_sources(session: AsyncSession, access: ReportAccess, purpose: ReportPurpose, day: date, cutoff: datetime) -> PreparedSources:
    zone, episode = ZoneInfo(access.timezone), access.episode
    today = cutoff.astimezone(zone).date()
    earliest = episode.created_at.astimezone(zone).date()
    if day > today or day < earliest:
        raise ApiError(code='care_report_date_invalid', message='Choose a date within this service history.', status=422)
    start_day = max(earliest, day - timedelta(days=6)) if purpose == 'preparation' else day
    start = datetime.combine(start_day, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    consents = list(await session.scalars(select(CareConsentRevision).where(CareConsentRevision.episode_id == episode.id)))
    links = list(await session.scalars(select(CareConversationLink).where(CareConversationLink.episode_id == episode.id)
        .order_by(CareConversationLink.shared_since.desc(), CareConversationLink.thread_id)))
    intervals = [ReportThreadSource(thread_id=link.thread_id, shared_since=window.start, shared_until=window.end)
        for link in links[:50] for window in sharing_windows(consents, linked_at=link.shared_since)
        if window.start <= cutoff and window.start < end and (window.end is None or window.end > start)]
    intervals.sort(key=lambda value: value.shared_since, reverse=True)
    if len(links) > 50 or len(intervals) > 100:
        # Never silently claim coverage when the association/consent history exceeds the read contract.
        raise ApiError(code='care_report_source_limit', message='The conversation sharing history exceeds the report window limit.', status=422)
    sources: list[ReportSource] = []
    available = 0
    lactation_query = select(LactationRecord).where(LactationRecord.owner_user_id == episode.owner_user_id,
        LactationRecord.occurred_at >= start, LactationRecord.occurred_at < end,
        LactationRecord.occurred_at <= cutoff, LactationRecord.updated_at <= cutoff, LactationRecord.deleted_at.is_(None))
    available += int(await session.scalar(select(func.count()).select_from(lactation_query.subquery())) or 0)
    for lactation in await session.scalars(lactation_query.order_by(LactationRecord.occurred_at.desc(), LactationRecord.id).limit(80)):
        content, truncated = source_text(lactation_text(lactation))
        sources.append(ReportSource(id=f'lactation:{lactation.id}:{lactation.version}', kind='lactation', recorded_at=lactation.occurred_at, content=content, truncated=truncated))
    if episode.baby_id is not None:
        baby_query = select(BabyRecord).join(BabyProfile, BabyProfile.id == BabyRecord.baby_id).where(
            BabyRecord.owner_user_id == episode.owner_user_id, BabyRecord.baby_id == episode.baby_id,
            BabyProfile.owner_user_id == episode.owner_user_id, BabyProfile.deleted_at.is_(None),
            BabyRecord.deleted_at.is_(None), BabyRecord.updated_at <= cutoff,
            or_(and_(BabyRecord.recorded_on >= start_day, BabyRecord.recorded_on <= day),
                and_(BabyRecord.occurred_at <= cutoff, BabyRecord.occurred_at < end,
                    or_(BabyRecord.occurred_at >= start, and_(BabyRecord.kind == 'sleep', or_(BabyRecord.ended_at.is_(None), BabyRecord.ended_at > start))))))
        available += int(await session.scalar(select(func.count()).select_from(baby_query.subquery())) or 0)
        for record in await session.scalars(baby_query.order_by(func.coalesce(BabyRecord.occurred_at, BabyRecord.updated_at).desc(), BabyRecord.id).limit(80)):
            content, truncated = source_text(baby_record_text(record, access.timezone))
            sources.append(ReportSource(id=f'baby_record:{record.id}:{record.version}', kind='baby_record', recorded_at=record.occurred_at or record.updated_at, content=content, truncated=truncated))
    intake = await session.scalar(select(CareIntakeRevision).where(CareIntakeRevision.episode_id == episode.id,
        CareIntakeRevision.submitted_at <= cutoff, CareIntakeRevision.submitted_at < end)
        .order_by(CareIntakeRevision.submitted_at.desc(), CareIntakeRevision.version.desc(), CareIntakeRevision.id).limit(1))
    if intake is not None:
        content, truncated = source_text(intake_text(intake))
        sources.append(ReportSource(id=f'intake:{intake.id}:{intake.version}', kind='intake', recorded_at=intake.submitted_at, content=content, truncated=truncated))
        available += 1
    publication = await session.scalar(select(CarePlanPublication).join(CarePlanDraft, CarePlanDraft.id == CarePlanPublication.plan_id)
        .join(CareConsultation, CareConsultation.id == CarePlanDraft.consultation_id).where(CareConsultation.episode_id == episode.id,
            CarePlanPublication.published_at <= cutoff, CarePlanPublication.published_at < end)
        .order_by(CarePlanPublication.published_at.desc(), CarePlanPublication.id).limit(1))
    if publication is not None:
        content, truncated = source_text(plan_text(publication.content))
        sources.append(ReportSource(id=f'care_plan:{publication.id}:{publication.revision}', kind='care_plan', recorded_at=publication.published_at, content=content, truncated=truncated))
        available += 1
    return PreparedSources(access.authority, episode.id, purpose, day, access.timezone, cutoff,
        ReportSourceQuery(owner_user_id=episode.owner_user_id, threads=intervals, starts_at=start, ends_at=end, as_of=cutoff), sources, available)


def assemble_snapshot(prepared: PreparedSources, dialogues: ReportSourcesRead) -> ReportSnapshot | None:
    if dialogues.as_of != prepared.cutoff:
        raise ApiError(code='care_report_invalid_output', message='The source cutoff does not match.', status=502)
    candidates: list[ReportSource] = []
    for item in dialogues.items:
        # Product also checks identifiers and interval boundaries at the trust boundary.
        authorized = any(value.thread_id == item.thread_id and item.question_at >= value.shared_since and
            (value.shared_until is None or max(item.question_at, item.answered_at) < value.shared_until) for value in prepared.query.threads)
        if not authorized or not prepared.query.starts_at <= item.question_at < prepared.query.ends_at or item.answered_at > prepared.cutoff:
            raise ApiError(code='care_report_invalid_output', message='A conversation source is outside the authorized window.', status=502)
        content, truncated = source_text(f'Client question: {item.question}\nAI response: {item.answer}', budget=18000)
        candidates.append(ReportSource(id=f'dialogue:{item.run_id}', kind='dialogue', recorded_at=item.question_at,
            content=content, truncated=truncated or item.question_truncated or item.answer_truncated))
    candidates.extend(prepared.sources)
    if not candidates or (prepared.purpose == 'daily' and not any(value.kind in {'dialogue', 'lactation', 'baby_record'} for value in candidates)):
        return None
    included: list[ReportSource] = []
    size = 0
    for value in candidates:
        cost = len(value.model_dump_json().encode('utf-8'))
        if size + cost > 80 * 1024 or len(included) >= 120:
            continue
        included.append(value)
        size += cost
    if not included:
        return None
    total = prepared.available_count + dialogues.available_count
    request = ReportGenerationInput(episode_id=prepared.episode_id, purpose=prepared.purpose, report_date=prepared.day,
        timezone=prepared.timezone, as_of=prepared.cutoff, sources=included, omitted_count=max(0, total - len(included)))
    included_ids = {value.id for value in included}
    return ReportSnapshot(input=request, dialogues=[value for value in dialogues.items if f'dialogue:{value.run_id}' in included_ids])
