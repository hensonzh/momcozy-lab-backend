from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, request_hash
from .models import Notification
from .repository import NotificationsRepository


NOTIFICATION_CREATE_IDEMPOTENCY_SCOPE = "notifications.create"


class NotificationsService:
    def __init__(
        self,
        *,
        repository: NotificationsRepository,
        audit_service: AuditService | None = None,
        idempotency_service: IdempotencyService | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.idempotency_service = idempotency_service

    async def create_notification(
        self,
        *,
        owner_user_id: UUID,
        notification_type: str,
        title: str = "",
        body: str = "",
        source: str = "system",
        payload: dict[str, Any] | None = None,
        delivered_at: datetime | None = None,
        request_id: str = "",
        idempotency_key: str | None = None,
        actor_service: str = "",
    ) -> Notification:
        normalized_type = _normalize_text(notification_type, field_name="notification_type", max_length=64, required=True)
        normalized_title = _normalize_text(title, field_name="title", max_length=255)
        normalized_body = _normalize_text(body, field_name="body", max_length=2000)
        normalized_source = _normalize_text(source, field_name="source", max_length=64) or "system"
        safe_payload = payload or {}

        idempotency_record = await self._reserve_idempotency(
            owner_user_id=owner_user_id,
            key=idempotency_key,
            payload={
                "notification_type": normalized_type,
                "title": normalized_title,
                "body": normalized_body,
                "source": normalized_source,
                "payload": safe_payload,
                "delivered_at": delivered_at.isoformat() if delivered_at else "",
            },
        )
        if idempotency_record is not None and idempotency_record.response_ref:
            return await self._replay_notification(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        notification = await self.repository.create_notification(
            owner_user_id=owner_user_id,
            notification_type=normalized_type,
            title=normalized_title,
            body=normalized_body,
            source=normalized_source,
            payload=safe_payload,
            delivered_at=delivered_at,
        )
        await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(notification.id))
        await self._audit(
            actor_user_id=None,
            actor_type="service",
            actor_service=actor_service,
            action="notifications.create",
            resource_id=str(notification.id),
            request_id=request_id,
            details={"owner_user_id": str(owner_user_id), "source": normalized_source},
        )
        return notification

    async def list_notifications(
        self,
        *,
        owner_user_id: UUID,
        status: str | None = None,
        notification_type: str | None = None,
        limit: int = 50,
    ) -> list[Notification]:
        self._validate_limit(limit)
        return await self.repository.list_for_owner(
            owner_user_id=owner_user_id,
            status=_optional_filter(status),
            notification_type=_optional_filter(notification_type),
            limit=limit,
        )

    async def set_read_state(
        self,
        *,
        owner_user_id: UUID,
        notification_id: UUID,
        read: bool = True,
        request_id: str = "",
    ) -> Notification:
        notification = await self.repository.set_read_state(
            notification_id=notification_id,
            owner_user_id=owner_user_id,
            read=read,
            read_at=_utcnow() if read else None,
        )
        if notification is None:
            raise ApiError(code="not_found", message="Notification not found.", status=404)
        await self._audit(
            actor_user_id=owner_user_id,
            action="notifications.read" if read else "notifications.unread",
            resource_id=str(notification_id),
            request_id=request_id,
        )
        return notification

    async def archive_notification(self, *, owner_user_id: UUID, notification_id: UUID, request_id: str = "") -> None:
        archived = await self.repository.archive(notification_id=notification_id, owner_user_id=owner_user_id)
        if archived is None:
            raise ApiError(code="not_found", message="Notification not found.", status=404)
        await self._audit(
            actor_user_id=owner_user_id,
            action="notifications.archive",
            resource_id=str(notification_id),
            request_id=request_id,
        )

    async def _reserve_idempotency(self, *, owner_user_id: UUID, key: str | None, payload: dict[str, Any]):
        if not key:
            return None
        if self.idempotency_service is None:
            raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
        decision = await self.idempotency_service.reserve(
            actor_user_id=owner_user_id,
            scope=NOTIFICATION_CREATE_IDEMPOTENCY_SCOPE,
            key=key,
            request_hash=request_hash(payload),
            expires_at=_utcnow() + timedelta(hours=24),
        )
        if decision.status == "replay" and not decision.record.response_ref:
            raise ApiError(code="idempotency_in_progress", message="Request is still in progress.", status=409)
        return decision.record

    async def _complete_idempotency(self, *, idempotency_record, response_ref: str) -> None:
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=response_ref)

    async def _replay_notification(self, *, owner_user_id: UUID, response_ref: str) -> Notification:
        notification = await self.repository.get_for_owner(notification_id=UUID(response_ref), owner_user_id=owner_user_id)
        if notification is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return notification

    async def _audit(
        self,
        *,
        actor_user_id: UUID | None,
        actor_type: str | None = None,
        actor_service: str = "",
        action: str,
        resource_id: str,
        request_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=actor_user_id,
                actor_type=actor_type,
                actor_service=actor_service,
                action=action,
                resource_type="notification",
                resource_id=resource_id,
                request_id=request_id,
                details=details,
            )

    def _validate_limit(self, limit: int) -> None:
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)


def _normalize_text(value: str | None, *, field_name: str, max_length: int, required: bool = False) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise ApiError(code="validation_failed", message=f"{field_name} is required.", status=422)
    if len(normalized) > max_length:
        raise ApiError(code="validation_failed", message=f"{field_name} is too long.", status=422)
    return normalized


def _optional_filter(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
