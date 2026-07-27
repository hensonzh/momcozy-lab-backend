from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import (
    optional_idempotency_key,
    require_agent_runtime_client,
)
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import ServiceClient
from .agent_contracts import (
    AgentDiaryApplyRequest,
    AgentDiaryApplyResponse,
    AgentDiaryReadResponse,
)
from .agent_service import AgentDiaryWriteService
from .repository import DiaryRepository
from .service import DiaryService


router = SurfaceAPIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    api_surface_metadata=api_surface(
        "internal_service_api",
        owner="diary",
        clients=["agent-runtime"],
    ),
)


def get_agent_diary_read_service(
    session: AsyncSession = Depends(get_session),
) -> DiaryService:
    return DiaryService(repository=DiaryRepository(session))


def get_agent_diary_write_service(
    session: AsyncSession = Depends(get_session),
) -> AgentDiaryWriteService:
    audit_repository = AuditRepository(session)
    return AgentDiaryWriteService(
        diary_service=DiaryService(repository=DiaryRepository(session)),
        idempotency_service=IdempotencyService(repository=audit_repository),
        audit_service=AuditService(repository=audit_repository),
    )


@router.get(
    "/diary",
    response_model=AgentDiaryReadResponse,
)
async def read_agent_diary(
    actor_user_id: UUID,
    entry_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=7, ge=1, le=30),
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: DiaryService = Depends(get_agent_diary_read_service),
) -> AgentDiaryReadResponse:
    if entry_date is not None:
        try:
            entry = await service.get_entry(
                owner_user_id=actor_user_id,
                entry_date=entry_date,
            )
        except ApiError as exc:
            if exc.code != "not_found":
                raise
            return AgentDiaryReadResponse(
                status="entry_not_found",
                entry_date=entry_date,
            )
        return AgentDiaryReadResponse(
            status="entry_read",
            entry_date=entry_date,
            entry=_entry_payload(entry, include_content=True),
        )
    entries = await service.list_entries(
        owner_user_id=actor_user_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    return AgentDiaryReadResponse(
        status="entries_read",
        entries=[
            _entry_payload(entry, include_content=False)
            for entry in entries
        ],
        count=len(entries),
        filters={
            "start_date": start_date.isoformat() if start_date else "",
            "end_date": end_date.isoformat() if end_date else "",
            "limit": limit,
        },
    )


@router.post(
    "/actions/diary.entry/apply",
    response_model=AgentDiaryApplyResponse,
)
async def apply_agent_diary_action(
    payload: AgentDiaryApplyRequest,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: AgentDiaryWriteService = Depends(get_agent_diary_write_service),
) -> AgentDiaryApplyResponse:
    if idempotency_key is None:
        raise ApiError(
            code="validation_failed",
            message="Idempotency-Key is required.",
            status=422,
        )
    if idempotency_key != f"agent-action:{payload.action_id}":
        raise ApiError(
            code="validation_failed",
            message="Idempotency-Key must be bound to action_id.",
            status=422,
        )
    result = await service.apply_idempotent(
        owner_user_id=payload.actor_user_id,
        payload=payload.payload,
        idempotency_key=idempotency_key,
        action_id=payload.action_id,
        run_id=payload.run_id,
        actor_service=service_client.name,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return AgentDiaryApplyResponse(
        status="applied",
        action_id=payload.action_id,
        resource_type="diary_entry",
        resource_id=result.resource_id,
        details=result.details,
        application_events=list(result.application_events),
    )


def _entry_payload(entry: Any, *, include_content: bool) -> dict[str, Any]:
    attributes = dict(entry.attributes or {})
    payload: dict[str, Any] = {
        "id": str(entry.id),
        "entry_date": entry.entry_date.isoformat(),
        "attributes": attributes,
        "status": str(entry.status or ""),
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }
    if include_content:
        payload["content"] = str(entry.content or "")
        payload["attachments"] = list(entry.attachments or [])
    return payload
