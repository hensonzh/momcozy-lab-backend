from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user, require_service_client
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser, ServiceClient
from .repository import NotificationsRepository
from .schemas import NotificationListResponse, NotificationRead, NotificationReadStateUpdate, NotificationServiceCreate
from .service import NotificationsService


router = APIRouter(prefix="/notifications", tags=["notifications"])


def get_notifications_service(session: AsyncSession = Depends(get_session)) -> NotificationsService:
    audit_repository = AuditRepository(session)
    return NotificationsService(
        repository=NotificationsRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )


@router.post("", response_model=NotificationRead, status_code=status.HTTP_201_CREATED)
async def create_notification(
    payload: NotificationServiceCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service_client: ServiceClient = Depends(require_service_client),
    service: NotificationsService = Depends(get_notifications_service),
) -> NotificationRead:
    notification = await service.create_notification(
        owner_user_id=payload.owner_user_id,
        notification_type=payload.notification_type,
        title=payload.title or "",
        body=payload.body or "",
        source=payload.source or "system",
        payload=payload.payload,
        delivered_at=payload.delivered_at,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=_normalize_idempotency_key(idempotency_key),
        actor_service=service_client.name,
    )
    return NotificationRead.model_validate(notification)


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    status_filter: str | None = Query(default=None, alias="status"),
    notification_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: NotificationsService = Depends(get_notifications_service),
) -> NotificationListResponse:
    notifications = await service.list_notifications(
        owner_user_id=current_user.user_id,
        status=status_filter,
        notification_type=notification_type,
        limit=limit,
    )
    return NotificationListResponse(items=[NotificationRead.model_validate(notification) for notification in notifications])


@router.patch("/{notification_id}/read", response_model=NotificationRead)
async def set_notification_read_state(
    notification_id: UUID,
    payload: NotificationReadStateUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: NotificationsService = Depends(get_notifications_service),
) -> NotificationRead:
    notification = await service.set_read_state(
        owner_user_id=current_user.user_id,
        notification_id=notification_id,
        read=payload.read,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return NotificationRead.model_validate(notification)


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_notification(
    notification_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: NotificationsService = Depends(get_notifications_service),
) -> Response:
    await service.archive_notification(
        owner_user_id=current_user.user_id,
        notification_id=notification_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _normalize_idempotency_key(value: str | None) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    if len(key) > 255:
        raise ApiError(code="validation_failed", message="Idempotency-Key is too long.", status=422)
    return key
