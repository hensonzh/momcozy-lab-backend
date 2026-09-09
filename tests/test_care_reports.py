
from app.modules.baby.profile_models import BabyProfile
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.errors import ApiError
from app.core.settings import Settings
from app.infrastructure.care_report_contract import ReportGenerationResult, StructuredCareReport
from app.infrastructure.care_report_sources_contract import ReportSourcesRead
from app.modules.consultations.models import CareConsentRevision
from app.modules.care.event_models import CareServiceEvent
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.ibclc.repository import WorkbenchRepository
from app.modules.ibclc.service import WorkbenchService
from app.modules.lactation.models import LactationRecord
from app.modules.reports.models import CareReport, CareReportReview
from app.modules.reports.schemas import ReportRequest, ReviewWrite
from app.modules.reports.workflow import CareReportWorkflow
from app.workers.care_reports import process_next
from test_care_booking import postgres
from test_care_rooms import room_case


class ReportGateway:
    def __init__(self):
        self.calls = []
        self.on_generate = None
    async def sources(self, query):
        return ReportSourcesRead(items=[], available_count=0, omitted_count=0, as_of=query.as_of)
    async def generate(self, body):
        self.calls.append(body)
        if self.on_generate:
            await self.on_generate()
        source = body.sources[0]
        return ReportGenerationResult(content=StructuredCareReport(summary=[{'text': '待专家核对的用户记录',
            'evidence': [{'source_id': source.id, 'quote': source.content[:10]}]}], emotional_state=[], communication_preferences=[], checks=[], data_gaps=[]),
            input_hash=body.input_hash(), provider='synthetic-test', model='synthetic-test', provider_response_id='synthetic-response')


async def add_sources(sessions, appointment, owner, at):
    async with sessions.begin() as session:
        session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ai_context', version=1, active=True, policy_version='2026-09-08', recorded_at=at))
        record = LactationRecord(owner_user_id=owner, method='pump', side='left', occurred_at=at, volume_ml=None,
            duration_minutes=None, note='妈妈泵奶量未记录，宝宝摄入量未知。', updated_at=at, created_at=at)
        session.add(record)
        await session.flush()
        return record.id


@postgres
def test_daily_report_deduplicates_sources_persists_review_and_rejects_stale_versions():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            record_id = await add_sources(sessions, appointment, mom.user_id, at[0])
            gateway = ReportGateway()
            workflow = CareReportWorkflow(sessions, gateway, now=lambda: at[0])
            first = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'first')
            assert first.state == 'queued' and first.report.version == 1
            at[0] += timedelta(minutes=1)
            repeated = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'retry')
            assert repeated.report.id == first.report.id
            assert await process_next(sessions, gateway, now=lambda: at[0])
            assert not await process_next(sessions, gateway, now=lambda: at[0])
            ready = await workflow.read(expert, appointment.episode_id, 'daily', first.report_date, 'read')
            assert ready.state == 'ready' and len(gateway.calls) == 1
            assert all(value.kind != 'clinical_note' for value in gateway.calls[0].sources)
            write = ReviewWrite(expected_version=0, decision='feedback', feedback='请先核对泵奶和宝宝摄入量的区别。')
            reviewed = await workflow.review(expert, first.report.id, write, 'review')
            assert reviewed.review.version == 1
            assert (await workflow.review(expert, first.report.id, write, 'repeat')).review.id == reviewed.review.id
            async with sessions.begin() as session:
                record = await session.get(LactationRecord, record_id)
                record.volume_ml, record.version, record.updated_at = 60, 2, at[0]
            second = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'changed')
            assert second.report.version == 2 and second.report.id != first.report.id
            with pytest.raises(ApiError) as stale:
                await workflow.review(expert, first.report.id, ReviewWrite(expected_version=1, decision='confirmed'), 'old-tab')
            assert stale.value.code == 'care_report_changed'
            for actor in [mom, wrong]:
                with pytest.raises(ApiError):
                    await workflow.read(actor, appointment.episode_id, 'daily', first.report_date, 'foreign')
            async with sessions.begin() as session:
                assert len(list(await session.scalars(select(CareReportReview)))) == 1
                events = list(await session.scalars(select(CareServiceEvent).where(CareServiceEvent.kind.in_(['report_generated', 'report_reviewed']))))
                assert len(events) == 2
                generated = next(value for value in events if value.kind == 'report_generated')
                assert generated.aggregate_id == first.report.id and generated.aggregate_version == 1
                assert generated.workbench_recipient_id == expert.user_id
                reviewed_event = next(value for value in events if value.kind == 'report_reviewed')
                assert reviewed_event.aggregate_id == first.report.id and reviewed_event.aggregate_version == 1
                assert reviewed_event.workbench_recipient_id is None
                reminders = await WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), Settings(), now=lambda: at[0]).reminders(
                    expert, offset=0, limit=20, request_id='report-reminder')
                item = next(value for value in reminders.items if value.kind == 'report_generated')
                assert item.report_date == first.report_date and item.episode_id == appointment.episode_id
                assert item.timezone == first.timezone
                assert not any(value.kind == 'report_reviewed' for value in reminders.items)
    asyncio.run(run())


