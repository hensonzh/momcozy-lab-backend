from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID

from fastapi import Depends, Request, Response
import httpx

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.care_reports import RuntimeCareReportGateway
from ..auth import CurrentUser
from .schemas import ReportHistoryRead, ReportPurpose, ReportRead, ReportRequest, ReportStateRead, ReviewWrite
from .workflow import CareReportWorkflow

router = SurfaceAPIRouter(prefix='/ibclc', tags=['ibclc-reports'],
    api_surface_metadata=api_surface('public_app_api', owner='care-reports', clients=['ibclc']))


async def get_report_workflow(request: Request, response: Response) -> AsyncIterator[CareReportWorkflow]:
    settings = request.app.state.settings
    response.headers['Cache-Control'] = 'private, no-store'
    async with httpx.AsyncClient(follow_redirects=False) as client:
        yield CareReportWorkflow(request.app.state.db_session_factory,
            RuntimeCareReportGateway(client, settings.care_report_runtime_url, settings.care_report_service_key))


@router.get('/episodes/{episode_id}/reports', response_model=ReportStateRead)
async def read(episode_id: UUID, request: Request, purpose: ReportPurpose = 'daily', report_date: date | None = None,
    actor: CurrentUser = Depends(require_current_user), workflow: CareReportWorkflow = Depends(get_report_workflow)) -> ReportStateRead:
    return await workflow.read(actor, episode_id, purpose, report_date, request.state.request_id)


@router.post('/episodes/{episode_id}/reports', response_model=ReportStateRead)
async def generate(episode_id: UUID, body: ReportRequest, request: Request, actor: CurrentUser = Depends(require_current_user),
    workflow: CareReportWorkflow = Depends(get_report_workflow)) -> ReportStateRead:
    settings = request.app.state.settings
    if not settings.care_report_runtime_url or not settings.care_report_service_key:
        raise ApiError(code='care_reports_unavailable', message='AI reports are not configured.', status=503)
    return await workflow.request(actor, episode_id, body, request.state.request_id)


@router.post('/reports/{report_id}/reviews', response_model=ReportRead)
async def review(report_id: UUID, body: ReviewWrite, request: Request, actor: CurrentUser = Depends(require_current_user),
    workflow: CareReportWorkflow = Depends(get_report_workflow)) -> ReportRead:
    return await workflow.review(actor, report_id, body, request.state.request_id)


@router.get('/episodes/{episode_id}/reports/history', response_model=ReportHistoryRead)
async def history(episode_id: UUID, request: Request, purpose: ReportPurpose = 'daily', actor: CurrentUser = Depends(require_current_user),
    workflow: CareReportWorkflow = Depends(get_report_workflow)) -> ReportHistoryRead:
    return await workflow.history(actor, episode_id, purpose, request.state.request_id)
