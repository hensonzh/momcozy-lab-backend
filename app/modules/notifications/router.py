from __future__ import annotations

from uuid import UUID

import httpx
from fastapi import Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ...infrastructure.agent_runtime import RuntimeConversationGateway
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .repository import NotificationsRepository
from .schemas import NotificationListResponse, NotificationRead, NotificationReadStateUpdate
from .service import NotificationsService
from .lifecycle import NotificationLifecycleService
from .schemas import (NotificationOpenRead, PushInstallationDetach, PushInstallationRead, PushInstallationWrite,
    ReminderRead, ReminderWrite)


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


def get_push_registration_service(request: Request, session: AsyncSession = Depends(get_session)) -> NotificationLifecycleService:
    settings = request.app.state.settings
    return NotificationLifecycleService(session, token_key=settings.push_token_key, push_available=settings.push_provider == "fcm")


@router.post("/installations", response_model=PushInstallationRead)
async def register_push_installation(payload: PushInstallationWrite,
    current_user: CurrentUser = Depends(require_current_user),
    service: NotificationLifecycleService = Depends(get_push_registration_service)) -> PushInstallationRead:
    return await service.register(current_user, payload)


@router.post("/installations/{installation_id}/detach", status_code=status.HTTP_204_NO_CONTENT)
async def detach_push_installation(installation_id: UUID, payload: PushInstallationDetach,
    current_user: CurrentUser = Depends(require_current_user),
    service: NotificationLifecycleService = Depends(get_push_registration_service)) -> Response:
    await service.detach(current_user, installation_id, payload.installation_secret.get_secret_value())
    return Response(status_code=204)


@router.get("/preferences")
async def notification_preferences(current_user: CurrentUser = Depends(require_current_user),
    service: NotificationLifecycleService = Depends(get_push_registration_service)) -> dict[str, bool]:
    return await service.preferences(current_user.user_id)


@router.patch("/preferences/{category}")
async def update_notification_preference(category: str, payload: ReminderWrite,
    current_user: CurrentUser = Depends(require_current_user),
    service: NotificationLifecycleService = Depends(get_push_registration_service)) -> dict[str, bool]:
    return await service.set_preference(current_user, category, payload.enabled, payload.installation_id)


@router.get("/appointments/{appointment_id}/reminder", response_model=ReminderRead)
async def read_appointment_reminder(appointment_id: UUID, current_user: CurrentUser = Depends(require_current_user),
    service: NotificationLifecycleService = Depends(get_push_registration_service)) -> ReminderRead:
    value = await service.appointment_reminder(current_user.user_id, appointment_id)
    return ReminderRead(enabled=value is not None and value.send_status in {"scheduled", "pending"},
        status=value.send_status if value else "disabled", trigger_at=value.trigger_at if value else None,
        notification_id=value.id if value else None)


@router.put("/appointments/{appointment_id}/reminder", response_model=ReminderRead)
async def update_appointment_reminder(appointment_id: UUID, payload: ReminderWrite,
    current_user: CurrentUser = Depends(require_current_user),
    service: NotificationLifecycleService = Depends(get_push_registration_service)) -> ReminderRead:
    value = await service.set_appointment_reminder(current_user, appointment_id, enabled=payload.enabled, installation_id=payload.installation_id)
    return ReminderRead(enabled=value.send_status in {"scheduled", "pending"}, status=value.send_status,
        trigger_at=value.trigger_at, notification_id=value.id)


@router.post("/{notification_id}/open", response_model=NotificationOpenRead)
async def open_notification(notification_id: UUID, request: Request, current_user: CurrentUser = Depends(require_current_user),
    service: NotificationLifecycleService = Depends(get_push_registration_service)) -> NotificationOpenRead:
    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
        gateway = RuntimeConversationGateway(client, request.app.state.settings.care_report_runtime_url)
        return await service.open_notification(current_user, notification_id,
            verify_conversation=lambda owner, thread: gateway.verify_owner(owner, thread, request.headers.get("Authorization", "")))


@router.post("/read-all")
async def mark_all_notifications_read(current_user: CurrentUser = Depends(require_current_user),
    service: NotificationsService = Depends(get_notifications_service)) -> dict[str, int]:
    return {"updated_count": await service.mark_all_read(owner_user_id=current_user.user_id)}


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    status_filter: str | None = Query(default=None, alias="status"),
    notification_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
    current_user: CurrentUser = Depends(require_current_user),
    service: NotificationsService = Depends(get_notifications_service),
) -> NotificationListResponse:
    return await service.list_page(
        owner_user_id=current_user.user_id,
        status=status_filter,
        notification_type=notification_type,
        limit=limit,
        cursor=cursor,
    )


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
