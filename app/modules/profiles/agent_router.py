from __future__ import annotations


from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, Header, Query, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError

from ...api.dependencies import require_agent_runtime_client
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..auth import ServiceClient
from ..baby.repository import BabyRecordRepository
from ..records.repository import RecordsRepository
from ..records.service import RecordsService
from .lactation_context import LactationContextService
from .lactation_context_schema import MaternalBabyProfileReadOutput
from .repository import ProfileRepository
from .topical_records import TopicalRecordsQuery, TopicalRecordsReadOutput, TopicalRecordsService
from .agent_mutation import AgentBatchService, RecordBatch, ScheduleBatch, BatchResult
from ..schedule.schemas import SchedulePageRead
from ..schedule.service import ScheduleService
from ..schedule.repository import ScheduleRepository
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, IdempotencyService


router = SurfaceAPIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    api_surface_metadata=api_surface(
        "internal_service_api",
        owner="profiles",
        clients=["agent-runtime"],
    ),
)


def get_agent_profile_read_service(
    session: AsyncSession = Depends(get_session),
) -> LactationContextService:
    return LactationContextService(
        profile_repository=ProfileRepository(session),
        records_service=RecordsService(repository=RecordsRepository(session)),
        baby_records_repository=BabyRecordRepository(session),
    )




@router.get("/profile", response_model=MaternalBabyProfileReadOutput)
async def read_agent_profile(
    actor_user_id: UUID,
    infant_scope: Literal["current_delivery", "all"] = Query(default="current_delivery"),
    as_of_date: date | None = None,
    timezone: str = Query(default="UTC", min_length=1, max_length=80),
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: LactationContextService = Depends(get_agent_profile_read_service),
) -> MaternalBabyProfileReadOutput:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ApiError(code="validation_failed", message="Use an IANA timezone.", status=422) from exc
    result = await service.read(
        owner_user_id=actor_user_id,
        as_of_date=as_of_date,
        infant_scope=infant_scope,
        timezone=timezone,
    )
    return MaternalBabyProfileReadOutput.model_validate(result)


def get_agent_topical_records_service(session: AsyncSession = Depends(get_session)) -> TopicalRecordsService:
    return TopicalRecordsService(session, ProfileRepository(session))


@router.get("/records", response_model=TopicalRecordsReadOutput)
async def read_agent_topical_records(
    actor_user_id: UUID,
    topic: Literal["feeding", "pumping", "diaper", "pain", "growth", "latch", "after_feeding_mood"],
    start_date: date,
    end_date: date,
    timezone: str,
    infant_id: UUID | None = None,
    limit: int = 20,
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: TopicalRecordsService = Depends(get_agent_topical_records_service),
) -> TopicalRecordsReadOutput:
    try:
        query = TopicalRecordsQuery(actor_user_id=actor_user_id, topic=topic, infant_id=infant_id,
            start_date=start_date, end_date=end_date, timezone=timezone, limit=limit)
    except ValidationError as exc:
        raise ApiError(code="validation_failed", message="Record query is invalid.", status=422) from exc
    return await service.read(query)


def get_agent_batch_service(session: AsyncSession = Depends(get_session)) -> AgentBatchService:
    return AgentBatchService(session)


@router.post("/records/batch", response_model=BatchResult)
async def write_agent_records(
    body: RecordBatch, request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: AgentBatchService = Depends(get_agent_batch_service),
) -> BatchResult:
    return await service.records(body, key=idempotency_key, request_id=request.state.request_id)


@router.post("/schedule/batch", response_model=BatchResult)
async def write_agent_schedule(
    body: ScheduleBatch, request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: AgentBatchService = Depends(get_agent_batch_service),
) -> BatchResult:
    return await service.schedule(body, key=idempotency_key, request_id=request.state.request_id)


def get_agent_schedule_service(session: AsyncSession = Depends(get_session)) -> ScheduleService:
    audit = AuditRepository(session)
    return ScheduleService(ScheduleRepository(session), audit=AuditService(repository=audit),
        idempotency=IdempotencyService(repository=audit))


@router.get("/schedule", response_model=SchedulePageRead)
async def read_agent_schedule(
    actor_user_id: UUID, start_date: date, end_date: date,
    timezone: str = Query(min_length=1, max_length=80),
    offset: int = Query(default=0, ge=0, le=10000),
    limit: int = Query(default=50, ge=1, le=100),
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: ScheduleService = Depends(get_agent_schedule_service),
) -> SchedulePageRead:
    return await service.read(actor_user_id, start_date, end_date, timezone, offset, limit)
