"""Authenticated cry analysis; audio lives only in a bounded in-process task."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import BackgroundTasks, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...api.dependencies import optional_idempotency_key, require_agent_runtime_client, require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ..audit.repository import AuditRepository
from ..audit.service import IdempotencyService, parse_idempotency_response_ref
from ..auth import CurrentUser, ServiceClient
from .models import CryAnalysis
from .schemas import CryAnalysisInternalRead, CryAnalysisListRead, CryAnalysisRead, CryAnalysisResolveRequest
from .service import MAX_WAV_BYTES, analyze, validate_wav

router = SurfaceAPIRouter(prefix='/cry-analyses', tags=['cry-analyses'],
                          api_surface_metadata=api_surface('public_app_api', owner='cry-analysis', clients=['flutter']))
internal_router = SurfaceAPIRouter(prefix='/internal/agent/cry-analyses', tags=['internal-agent'],
                                   api_surface_metadata=api_surface('internal_service_api', owner='cry-analysis', clients=['agent-runtime']))
PENDING_TIMEOUT = timedelta(minutes=5)
MAX_ACTIVE_CRY_ANALYSES = 8
MAX_ACTIVE_CRY_ANALYSES_PER_OWNER = 2
LOGGER = logging.getLogger('production_backend.cry_analysis')
# All API replicas for a region share the same PostgreSQL admission gate.
_CRY_ADMISSION_LOCK = 0x6372795F616E616C


async def _read_audio(file: UploadFile) -> bytes:
    if file.content_type != 'audio/wav':
        raise ApiError(code='cry_audio_invalid', message='Recording must be WAV.', status=422)
    body = await file.read(MAX_WAV_BYTES + 1)
    validate_wav(body)
    return body


async def _finish_analysis(
    analysis_id: UUID, body: bytes, owner: UUID, *, endpoint: str, api_key: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # No bytes are persisted or passed to Agent. An interrupted worker leaves a
    # bounded pending record which is reported as failed after PENDING_TIMEOUT.
    try:
        result = await analyze(body, owner, endpoint=endpoint, api_key=api_key)
    except Exception as exc:
        # Never log vendor URLs, keys, response bodies or the recording.
        LOGGER.warning('Cry analysis failed (%s)', exc.code if isinstance(exc, ApiError) else 'unexpected')
        result = {'status': 'failed'}
    async with sessions.begin() as session:
        analysis = await session.scalar(select(CryAnalysis).where(CryAnalysis.id == analysis_id).with_for_update())
        if analysis is not None and analysis.status == 'pending':
            analysis.status = result['status']
            analysis.answer = result.get('answer')


async def _read_result(session: AsyncSession, analysis_id: UUID, owner: UUID) -> CryAnalysisRead:
    analysis = await session.scalar(select(CryAnalysis).where(
        CryAnalysis.id == analysis_id, CryAnalysis.owner_user_id == owner,
    ).with_for_update())
    if analysis is None:
        raise ApiError(code='not_found', message='Analysis not found.', status=404)
    await _expire_pending(session, analysis)
    return CryAnalysisRead.model_validate(analysis)


async def _expire_pending(session: AsyncSession, analysis: CryAnalysis) -> None:
    created_at = analysis.created_at
    if analysis.status == 'pending' and created_at is not None:
        if created_at.replace(tzinfo=created_at.tzinfo or timezone.utc) < datetime.now(timezone.utc) - PENDING_TIMEOUT:
            analysis.status = 'failed'
            await session.flush()


async def _admit_cry_job(session: AsyncSession, owner: UUID) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(_CRY_ADMISSION_LOCK)))
    fresh = CryAnalysis.created_at >= datetime.now(timezone.utc) - PENDING_TIMEOUT
    active = CryAnalysis.status == 'pending'
    total = await session.scalar(select(func.count()).select_from(CryAnalysis).where(active, fresh))
    per_owner = await session.scalar(select(func.count()).select_from(CryAnalysis).where(
        active, fresh, CryAnalysis.owner_user_id == owner,
    ))
    if (total or 0) >= MAX_ACTIVE_CRY_ANALYSES or (per_owner or 0) >= MAX_ACTIVE_CRY_ANALYSES_PER_OWNER:
        raise ApiError(code='cry_analysis_busy', message='Cry analysis is busy. Please retry shortly.', status=429,
                       details={'retry_after_seconds': 3})


@router.post('', response_model=CryAnalysisRead, status_code=202)
async def create_cry_analysis(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    thread_id: UUID | None = Form(default=None),
    idempotency_key: str | None = Depends(optional_idempotency_key),
    user: CurrentUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> CryAnalysisRead:
    response.headers['Cache-Control'] = 'private, no-store'
    body = await _read_audio(file)
    if not idempotency_key:
        raise ApiError(code='validation_failed', message='Idempotency-Key is required.', status=422)
    idempotency = IdempotencyService(repository=AuditRepository(session))
    decision = await idempotency.reserve(
        actor_user_id=user.user_id, scope='cry.analysis.create', key=idempotency_key,
        request_hash=hashlib.sha256(body + (thread_id.bytes if thread_id else b'')).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    if decision.status == 'replay':
        return await _read_result(session, parse_idempotency_response_ref(decision.record.response_ref), user.user_id)
    await _admit_cry_job(session, user.user_id)
    # The App creates a committed Agent thread before uploading. This is only
    # an owner-scoped association; Agent independently verifies thread ownership.
    analysis = CryAnalysis(owner_user_id=user.user_id, thread_id=thread_id, status='pending', answer=None)
    session.add(analysis)
    await session.flush()
    await idempotency.mark_completed(record=decision.record, response_ref=str(analysis.id))
    result = CryAnalysisRead.model_validate(analysis)
    # Release the DB connection and row locks before contacting the vendor.
    await session.commit()
    settings = request.app.state.settings
    background_tasks.add_task(
        _finish_analysis, analysis.id, body, user.user_id, endpoint=settings.zoundream_endpoint,
        api_key=settings.zoundream_api_key,
        sessions=async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    return result


@router.get('', response_model=CryAnalysisListRead)
async def list_cry_analyses(
    response: Response,
    thread_id: UUID,
    limit: int = Query(default=100, ge=1, le=100),
    user: CurrentUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> CryAnalysisListRead:
    response.headers['Cache-Control'] = 'private, no-store'
    items = list(await session.scalars(select(CryAnalysis).where(
        CryAnalysis.owner_user_id == user.user_id, CryAnalysis.thread_id == thread_id,
    ).order_by(CryAnalysis.created_at.desc(), CryAnalysis.id.desc()).limit(limit).with_for_update()))
    for item in items:
        await _expire_pending(session, item)
    return CryAnalysisListRead(items=[CryAnalysisRead.model_validate(item) for item in items])


@router.get('/by-key/{key}', response_model=CryAnalysisRead)
async def get_cry_analysis_by_key(
    key: str, response: Response,
    user: CurrentUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> CryAnalysisRead:
    response.headers['Cache-Control'] = 'private, no-store'
    if not key or len(key) > 255:
        raise ApiError(code='not_found', message='Analysis not found.', status=404)
    record = await AuditRepository(session).get_idempotency_key(
        actor_user_id=user.user_id, scope='cry.analysis.create', key=key,
    )
    if record is None or not record.response_ref:
        raise ApiError(code='not_found', message='Analysis not found.', status=404)
    return await _read_result(session, parse_idempotency_response_ref(record.response_ref), user.user_id)


@router.get('/{analysis_id}', response_model=CryAnalysisRead)
async def get_cry_analysis(
    analysis_id: UUID, response: Response,
    user: CurrentUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> CryAnalysisRead:
    response.headers['Cache-Control'] = 'private, no-store'
    return await _read_result(session, analysis_id, user.user_id)


@internal_router.post('/resolve', response_model=CryAnalysisInternalRead)
async def get_cry_analysis_for_agent(
    payload: CryAnalysisResolveRequest,
    response: Response,
    _client: ServiceClient = Depends(require_agent_runtime_client),
    session: AsyncSession = Depends(get_session),
) -> CryAnalysisInternalRead:
    response.headers['Cache-Control'] = 'private, no-store'
    analysis = await session.scalar(select(CryAnalysis).where(
        CryAnalysis.id == payload.analysis_id, CryAnalysis.owner_user_id == payload.actor_user_id,
    ).with_for_update())
    if analysis is None or (analysis.thread_id is not None and analysis.thread_id != payload.thread_id):
        raise ApiError(code='not_found', message='Analysis not found.', status=404)
    if analysis.status not in {'done', 'incomplete'}:
        raise ApiError(code='cry_analysis_not_ready', message='Analysis is not ready.', status=409)
    if analysis.thread_id is None:
        analysis.thread_id = payload.thread_id
        await session.flush()
    return CryAnalysisInternalRead.model_validate({
        'id': analysis.id, 'status': analysis.status, 'answer': analysis.answer,
        'created_at': analysis.created_at, 'thread_id': payload.thread_id,
    })
