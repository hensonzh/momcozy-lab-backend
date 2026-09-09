from datetime import date
from uuid import UUID
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.care_report_contract import ReportGenerationResult
from .models import CareReport, CareReportReview
from .schemas import ReportRead, ReportSnapshot, ReviewRead


async def latest_report(session: AsyncSession, episode_id: UUID, purpose: str, day: date) -> CareReport | None:
    return cast(CareReport | None, await session.scalar(select(CareReport).where(CareReport.episode_id == episode_id,
        CareReport.purpose == purpose, CareReport.report_date == day).order_by(CareReport.version.desc()).limit(1)
        .execution_options(populate_existing=True)))


async def latest_review(session: AsyncSession, report_id: UUID) -> CareReportReview | None:
    return cast(CareReportReview | None, await session.scalar(select(CareReportReview).where(CareReportReview.report_id == report_id)
        .order_by(CareReportReview.version.desc()).limit(1)))


async def report_read(session: AsyncSession, value: CareReport, *, reviewable: bool) -> ReportRead:
    review = await latest_review(session, value.id)
    return ReportRead.model_validate({'id': value.id, 'episode_id': value.episode_id, 'purpose': value.purpose,
        'report_date': value.report_date, 'timezone': value.timezone, 'version': value.version, 'status': value.status,
        'snapshot': ReportSnapshot.model_validate(value.snapshot) if value.snapshot is not None else None,
        'result': ReportGenerationResult.model_validate(value.result) if value.result is not None else None,
        'created_at': value.created_at, 'generated_at': value.generated_at, 'error_code': value.error_code,
        'review': ReviewRead.model_validate(review) if review else None, 'reviewable': reviewable})
