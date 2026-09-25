"""Synthetic fixtures exercise real Product workflows in an owned, temporary schema."""
from contextlib import asynccontextmanager
from datetime import timedelta, date
from uuid import uuid4

from app.core.settings import Settings
from app.infrastructure.care_report_contract import ReportGenerationResult, StructuredCareReport
from app.infrastructure.care_report_sources_contract import ReportSourcesRead, ReportDialogueSource
from app.modules.appointments.schemas import VersionWrite
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.consultations.models import CareConsentRevision
from app.modules.documentation.schemas import NoteWrite, NoteVersionWrite, PlanWrite
from app.modules.ibclc.repository import WorkbenchRepository
from app.modules.ibclc.service import WorkbenchService
from app.modules.lactation.models import LactationRecord
from app.modules.profiles.models import UserProfile, MaternalProfile
from app.modules.reports.models import CareConversationLink
from app.modules.reports.workflow import CareReportWorkflow
from app.modules.reports.schemas import ReportRequest
from app.workers.care_reports import process_next
from test_care_documentation import documented_case, NOTE, PLAN


@asynccontextmanager
async def ready_report_case(*, include_context=False):
    async with documented_case() as (sessions, documentation, at, appointment, mom, expert, wrong):
        thread, run_id, question_id, answer_id = uuid4(), uuid4(), uuid4(), uuid4()
        async with sessions.begin() as session:
            session.add(UserProfile(user_id=mom.user_id, preferred_name='Lin Xiao'))
            session.add(MaternalProfile(owner_user_id=mom.user_id, latest_delivery_date=date(2026, 8, 18)))
            session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ai_context', version=1, active=True,
                policy_version='2026-09-08', recorded_at=at[0]-timedelta(hours=1)))
            session.add(CareConversationLink(thread_id=thread, episode_id=appointment.episode_id, shared_since=at[0]-timedelta(hours=1)))
            session.add(LactationRecord(owner_user_id=mom.user_id, method='pump', side='left', occurred_at=at[0]-timedelta(minutes=30), volume_ml=60,
                note='First, note how I feel today.', version=1, created_at=at[0], updated_at=at[0]))
            service = documentation(session)
            await service.save_note(expert, appointment.id, NoteWrite(expected_revision=0, expected_version=0, content=NOTE), 'fixture-note', 'fixture-note')
            await service.sign_note(expert, appointment.id, NoteVersionWrite(expected_revision=1, expected_version=1), 'fixture-sign', 'fixture-sign')
            await service.save_plan(expert, appointment.id, PlanWrite(expected_version=0, content=PLAN), 'fixture-plan', 'fixture-plan')
            await service.publish(expert, appointment.id, VersionWrite(expected_version=1), 'fixture-publish', 'fixture-publish')
        class Gateway:
            async def sources(self, query):
                item = ReportDialogueSource(run_id=run_id, thread_id=thread, question_id=question_id, answer_id=answer_id,
                    question='Feeding hurts at the start. I want to check the latch first. Please give me one simple step.',
                    answer='Track how your comfort changes and review the latch together during your consultation.', question_at=at[0]-timedelta(minutes=20),
                    answered_at=at[0]-timedelta(minutes=19), question_truncated=False, answer_truncated=False)
                return ReportSourcesRead(items=[item], available_count=1, omitted_count=0, as_of=query.as_of)
            async def generate(self, body):
                assert 'Private observations' not in body.canonical_json() and 'Professional assessment' not in body.canonical_json()
                dialogue = next(value for value in body.sources if value.kind == 'dialogue')
                milk = next(value for value in body.sources if value.kind == 'lactation')
                def finding(text, source, quote):
                    return {'text': text, 'evidence': [{'source_id': source.id, 'quote': quote}]}
                content = StructuredCareReport(summary=[finding('The client reports discomfort when feeding begins and wants to check the latch first.', dialogue, 'I want to check the latch first'),
                    finding('There is a left-side pumping record; the baby’s actual intake still needs to be checked.', milk, 'Pumped milk: 60 ml')], emotional_state=[],
                    communication_preferences=[finding('The client prefers one simple step first.', dialogue, 'Please give me one simple step')],
                    checks=[finding('During the consultation, check when discomfort begins and how long it lasts.', dialogue, 'Feeding hurts at the start')],
                    data_gaps=['No direct observation of the latch is available.', 'Pumped milk volume does not establish the baby’s actual intake.'])
                return ReportGenerationResult(content=content, input_hash=body.input_hash(), provider='synthetic-test',
                    model='synthetic-test-fixture', provider_response_id='synthetic-report-fixture')
        gateway = Gateway()
        workflow = CareReportWorkflow(sessions, gateway, now=lambda: at[0])
        await workflow.request(expert, appointment.episode_id, ReportRequest(), 'fixture')
        assert await process_next(sessions, gateway, now=lambda: at[0])
        report = await workflow.read(expert, appointment.episode_id, 'daily', None, 'fixture')
        assert report.state == 'ready'
        history = await workflow.history(expert, appointment.episode_id, 'daily', 'fixture')
        async with sessions.begin() as session:
            service = WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), Settings(), now=lambda: at[0])
            queue = await service.followups(expert, query='', status='all', offset=0, limit=20, request_id='fixture')
            client = await service.client_detail(expert, mom.user_id, 'fixture')
        values = {'followups': queue, 'report': report, 'report_history': history, 'report_client': client}
        if include_context:
            values['context'] = (sessions, at, appointment, mom, expert)
        yield values
