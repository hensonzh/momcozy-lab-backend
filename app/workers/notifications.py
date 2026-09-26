from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..core.settings import Settings
from ..infrastructure.db.session import create_db_engine, create_session_factory
from ..infrastructure.push.provider import PushMessage, PushProvider, PushResult, build_push_provider
from ..modules.auth.models import DeviceSession
from ..modules.notifications.models import Notification, NotificationDelivery, PushInstallation
from ..modules.notifications.lifecycle import NotificationLifecycleService
from ..modules.notifications.repository import NotificationsRepository
from ..modules.notifications.push_registration import SEND_PERMISSIONS, active_installations, token_cipher
from ..modules.users.models import User

LOGGER = logging.getLogger("production_backend.notifications")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def reconcile_notification(session: AsyncSession, value: Notification, *, push_available: bool,
                                  now: Callable[[], datetime]) -> bool:
    """Called under the owner's row lock, also immediately before every send."""
    service = NotificationLifecycleService(session, push_available=push_available, now=now)
    if value.status == "archived":
        value.send_status, value.canceled_at = "canceled", now()
        return False
    if value.related_resource_type != "agent_conversation":
        value.send_status, value.canceled_at = "canceled", now()
        return False
    if value.expires_at is not None and value.expires_at <= now():
        value.send_status = "expired"
        return False
    prefs = await service.preferences(value.owner_user_id)
    if not push_available or not prefs.get(value.category, False) or not await active_installations(session, value.owner_user_id, now=now()):
        value.send_status = "disabled"
        return False
    return True


async def process_next(sessions: async_sessionmaker[AsyncSession], provider: PushProvider | None, *, token_key: str,
                       now: Callable[[], datetime] = utc_now) -> bool:
    async with sessions.begin() as session:
        due = [Notification.send_status.in_(["pending", "scheduled"]),
            func.coalesce(Notification.available_at, Notification.trigger_at) <= now()]
        # Match booking, account reset and registration: owner is the first lock.
        owner = await session.scalar(select(User).join(Notification, Notification.owner_user_id == User.id).where(*due)
            .order_by(Notification.trigger_at, Notification.id).with_for_update(of=User, skip_locked=True).limit(1)
            .execution_options(populate_existing=True))
        if owner is None:
            return False
        value = await session.scalar(select(Notification).where(Notification.owner_user_id == owner.id, *due)
            .order_by(Notification.trigger_at, Notification.id).with_for_update(skip_locked=True).limit(1))
        if value is None:
            return False
        if owner.status != "active":
            value.send_status, value.canceled_at = "canceled", now()
            return True
        if not await reconcile_notification(session, value, push_available=provider is not None, now=now):
            return True
        assert provider is not None and value.expires_at is not None
        installations = await active_installations(session, owner.id, now=now())
        badge_count = await NotificationsRepository(session).unread_count(owner_user_id=owner.id)
        for initial in installations:
            device_session = await session.scalar(select(DeviceSession).where(DeviceSession.id == initial.session_id)
                .with_for_update().execution_options(populate_existing=True))
            device = await session.scalar(select(PushInstallation).where(PushInstallation.id == initial.id)
                .with_for_update().execution_options(populate_existing=True))
            if (device is None or device_session is None or device_session.status != "active" or device_session.user_id != owner.id
                or device.owner_user_id != owner.id or device.session_id != device_session.id or device.permission not in SEND_PERMISSIONS
                or device.encrypted_token is None or device.invalidated_at is not None):
                continue
            delivery = await session.scalar(select(NotificationDelivery).where(NotificationDelivery.notification_id == value.id,
                NotificationDelivery.installation_id == device.id, NotificationDelivery.binding_id == device.binding_id))
            if delivery is None:
                delivery = NotificationDelivery(notification_id=value.id, installation_id=device.id, binding_id=device.binding_id, available_at=now())
                session.add(delivery)
                await session.flush()
            if delivery.status != "pending" or delivery.available_at > now():
                continue
            delivery.attempts += 1
            try:
                raw_token = token_cipher(token_key).decrypt(device.encrypted_token.encode()).decode()
                result = await provider.send(token=raw_token, message=PushMessage(notification_id=value.id,
                    binding_id=device.binding_id, expires_at=value.expires_at, badge_count=badge_count))
            except Exception:
                result = PushResult("retry", error_code="provider_unavailable")
            delivery.error_code = result.error_code
            if result.outcome == "sent":
                delivery.status, delivery.sent_at, delivery.provider_message_id = "sent", now(), result.message_id
                value.sent_at = value.sent_at or now()
            elif result.outcome == "invalid":
                delivery.status, device.invalidated_at = "invalid", now()
            elif result.outcome == "failed" or delivery.attempts >= 5:
                delivery.status = "failed"
            else:
                delay = max(result.retry_after_seconds, min(900, 30 * 2 ** (delivery.attempts - 1))) + secrets.randbelow(5)
                delivery.available_at = now() + timedelta(seconds=delay)
        await session.flush()
        deliveries = list(await session.scalars(select(NotificationDelivery).where(NotificationDelivery.notification_id == value.id)))
        current_bindings = {(item.id, item.binding_id) for item in await active_installations(session, owner.id, now=now())}
        for item in deliveries:
            if item.status == "pending" and (item.installation_id, item.binding_id) not in current_bindings:
                item.status, item.error_code = "canceled", "binding_unavailable"
        pending = [item.available_at for item in deliveries if item.status == "pending"]
        if pending:
            value.send_status, value.available_at = "pending", min(pending)
        else:
            value.send_status = "sent" if any(item.status == "sent" for item in deliveries) else "failed"
            value.available_at = None
        return True


async def run() -> None:
    settings = Settings.from_env()
    settings.validate_for_startup()
    engine = create_db_engine(settings)
    sessions = create_session_factory(engine)
    try:
        async with httpx.AsyncClient() as client:
            provider = build_push_provider(settings, client)
            while True:
                try:
                    sent = await process_next(sessions, provider, token_key=settings.push_token_key)
                    if not sent:
                        await asyncio.sleep(1)
                except Exception:
                    # Do not include exception text: transport errors may contain tokens.
                    LOGGER.warning("Notification worker transaction failed; retrying")
                    await asyncio.sleep(2)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