@postgres
def test_no_daily_records_stays_waiting_across_reload_but_preparation_can_use_intake():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            async with sessions.begin() as session:
                session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ai_context', version=1, active=True, policy_version='2026-09-08', recorded_at=at[0]))
            gateway = ReportGateway()
            workflow = CareReportWorkflow(sessions, gateway, now=lambda: at[0])
            waiting = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'waiting')
            assert waiting.state == 'waiting_for_record' and waiting.report.snapshot is None
            assert (await workflow.read(expert, appointment.episode_id, 'daily', None, 'reload')).state == 'waiting_for_record'
            assert not await process_next(sessions, gateway, now=lambda: at[0])
            preparation = await workflow.request(expert, appointment.episode_id, ReportRequest(purpose='preparation'), 'prep')
            assert preparation.state == 'queued'
            assert await process_next(sessions, gateway, now=lambda: at[0])
            assert gateway.calls[0].purpose == 'preparation'
    asyncio.run(run())


@postgres
def test_report_result_is_discarded_when_authorization_changes_during_generation():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            await add_sources(sessions, appointment, mom.user_id, at[0])
            gateway = ReportGateway()
            async def withdraw():
                async with sessions.begin() as session:
                    session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ai_context', version=2, active=False, policy_version='2026-09-08', recorded_at=at[0]))
            gateway.on_generate = withdraw
            workflow = CareReportWorkflow(sessions, gateway, now=lambda: at[0])
            first = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'request')
            assert await process_next(sessions, gateway, now=lambda: at[0])
            async with sessions.begin() as session:
                report = await session.get(CareReport, first.report.id)
                assert report.status == 'cancelled' and report.result is None and report.lease_token is None
                assert not list(await session.scalars(select(CareServiceEvent).where(CareServiceEvent.kind == 'report_generated')))
            with pytest.raises(ApiError) as denied:
                await workflow.read(expert, appointment.episode_id, 'daily', None, 'read')
            assert denied.value.code == 'ai_consent_required'
    asyncio.run(run())


@postgres
def test_two_workers_share_a_lease_and_old_inflight_output_cannot_replace_new_version():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            record_id = await add_sources(sessions, appointment, mom.user_id, at[0])
            gateway = ReportGateway()
            entered, release = asyncio.Event(), asyncio.Event()
            async def pause():
                entered.set()
                await release.wait()
            gateway.on_generate = pause
            workflow = CareReportWorkflow(sessions, gateway, now=lambda: at[0])
            first = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'first')
            worker = asyncio.create_task(process_next(sessions, gateway, now=lambda: at[0]))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                assert not await process_next(sessions, gateway, now=lambda: at[0])
                async with sessions.begin() as session:
                    record = await session.get(LactationRecord, record_id)
                    record.volume_ml, record.version = 75, 2
                second = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'new')
                assert second.report.version == 2
            finally:
                release.set()
                await asyncio.wait_for(worker, timeout=5)
            async with sessions.begin() as session:
                previous = await session.get(CareReport, first.report.id)
                assert previous.status == 'cancelled' and previous.result is None
            gateway.on_generate = None
            assert await process_next(sessions, gateway, now=lambda: at[0])
            assert (await workflow.read(expert, appointment.episode_id, 'daily', None, 'read')).report.id == second.report.id
    asyncio.run(run())


@postgres
def test_expired_worker_lease_recovers_and_failed_source_snapshot_has_bounded_retries():
    from uuid import uuid4
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            record_id = await add_sources(sessions, appointment, mom.user_id, at[0])
            gateway = ReportGateway()
            workflow = CareReportWorkflow(sessions, gateway, now=lambda: at[0])
            first = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'first')
            async with sessions.begin() as session:
                report = await session.get(CareReport, first.report.id)
                report.status, report.attempts, report.lease_token, report.lease_until = 'running', 1, uuid4(), at[0] - timedelta(seconds=1)
            assert await process_next(sessions, gateway, now=lambda: at[0])
            async with sessions.begin() as session:
                report = await session.get(CareReport, first.report.id)
                assert report.status == 'ready' and report.attempts == 2 and report.lease_token is None
                source = await session.get(LactationRecord, record_id)
                source.volume_ml, source.version = 60, 2
            changed = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'new-source')
            assert changed.report.version == 2
            async def unavailable():
                raise ApiError(code='care_report_provider_unavailable', message='Synthetic upstream outage.', status=503)
            gateway.on_generate = unavailable
            for attempt in range(3):
                assert await process_next(sessions, gateway, now=lambda: at[0])
                if attempt < 2:
                    assert not await process_next(sessions, gateway, now=lambda: at[0])
                at[0] += timedelta(seconds=60)
            failed = await workflow.read(expert, appointment.episode_id, 'daily', None, 'failed')
            assert failed.state == 'failed' and failed.report.result is None
            scheduled = await workflow.enqueue(expert.user_id, appointment.episode_id, ReportRequest(), 'scan', retry_failed=False)
            assert scheduled.report.id == changed.report.id
            retried = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'manual')
            assert retried.report.version == 3 and retried.state == 'queued'
    asyncio.run(run())


