from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ..users.models import User
from .lifecycle import NotificationLifecycleService
from .models import Notification
from .push_registration import active_installations
from .schemas import AgentUpdateRead


class AgentUpdateProducer:
    """Project a committed Agent run into one durable inbox/push task."""

    def __init__(self, session: AsyncSession, *, push_available: bool) -> None:
        self.session = session
        self.push_available = push_available

    async def reply_ready(self, *, run_id: UUID, owner_user_id: UUID, thread_id: UUID, completed_at: datetime) -> AgentUpdateRead:
        now = datetime.now(timezone.utc)
        if completed_at.tzinfo is None or completed_at > now + timedelta(minutes=5):
            raise ApiError(code="validation_failed", message="Invalid completion time.", status=422)
        # Match registration/delivery's owner-first lock order. The unique run key
        # and this lock make repeated Agent callbacks safe after network failures.
        owner = await self.session.scalar(select(User).where(User.id == owner_user_id).with_for_update())
        if owner is None or owner.status != "active":
            raise ApiError(code="account_inactive", message="Account is not active.", status=403)
        key = f"agent_run:{run_id}"
        existing = await self.session.scalar(select(Notification).where(Notification.idempotency_key == key).with_for_update())
        if existing is not None:
            if (existing.owner_user_id != owner_user_id or existing.related_resource_id != thread_id
                or existing.notification_type != "agent_reply_ready" or existing.related_resource_type != "agent_conversation"
                or existing.expires_at != completed_at + timedelta(hours=24)):
                raise ApiError(code="notification_conflict", message="Agent update identity changed.", status=409)
            return AgentUpdateRead(notification_id=existing.id, send_status=existing.send_status)

        expires_at = completed_at + timedelta(hours=24)
        eligible = False
        if self.push_available and expires_at > now:
            enabled = (await NotificationLifecycleService(self.session).preferences(owner_user_id))["agent_updates"]
            eligible = enabled and bool(await active_installations(self.session, owner_user_id, now=now))
        notification = Notification(
            owner_user_id=owner_user_id,
            notification_type="agent_reply_ready",
            title="Your Momcozy update is ready",
            body="Open the app to view it.",
            source="agent-runtime",
            category="agent_updates",
            send_status="pending" if eligible else "in_app",
            trigger_at=now,
            available_at=now if eligible else None,
            expires_at=expires_at,
            related_resource_type="agent_conversation",
            related_resource_id=thread_id,
            idempotency_key=key,
            payload={},
        )
        self.session.add(notification)
        await self.session.flush()
        return AgentUpdateRead(notification_id=notification.id, send_status=notification.send_status)
