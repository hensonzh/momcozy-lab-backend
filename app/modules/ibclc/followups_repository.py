from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import aliased

from ..care.catalog import PACKAGES
from ..care.models import CareEpisode
from ..consultations.models import CareConsentRevision
from ..profiles.models import UserProfile
from ..reports.models import CareReport, CareReportReview
from .repository import CaseProjection, WorkbenchRepository, case_granted


class FollowupProjection:
    def __init__(self, case: CaseProjection, metadata: Any) -> None:
        self.case, self.metadata = case, metadata


class FollowupsRepository:
    def __init__(self, repository: WorkbenchRepository) -> None:
        self.repository, self.session = repository, repository.session

    async def list(self, provider_id: UUID, day: date, start: datetime, end: datetime, *, query: str, status: str,
        offset: int, limit: int) -> tuple[list[FollowupProjection], int, int, int]:
        def consent_value(scope: str, field: Any) -> Any:
            return select(field).where(CareConsentRevision.episode_id == CareEpisode.id, CareConsentRevision.scope == scope)
        case_version = consent_value('ibclc_case', CareConsentRevision.version).order_by(CareConsentRevision.version.desc()).limit(1).correlate(CareEpisode).scalar_subquery()
        ai_version = consent_value('ai_context', CareConsentRevision.version).order_by(CareConsentRevision.version.desc()).limit(1).correlate(CareEpisode).scalar_subquery()
        ai = func.coalesce(consent_value('ai_context', CareConsentRevision.active).order_by(CareConsentRevision.version.desc()).limit(1).correlate(CareEpisode).scalar_subquery(), False)
        granted = case_granted()
        purpose = case((CareEpisode.starts_at.is_(None), 'preparation'), else_='daily')
        report = aliased(CareReport)
        report_query = select(CareReport.id).where(CareReport.episode_id == CareEpisode.id, CareReport.report_date == day, CareReport.purpose == purpose)
        report_id = report_query.order_by(CareReport.version.desc()).limit(1).correlate(CareEpisode).scalar_subquery()
        review = select(CareReportReview.decision).where(CareReportReview.report_id == report.id).order_by(CareReportReview.version.desc()).limit(1).correlate(report).scalar_subquery()
        readable = and_(granted, ai, report.provider_id == provider_id, report.case_consent_version == case_version, report.ai_consent_version == ai_version)
        review_decision = case((readable & (report.status == 'ready'), review), else_=None)
        done = func.coalesce(and_(readable, report.status == 'ready', review.is_not(None)), False)
        state = case((~granted, 'case_consent_required'), (~ai, 'ai_consent_required'),
            (readable, report.status), else_='not_generated')
        base = select(CareEpisode.id.label('episode_id'), CareEpisode.owner_user_id.label('owner'), CareEpisode.package_id,
            case((granted, UserProfile.preferred_name), else_=None).label('name'), ai.label('ai'), purpose.label('purpose'),
            case((readable, report.id), else_=None).label('report_id'), state.label('report_status'), review_decision.label('review'), done.label('done'))
        base = base.outerjoin(UserProfile, UserProfile.user_id == CareEpisode.owner_user_id).outerjoin(report, report.id == report_id).where(
            CareEpisode.assigned_ibclc_id == provider_id, CareEpisode.owner_user_id != provider_id,
            CareEpisode.created_at < end, or_(CareEpisode.status == 'active', and_(CareEpisode.status == 'completed', CareEpisode.ends_at.is_not(None))),
            or_(CareEpisode.ends_at.is_(None), CareEpisode.ends_at > start))
        data = base.subquery()
        owners = select(data.c.owner, func.bool_and(data.c.done).label('done')).group_by(data.c.owner).subquery()
        all_count = int(await self.session.scalar(select(func.count()).select_from(owners)) or 0)
        completed = int(await self.session.scalar(select(func.count()).select_from(owners).where(owners.c.done.is_(True))) or 0)
        matching = select(owners.c.owner, owners.c.done)
        if query:
            escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            packages = [key for key, value in PACKAGES.items() if query.casefold() in f'{key} {value.name}'.casefold()]
            hits = select(data.c.owner).where(or_(data.c.name.ilike(f'%{escaped}%', escape='\\'),
                cast(data.c.owner, String).ilike(f'{escaped}%', escape='\\'), data.c.package_id.in_(packages)))
            matching = matching.where(owners.c.owner.in_(hits))
        if status != 'all':
            matching = matching.where(owners.c.done.is_(status == 'completed'))
        total = int(await self.session.scalar(select(func.count()).select_from(matching.subquery())) or 0)
        page = list(await self.session.scalars(matching.order_by(owners.c.done, owners.c.owner).offset(offset).limit(limit)))
        cases = await self.repository.cases(provider_id, page)
        # Refresh sensitive metadata after the shared case locks, just like the case projection.
        current = (await self.session.execute(base.where(CareEpisode.owner_user_id.in_(page)))).mappings()
        metadata = {value['episode_id']: value for value in current}
        return [FollowupProjection(value, metadata[value.episode.id]) for value in cases if value.episode.id in metadata], total, all_count, completed
