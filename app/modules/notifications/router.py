from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .repository import NotificationsRepository
from .schemas import NotificationListResponse, NotificationRead, NotificationReadStateUpdate
from .service import NotificationsService


router = SurfaceAPIRouter(
    prefix="/notifications",
    tags=["notifications"],
    api_surface_metadata=api_surface("public_app_api", owner="notifications", clients=["flutter"]),
)


def get_notifications_service(session: AsyncSession = Depends(get_session)) -> NotificationsService:
    audit_repository = AuditRepository(session)
    return NotificationsService(
        repository=NotificationsRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )


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
