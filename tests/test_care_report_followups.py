import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.core.errors import ApiError
from app.core.settings import Settings
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.care.models import CareEligibility, CareEpisode, CareOrder
from app.modules.consultations.models import CareConsentRevision
from app.modules.ibclc.repository import WorkbenchRepository
from app.modules.ibclc.service import WorkbenchService
from app.modules.reports.models import CareReportReview
from app.modules.reports.schemas import ReportRequest, ReviewWrite
from app.modules.reports.workflow import CareReportWorkflow
from app.workers.care_reports import process_next
from test_care_booking import postgres
from test_care_reports import ReportGateway, add_sources
from test_care_rooms import room_case


@postgres
def test_all_services_must_be_reviewed_and_concurrent_reviews_cannot_overwrite_each_other():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            await add_sources(sessions, appointment, mom.user_id, at[0])
            async with sessions.begin() as session:
                first = await session.get(CareEpisode, appointment.episode_id)
                first.starts_at, first.ends_at = at[0] - timedelta(days=1), at[0] + timedelta(days=6)
                eligibility = CareEligibility(owner_user_id=mom.user_id, package_id='comfortable-feeding', region='CA', eligible=True, expires_at=at[0] + timedelta(days=1))
                session.add(eligibility)
                await session.flush()
                order = CareOrder(owner_user_id=mom.user_id, eligibility_id=eligibility.id, package_id=eligibility.package_id,
                    price_minor=21900, duration_days=3, total_sessions=2, region='CA', status='paid')
                session.add(order)
                await session.flush()
                second = CareEpisode(owner_user_id=mom.user_id, order_id=order.id, package_id=order.package_id,
                    assigned_ibclc_id=expert.user_id, total_sessions=2, remaining_sessions=2,
                    starts_at=at[0], ends_at=at[0] + timedelta(days=3))
                session.add(second)
                await session.flush()
                for scope in ['ibclc_case', 'ai_context']:
                    session.add(CareConsentRevision(episode_id=second.id, scope=scope, version=1, active=True, policy_version='2026-09-08', recorded_at=at[0]))
            async def queue(query='', status='all'):
                async with sessions.begin() as session:
                    return await WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), Settings(), now=lambda: at[0]).followups(
                        expert, query=query, status=status, offset=0, limit=20, request_id='multi-service')
            gateway = ReportGateway()
            workflow = CareReportWorkflow(sessions, gateway, now=lambda: at[0])
            report = await workflow.request(expert, appointment.episode_id, ReportRequest(), 'first')
            await process_next(sessions, gateway, now=lambda: at[0])
            await workflow.review(expert, report.report.id, ReviewWrite(expected_version=0, decision='confirmed'), 'first-reviewed')
            incomplete = await queue('feeding-confidence', 'pending')
            assert incomplete.total == incomplete.all_count == incomplete.pending_count == 1
            assert incomplete.completed_count == 0 and len(incomplete.items[0].services) == 2
            assert (await queue(status='completed')).total == 0
            second_report = await workflow.request(expert, second.id, ReportRequest(), 'second')
            await process_next(sessions, gateway, now=lambda: at[0])
            concurrent = await asyncio.gather(
                workflow.review(expert, second_report.report.id, ReviewWrite(expected_version=0, decision='confirmed'), 'tab-a'),
                workflow.review(expert, second_report.report.id, ReviewWrite(expected_version=0, decision='feedback', feedback='请进一步核对来源及描述。'), 'tab-b'),
                return_exceptions=True,
            )
            conflicts = [value for value in concurrent if isinstance(value, ApiError)]
            assert len(conflicts) == 1 and conflicts[0].code == 'care_report_review_changed'
            async with sessions.begin() as session:
                assert len(list(await session.scalars(select(CareReportReview).where(CareReportReview.report_id == second_report.report.id)))) == 1
            completed = await queue(status='completed')
            assert completed.total == completed.completed_count == 1 and completed.pending_count == 0
            at[0] += timedelta(days=1)
            following_day = await queue()
            assert following_day.pending_count == 1 and following_day.completed_count == 0
            assert all(value.report_status == 'not_generated' for value in following_day.items[0].services)
    asyncio.run(run())
