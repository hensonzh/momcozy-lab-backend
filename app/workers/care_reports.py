from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from uuid import UUID, uuid4
from typing import cast

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..core.errors import ApiError
from ..core.settings import Settings
from ..infrastructure.care_reports import CareReportGateway, RuntimeCareReportGateway
from ..infrastructure.db.session import create_db_engine, create_session_factory
from ..modules.appointments.service import utc_now
from ..modules.audit.repository import AuditRepository
from ..modules.audit.service import AuditService
from ..modules.care.models import CareEpisode
from ..modules.care.events import record_care_event
from ..modules.reports.access import report_access
from ..modules.reports.models import CareReport
from ..modules.reports.repository import latest_report
from ..modules.reports.schemas import ReportRequest, ReportSnapshot
from ..modules.reports.workflow import CareReportWorkflow

LOGGER = logging.getLogger('production_backend.care_reports')
MAX_ATTEMPTS = 3
LEASE_SECONDS = 180
NON_RETRYABLE = {'care_report_invalid_output', 'care_report_refused', 'care_report_incomplete', 'care_reports_unavailable'}


async def _authorized_job(session: AsyncSession, report_id: UUID, token: UUID, now: datetime) -> CareReport | None:
    record = await session.get(CareReport, report_id)
    if record is None:
        return None
    allowed = True
    try:
        access = await report_access(session, record.provider_id, record.episode_id, lock=True)
        allowed = access.authority == (record.provider_id, record.case_consent_version, record.ai_consent_version) and access.episode.status in {'active', 'completed'}
    except ApiError:
        allowed = False
    record = await session.scalar(select(CareReport).where(CareReport.id == report_id).with_for_update().execution_options(populate_existing=True))
    assert record is not None
    if record.status != 'running' or record.lease_token != token or record.lease_until is None or record.lease_until <= now:
        return None
    latest = await latest_report(session, record.episode_id, record.purpose, record.report_date)
    if not allowed or latest is None or latest.id != report_id:
        record.status, record.lease_token, record.lease_until, record.error_code = 'cancelled', None, None, 'care_report_authorization_changed' if not allowed else 'care_report_superseded'
        return None
    return cast(CareReport, record)


async def process_next(sessions: async_sessionmaker[AsyncSession], gateway: CareReportGateway, *, now: Callable[[], datetime] = utc_now) -> bool:
    async with sessions.begin() as session:
        record = await session.scalar(select(CareReport).where(or_(and_(CareReport.status == 'queued', CareReport.available_at <= now()),
            and_(CareReport.status == 'running', CareReport.lease_until <= now())))
            .order_by(CareReport.available_at, CareReport.id).limit(1).with_for_update(skip_locked=True))
        if record is None:
            return False
        if record.attempts >= MAX_ATTEMPTS:
            record.status, record.lease_token, record.lease_until, record.error_code = 'failed', None, None, 'care_report_attempts_exhausted'
            return True
        token, report_id = uuid4(), record.id
        record.status, record.lease_token, record.lease_until = 'running', token, now() + timedelta(seconds=LEASE_SECONDS)
        record.attempts += 1
    async with sessions.begin() as session:
        record = await _authorized_job(session, report_id, token, now())
        if record is None:
            return True
        snapshot = ReportSnapshot.model_validate(record.snapshot)
    result = None
    failure = None
    try:
        async with asyncio.timeout(140):
            result = await gateway.generate(snapshot.input)
        if result.input_hash != snapshot.input.input_hash():
            raise ApiError(code='care_report_invalid_output', message='Report source hash did not match.', status=502)
        result.content.validate_evidence(snapshot.input.sources)
    except ApiError as error:
        failure = error.code if error.code in NON_RETRYABLE | {'care_report_timeout', 'care_report_provider_unavailable'} else 'care_report_provider_unavailable'
    except ValueError:
        failure = 'care_report_invalid_output'
    except TimeoutError:
        failure = 'care_report_timeout'
    except Exception:
        failure = 'care_report_generation_failed'
    async with sessions.begin() as session:
        record = await _authorized_job(session, report_id, token, now())
        if record is None:
            return True
        record.lease_token, record.lease_until = None, None
        if failure:
            record.error_code = failure
            record.status = 'failed' if record.attempts >= MAX_ATTEMPTS or failure in NON_RETRYABLE else 'queued'
            record.available_at = now() + timedelta(seconds=5 * 2 ** record.attempts)
            LOGGER.warning('Care report %s generation failed (%s)', report_id, failure)
        else:
            assert result is not None
            record.result, record.generated_at, record.status, record.error_code = result.model_dump(mode='json'), now(), 'ready', None
            await session.flush()
            await record_care_event(session, episode_id=record.episode_id, kind='report_generated',
                aggregate_id=record.id, aggregate_version=record.version, actor_user_id=None,
                recipient_id=record.provider_id, occurred_at=record.generated_at)
        await AuditService(repository=AuditRepository(session)).record(actor_user_id=None, action=f'care.report.{record.status}',
            resource_type='care_report', resource_id=str(report_id), request_id=str(token),
            details={'attempt': record.attempts, 'version': record.version, 'error_code': record.error_code})
    return True


async def scan_page(sessions: async_sessionmaker[AsyncSession], gateway: CareReportGateway, *, after: UUID | None = None,
    now: Callable[[], datetime] = utc_now) -> UUID | None:
    async with sessions.begin() as session:
        query = select(CareEpisode).where(CareEpisode.status == 'active', CareEpisode.assigned_ibclc_id.is_not(None),
            or_(CareEpisode.ends_at.is_(None), CareEpisode.ends_at > now()))
        if after is not None:
            query = query.where(CareEpisode.id > after)
        episodes = list(await session.scalars(query.order_by(CareEpisode.id).limit(50)))
    workflow = CareReportWorkflow(sessions, gateway, now=now)
    for episode in episodes:
        assert episode.assigned_ibclc_id is not None
        try:
            await workflow.enqueue(episode.assigned_ibclc_id, episode.id,
                ReportRequest(purpose='daily' if episode.starts_at else 'preparation'), 'scheduled-report', retry_failed=False)
        except ApiError as error:
            # Consent/assignment changes are expected; no patient text is logged.
            if error.status not in {403, 404, 409}:
                LOGGER.warning('Care report scan for %s failed (%s)', episode.id, error.code)
    return episodes[-1].id if len(episodes) == 50 else None


async def run() -> None:
    settings = Settings.from_env()
    settings.validate_for_startup()
    if not settings.care_report_runtime_url or not settings.care_report_service_key:
        raise RuntimeError('Configure CARE_REPORT_RUNTIME_URL and CARE_REPORT_SERVICE_KEY before starting the report worker.')
    engine = create_db_engine(settings)
    sessions = create_session_factory(engine)
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            gateway = RuntimeCareReportGateway(client, settings.care_report_runtime_url, settings.care_report_service_key)
            cursor = None
            next_scan = utc_now()
            while True:
                if utc_now() >= next_scan:
                    cursor = await scan_page(sessions, gateway, after=cursor)
                    next_scan = utc_now() + timedelta(seconds=60)
                if not await process_next(sessions, gateway):
                    await asyncio.sleep(1)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