@postgres
def test_followup_queue_uses_real_reviews_and_hides_report_metadata_when_case_is_withdrawn():
    from app.core.settings import Settings
    from app.modules.audit.repository import AuditRepository
    from app.modules.audit.service import AuditService
    from app.modules.ibclc.repository import WorkbenchRepository
    from app.modules.ibclc.service import WorkbenchService
    from app.modules.profiles.models import UserProfile
    from app.modules.care.models import CareEpisode
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            await add_sources(sessions, appointment, mom.user_id, at[0])
            gateway = ReportGateway()
            workflow = CareReportWorkflow(sessions, gateway, now=lambda: at[0])
            async with sessions.begin() as session:
                session.add(UserProfile(user_id=mom.user_id, preferred_name='Synthetic followup client'))
                episode = await session.get(CareEpisode, appointment.episode_id)
                episode.starts_at, episode.ends_at = at[0] - timedelta(days=1), at[0] + timedelta(days=6)
            async def queue(status='all', q=''):
                async with sessions.begin() as session:
                    service = WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), Settings(), now=lambda: at[0])
                    return await service.followups(expert, query=q, status=status, offset=0, limit=20, request_id='queue')
            initial = await queue()
            assert initial.all_count == 1 and initial.pending_count == 1 and initial.completed_count == 0
            assert initial.items[0].services[0].report_status == 'not_generated'
            assert (await queue(q='feeding-confidence')).total == 1
            report = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'request')
            assert await process_next(sessions, gateway, now=lambda: at[0])
            pending = await queue('pending')
            assert pending.total == 1 and pending.items[0].services[0].report_status == 'ready'
            await workflow.review(expert, report.report.id, ReviewWrite(expected_version=0, decision='confirmed'), 'review')
            complete = await queue('completed')
            assert complete.total == 1 and complete.completed_count == 1 and complete.pending_count == 0
            assert complete.items[0].services[0].review_decision == 'confirmed'
            assert (await queue('pending')).items == []
            history = await workflow.history(expert, appointment.episode_id, 'daily', 'history')
            assert len(history.items) == 1 and history.items[0].review_decision == 'confirmed'
            async with sessions.begin() as session:
                session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ibclc_case', version=2, active=False, policy_version='2026-09-08', recorded_at=at[0]))
            revoked = await queue()
            assert revoked.items[0].name is None and revoked.completed_count == 0
            assert revoked.items[0].services[0].report_id is None
            assert revoked.items[0].services[0].report_status == 'case_consent_required'
            assert (await queue(q='Synthetic followup')).items == []
    asyncio.run(run())


@postgres
def test_report_sources_include_published_plan_but_never_the_signed_private_note():
    from care_report_fixture import ready_report_case
    async def run():
        async with ready_report_case() as values:
            report = values['report'].report
            assert report.status == 'ready' and report.reviewable is True
            assert 'care_plan' in {source.kind for source in report.snapshot.input.sources}
            assert 'Private observations' not in report.model_dump_json()
            assert 'Professional assessment' not in report.model_dump_json()
            assert len(report.snapshot.dialogues) == 1
    asyncio.run(run())


@postgres
def test_report_sources_only_include_the_baby_linked_to_the_service():
    from app.modules.baby.models import BabyRecord
    from app.modules.care.models import CareEpisode

    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            await add_sources(sessions, appointment, mom.user_id, at[0])
            async with sessions.begin() as session:
                selected, other = BabyProfile(owner_user_id=mom.user_id, name='Selected baby'), BabyProfile(owner_user_id=mom.user_id, name='Other baby')
                session.add_all([selected, other])
                await session.flush()
                episode = await session.get(CareEpisode, appointment.episode_id)
                episode.baby_id = selected.id
                for baby, amount, marker in [(selected, 40, 'selected-baby-note'), (other, 90, 'other-baby-private-note')]:
                    session.add(BabyRecord(owner_user_id=mom.user_id, baby_id=baby.id, kind='feeding', occurred_at=at[0],
                        data={'method': 'expressed_milk', 'side': None, 'duration_minutes': None, 'volume_ml': amount, 'note': marker}, version=1,
                        created_at=at[0], updated_at=at[0]))
            workflow = CareReportWorkflow(sessions, ReportGateway(), now=lambda: at[0])
            requested = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'baby-scope')
            sources = requested.report.snapshot.input.sources
            records = [source for source in sources if source.kind == 'baby_record']
            assert len(records) == 1 and '40 ml' in records[0].content
            assert 'selected-baby-note' in records[0].content
            assert all('other-baby-private-note' not in source.content for source in sources)
    asyncio.run(run())
