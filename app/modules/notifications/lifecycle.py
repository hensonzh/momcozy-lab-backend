from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ..auth.current_user import CurrentUser
from ..users.models import User
from .models import Notification, NotificationPreference, PushInstallation
from .push_registration import PushRegistrationService, active_installations
from .schemas import NotificationOpenRead, NotificationRead, PushInstallationRead, PushInstallationWrite
from .templates import SERVICE_CATEGORIES


class NotificationLifecycleService:
    def __init__(self, session: AsyncSession, *, push_available: bool = True, token_key: str = "", now: Callable[[], datetime] | None = None) -> None:
        self.session, self.push_available = session, push_available
        self.token_key = token_key
        self.now = now or (lambda: datetime.now(timezone.utc))

    async def register(self, actor: CurrentUser, body: PushInstallationWrite) -> PushInstallationRead:
        result = await PushRegistrationService(self.session, token_key=self.token_key, now=self.now).register(actor, body)
        await self._sync_permission(actor.user_id)
        return result.model_copy(update={"push_available": self.push_available})

    async def detach(self, actor: CurrentUser, installation_id: UUID, secret: str) -> None:
        await PushRegistrationService(self.session, token_key=self.token_key, now=self.now).detach(actor, installation_id, secret)
        await self._sync_permission(actor.user_id)

    async def _sync_permission(self, owner: UUID) -> None:
        if not self.push_available or not await active_installations(self.session, owner, now=self.now()):
            await self.session.execute(update(Notification).where(Notification.owner_user_id == owner,
                Notification.send_status.in_(["scheduled", "pending"])).values(send_status="disabled"))

    async def open_notification(self, actor: CurrentUser, identifier: UUID, *, verify_conversation: Callable[[UUID, UUID], Awaitable[None]] | None = None) -> NotificationOpenRead:
        await self.lock_owner(actor.user_id)
        value = await self.session.scalar(select(Notification).where(Notification.id == identifier,
            Notification.owner_user_id == actor.user_id, Notification.status != "archived"))
        if value is None or (value.trigger_at is not None and value.trigger_at > self.now()):
            raise ApiError(code="not_found", message="Notification not found.", status=404)
        route = None
        if value.related_resource_type == "agent_conversation" and value.related_resource_id:
            if verify_conversation is None:
                raise ApiError(code="conversation_unavailable", message="The conversation service is unavailable.", status=503)
            try:
                await verify_conversation(actor.user_id, value.related_resource_id)
                route = f"/?conversationId={value.related_resource_id}"
            except ApiError as error:
                if error.status not in {403, 404}:
                    raise
        value.status, value.read_at = "read", value.read_at or self.now()
        await self.session.flush()
        return NotificationOpenRead(notification=NotificationRead.model_validate(value), route=route, resource_available=route is not None)

    async def lock_owner(self, owner: UUID) -> User:
        user = await self.session.scalar(select(User).where(User.id == owner).with_for_update().execution_options(populate_existing=True))
        if user is None or user.status != "active":
            raise ApiError(code="account_inactive", message="Account is not active.", status=403)
        return user

    async def preferences(self, owner: UUID) -> dict[str, bool]:
        values = {category: True for category in SERVICE_CATEGORIES}
        values["marketing"] = False
        for row in await self.session.scalars(select(NotificationPreference).where(NotificationPreference.owner_user_id == owner)):
            values[row.category] = row.enabled
        return values

    async def require_current_permission(self, actor: CurrentUser, installation_id: UUID) -> PushInstallation:
        if not self.push_available:
            raise ApiError(code="push_unavailable", message="Background notifications are unavailable. Your inbox is still available.", status=503)
        for value in await active_installations(self.session, actor.user_id, now=self.now()):
            if value.id == installation_id and str(value.session_id) == actor.session_id and value.last_seen_at >= self.now() - timedelta(minutes=5):
                return value
        raise ApiError(code="notification_permission_required", message="Enable notifications in system settings to receive background reminders.", status=409)

    async def set_preference(self, actor: CurrentUser, category: str, enabled: bool, installation_id: UUID | None) -> dict[str, bool]:
        if category not in SERVICE_CATEGORIES:
            raise ApiError(code="validation_failed", message="This notification category is not available.", status=422)
        await self.lock_owner(actor.user_id)
        if enabled:
            if installation_id is None:
                raise ApiError(code="notification_permission_required", message="Check notification permission before enabling reminders.", status=409)
            await self.require_current_permission(actor, installation_id)
        await self.session.execute(insert(NotificationPreference).values(owner_user_id=actor.user_id, category=category,
            enabled=enabled, updated_at=self.now()).on_conflict_do_update(index_elements=["owner_user_id", "category"],
                set_={"enabled": enabled, "updated_at": self.now()}))
        if not enabled:
            await self.session.execute(update(Notification).where(Notification.owner_user_id == actor.user_id,
                Notification.category == category, Notification.send_status.in_(["pending", "scheduled"]))
                .values(send_status="disabled"))
        return await self.preferences(actor.user_id)
