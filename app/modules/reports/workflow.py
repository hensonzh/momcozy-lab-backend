from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from hashlib import sha256
import json
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...core.errors import ApiError
from ...infrastructure.care_report_sources_contract import ReportSourcesRead
from ...infrastructure.care_report_contract import REPORT_PROMPT_VERSION, REPORT_SCHEMA_VERSION
from ...infrastructure.care_reports import CareReportGateway
from ..appointments.service import utc_now
from ..audit.repository import AuditRepository
from ..audit.service import AuditService
from ..auth import CurrentUser
from ..care.events import record_care_event
from .access import report_access
from .models import CareReport, CareReportReview
from .repository import latest_report, latest_review, report_read
from .schemas import ReportHistoryRead, ReportIndexItem, ReportPurpose, ReportRead, ReportRequest, ReportStateRead, ReviewWrite
from .sources import assemble_snapshot, prepare_sources


def require_expert(actor: CurrentUser) -> None:
    if 'ibclc' not in actor.roles:
        raise ApiError(code='permission_denied', message='Specialist workbench access is required.', status=403)


class CareReportWorkflow:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], gateway: CareReportGateway, *, now: Callable[[], datetime] = utc_now) -> None:
        self.sessions, self.gateway, self.now = sessions, gateway, now

    async def request(self, actor: CurrentUser, episode_id: UUID, body: ReportRequest, request_id: str) -> ReportStateRead:
        require_expert(actor)
        return await self.enqueue(actor.user_id, episode_id, body, request_id)

    async def enqueue(self, provider_id: UUID, episode_id: UUID, body: ReportRequest, request_id: str, *, retry_failed: bool = True) -> ReportStateRead:
        # Do not keep database transactions or care locks open during Runtime calls.
        async with self.sessions.begin() as session:
            access = await report_access(session, provider_id, episode_id)
            cutoff = self.now()
            if access.episode.status not in {'active', 'completed'}:
                raise ApiError(code='care_report_service_unavailable', message='This service cannot generate reports in its current state.', status=409)
            day = body.report_date or cutoff.astimezone(ZoneInfo(access.timezone)).date()
            prepared = await prepare_sources(session, access, body.purpose, day, cutoff)
        dialogues = await self.gateway.sources(prepared.query) if prepared.query.threads else ReportSourcesRead(items=[], available_count=0, omitted_count=0, as_of=cutoff)
        snapshot = assemble_snapshot(prepared, dialogues)
        fingerprint = snapshot.input.model_dump(mode='json', exclude={'as_of'}) if snapshot else {'waiting': True, 'day': str(day), 'timezone': prepared.timezone, 'purpose': body.purpose}
        fingerprint['generation_contract'] = {'prompt_version': REPORT_PROMPT_VERSION, 'schema_version': REPORT_SCHEMA_VERSION}
        source_hash = sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        async with self.sessions.begin() as session:
            after = await report_access(session, provider_id, episode_id, lock=True)
            if after.authority != prepared.authority or after.timezone != prepared.timezone or after.episode.status not in {'active', 'completed'}:
                raise ApiError(code='care_report_authorization_changed', message='Report authorization changed. Refresh the service.', status=409)
            latest = await latest_report(session, episode_id, body.purpose, day)
            if latest is not None and latest.source_hash == source_hash and latest.provider_id == provider_id and (
                latest.case_consent_version, latest.ai_consent_version) == (after.case_version, after.ai_version) and (
                    latest.status not in {'failed', 'cancelled'} or (latest.status == 'failed' and not retry_failed)):
                return await self._state(session, latest, after.authority)
            pending = await session.scalars(select(CareReport).where(CareReport.episode_id == episode_id, CareReport.purpose == body.purpose,
                CareReport.report_date == day, CareReport.status.in_(['queued', 'running'])).with_for_update())
            for previous in pending:
                previous.status, previous.lease_token, previous.lease_until, previous.error_code = 'cancelled', None, None, 'care_report_superseded'
            value = CareReport(episode_id=episode_id, provider_id=provider_id, purpose=body.purpose, report_date=day, timezone=prepared.timezone,
                version=(latest.version if latest else 0) + 1, case_consent_version=after.case_version, ai_consent_version=after.ai_version,
                source_hash=source_hash, input_hash=snapshot.input.input_hash() if snapshot else source_hash,
                snapshot=snapshot.model_dump(mode='json') if snapshot else None, status='queued' if snapshot else 'waiting_for_record',
                available_at=self.now(), created_at=self.now())
            session.add(value)
            await session.flush()
            await AuditService(repository=AuditRepository(session)).record(actor_user_id=provider_id, action='care.report.requested',
                resource_type='care_report', resource_id=str(value.id), request_id=request_id,
                details={'episode_id': str(episode_id), 'version': value.version, 'status': value.status, 'source_hash': source_hash})
            return await self._state(session, value, after.authority)

    async def read(self, actor: CurrentUser, episode_id: UUID, purpose: ReportPurpose, day: date | None, request_id: str) -> ReportStateRead:
        require_expert(actor)
        async with self.sessions.begin() as session:
            access = await report_access(session, actor.user_id, episode_id, lock=True)
            selected = day or self.now().astimezone(ZoneInfo(access.timezone)).date()
            value = await latest_report(session, episode_id, purpose, selected)
            await AuditService(repository=AuditRepository(session)).record(actor_user_id=actor.user_id, action='care.report.viewed',
                resource_type='care_episode', resource_id=str(episode_id), request_id=request_id, details={'purpose': purpose, 'date': str(selected)})
            if value is None:
                return ReportStateRead(report_date=selected, timezone=access.timezone, purpose=purpose, state='not_generated', report=None, server_time=self.now())
            return await self._state(session, value, access.authority)

    async def review(self, actor: CurrentUser, report_id: UUID, body: ReviewWrite, request_id: str) -> ReportRead:
        require_expert(actor)
        async with self.sessions.begin() as session:
            value = await session.get(CareReport, report_id)
            if value is None:
                raise ApiError(code='not_found', message='Report not found.', status=404)
            access = await report_access(session, actor.user_id, value.episode_id, lock=True)
            value = await session.scalar(select(CareReport).where(CareReport.id == report_id).with_for_update().execution_options(populate_existing=True))
            assert value is not None
            latest = await latest_report(session, value.episode_id, value.purpose, value.report_date)
            if latest is None or latest.id != report_id or value.status != 'ready' or value.provider_id != actor.user_id or (
                value.case_consent_version, value.ai_consent_version) != (access.case_version, access.ai_version):
                raise ApiError(code='care_report_changed', message='Generate and review the latest authorized report.', status=409)
            previous = await latest_review(session, report_id)
            version = previous.version if previous else 0
            # An exact retry is safe; a different stale edit is rejected.
            if previous and previous.version == body.expected_version + 1 and previous.provider_id == actor.user_id and (
                previous.decision, previous.feedback) == (body.decision, body.feedback):
                return await report_read(session, value, reviewable=True)
            if body.expected_version != version:
                raise ApiError(code='care_report_review_changed', message='The review changed. Reload before submitting.', status=409)
            review = CareReportReview(report_id=report_id, provider_id=actor.user_id, version=version + 1,
                decision=body.decision, feedback=body.feedback, created_at=self.now())
            session.add(review)
            await session.flush()
            await record_care_event(session, episode_id=value.episode_id, kind='report_reviewed', aggregate_id=report_id,
                aggregate_version=review.version, actor_user_id=actor.user_id, recipient_id=None, occurred_at=review.created_at)
            await AuditService(repository=AuditRepository(session)).record(actor_user_id=actor.user_id, action='care.report.reviewed',
                resource_type='care_report', resource_id=str(report_id), request_id=request_id,
                details={'review_version': review.version, 'decision': review.decision})
            return await report_read(session, value, reviewable=True)

    async def history(self, actor: CurrentUser, episode_id: UUID, purpose: ReportPurpose, request_id: str) -> ReportHistoryRead:
        require_expert(actor)
        async with self.sessions.begin() as session:
            await report_access(session, actor.user_id, episode_id, lock=True)
            values = list(await session.scalars(select(CareReport).where(CareReport.episode_id == episode_id, CareReport.purpose == purpose)
                .distinct(CareReport.report_date).order_by(CareReport.report_date.desc(), CareReport.version.desc()).limit(31)))
            items = []
            for value in values:
                review = await latest_review(session, value.id)
                items.append(ReportIndexItem.model_validate({'id': value.id, 'report_date': value.report_date, 'timezone': value.timezone,
                    'version': value.version, 'status': value.status, 'review_decision': review.decision if review else None, 'generated_at': value.generated_at}))
            await AuditService(repository=AuditRepository(session)).record(actor_user_id=actor.user_id, action='care.report.history_viewed',
                resource_type='care_episode', resource_id=str(episode_id), request_id=request_id, details={'purpose': purpose, 'result_count': len(items)})
            return ReportHistoryRead(items=items, server_time=self.now())

    async def _state(self, session: AsyncSession, value: CareReport, authority: tuple[UUID | None, int, int]) -> ReportStateRead:
        report = await report_read(session, value, reviewable=value.status == 'ready' and authority == (
            value.provider_id, value.case_consent_version, value.ai_consent_version))
        return ReportStateRead(report_date=report.report_date, timezone=report.timezone, purpose=report.purpose,
            state=report.status, report=report, server_time=self.now())
