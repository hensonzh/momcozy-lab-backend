import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.notifications.models import Notification, NotificationDelivery, NotificationPreference, PushInstallation
from app.modules.notifications.producer import AgentUpdateProducer
from app.modules.notifications.push_registration import token_cipher
from app.modules.notifications.router import get_agent_update_producer
from app.modules.users.models import User
from app.infrastructure.push.provider import PushResult
from app.workers.notifications import process_next
from tests.product_database import database, postgres


def test_agent_event_requires_runtime_service_identity():
    app = create_app(Settings(app_env="test", agent_runtime_service_api_key="a" * 40))
    response = TestClient(app).post("/v1/internal/agent/notifications/reply-ready", json={
        "run_id": str(uuid4()), "owner_user_id": str(uuid4()), "thread_id": str(uuid4()), "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    assert response.status_code == 401


def test_agent_event_api_passes_validated_ids_only_to_producer():
    from app.modules.notifications.schemas import AgentUpdateRead

    class Producer:
        async def reply_ready(self, *, run_id, owner_user_id, thread_id, completed_at):
            self.ids = (run_id, owner_user_id, thread_id)
            self.completed_at = completed_at
            return AgentUpdateRead(notification_id=uuid4(), send_status="pending")

    producer = Producer()
    app = create_app(Settings(app_env="test", agent_runtime_service_api_key="a" * 40))
    app.dependency_overrides[get_agent_update_producer] = lambda: producer
    ids = (uuid4(), uuid4(), uuid4())
    response = TestClient(app).post("/v1/internal/agent/notifications/reply-ready",
        headers={"X-Service-Key": "a" * 40}, json={"run_id": str(ids[0]), "owner_user_id": str(ids[1]), "thread_id": str(ids[2]), "completed_at": datetime.now(timezone.utc).isoformat()})
    assert response.status_code == 200
    assert producer.ids == ids
    assert producer.completed_at.tzinfo is not None
    assert response.json()["send_status"] == "pending"


@postgres
def test_reply_ready_is_idempotent_and_only_queues_with_an_eligible_installation():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner, other = owners
            run_id, thread_id = uuid4(), uuid4()
            completed_at = datetime.now(timezone.utc)
            async with sessions.begin() as session:
                producer = AgentUpdateProducer(session, push_available=True)
                first = await producer.reply_ready(run_id=run_id, owner_user_id=owner, thread_id=thread_id, completed_at=completed_at)
                assert first.send_status == "in_app"
                assert (await producer.reply_ready(run_id=run_id, owner_user_id=owner, thread_id=thread_id, completed_at=completed_at)).notification_id == first.notification_id
                from app.modules.auth.models import DeviceSession
                device_session = DeviceSession(user_id=owner)
                session.add(device_session)
                await session.flush()
                session.add(PushInstallation(id=uuid4(), secret_hash="x", owner_user_id=owner, session_id=device_session.id,
                    platform="android", permission="authorized", encrypted_token="opaque", token_hash="h", last_seen_at=datetime.now(timezone.utc)))
                await session.flush()
                second_run = uuid4()
                second = await producer.reply_ready(run_id=second_run, owner_user_id=owner, thread_id=thread_id, completed_at=completed_at)
                assert second.send_status == "pending"
                assert (await producer.reply_ready(run_id=second_run, owner_user_id=owner, thread_id=thread_id, completed_at=completed_at)).notification_id == second.notification_id
                stored = await session.get(Notification, second.notification_id)
                stored.send_status = "sent"
                await session.flush()
                replay = await producer.reply_ready(run_id=second_run, owner_user_id=owner,
                    thread_id=thread_id, completed_at=completed_at)
                assert replay.notification_id == second.notification_id and replay.send_status == "sent"
                assert len(list(await session.scalars(select(Notification).where(Notification.owner_user_id == owner)))) == 2
                assert (await producer.reply_ready(run_id=uuid4(), owner_user_id=other, thread_id=thread_id, completed_at=completed_at)).send_status == "in_app"
    asyncio.run(run())


@postgres
def test_reply_ready_is_consumed_by_notification_worker_and_targets_both_platforms():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            key = "test-push-key-" + "x" * 32
            from app.modules.auth.models import DeviceSession
            async with sessions.begin() as session:
                for platform in ("android", "ios"):
                    device = DeviceSession(user_id=owner)
                    session.add(device)
                    await session.flush()
                    session.add(PushInstallation(id=uuid4(), secret_hash="x", owner_user_id=owner, session_id=device.id,
                        platform=platform, permission="authorized", encrypted_token=token_cipher(key).encrypt(platform.encode()).decode(),
                        token_hash=platform, last_seen_at=datetime.now(timezone.utc)))
                await session.flush()
                result = await AgentUpdateProducer(session, push_available=True).reply_ready(
                    run_id=uuid4(), owner_user_id=owner, thread_id=uuid4(), completed_at=datetime.now(timezone.utc))
                assert result.send_status == "pending"
            class Provider:
                def __init__(self):
                    self.tokens = []
                async def send(self, *, token, message):
                    self.tokens.append(token)
                    assert message.notification_id == result.notification_id
                    return PushResult("sent", message_id="mocked-fcm-id")
            provider = Provider()
            assert await process_next(sessions, provider, token_key=key)
            assert set(provider.tokens) == {"android", "ios"}
            async with sessions.begin() as session:
                value = await session.get(Notification, result.notification_id)
                assert value.send_status == "sent"
                assert len(list(await session.scalars(select(NotificationDelivery)))) == 2
    asyncio.run(run())


@postgres
def test_reply_ready_respects_preference_and_account_status():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            async with sessions.begin() as session:
                from app.modules.auth.models import DeviceSession
                device_session = DeviceSession(user_id=owner)
                session.add(device_session)
                await session.flush()
                session.add(PushInstallation(id=uuid4(), secret_hash="x", owner_user_id=owner, session_id=device_session.id,
                    platform="ios", permission="authorized", encrypted_token="opaque", token_hash="h2", last_seen_at=datetime.now(timezone.utc)))
                session.add(NotificationPreference(owner_user_id=owner, category="agent_updates", enabled=False,
                    updated_at=datetime.now(timezone.utc)))
                await session.flush()
                producer = AgentUpdateProducer(session, push_available=True)
                assert (await producer.reply_ready(run_id=uuid4(), owner_user_id=owner, thread_id=uuid4(), completed_at=datetime.now(timezone.utc))).send_status == "in_app"
                (await session.get(User, owner)).status = "disabled"
                await session.flush()
                try:
                    await producer.reply_ready(run_id=uuid4(), owner_user_id=owner, thread_id=uuid4(), completed_at=datetime.now(timezone.utc))
                    raise AssertionError("inactive account must not create a notification")
                except ApiError as exc:
                    assert exc.status == 403
    asyncio.run(run())


@postgres
def test_old_agent_reply_remains_in_inbox_without_historical_push():
    async def run():
        from app.modules.auth.models import DeviceSession
        from datetime import timedelta

        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            async with sessions.begin() as session:
                device = DeviceSession(user_id=owner)
                session.add(device)
                await session.flush()
                session.add(PushInstallation(id=uuid4(), secret_hash="x", owner_user_id=owner, session_id=device.id,
                    platform="ios", permission="authorized", encrypted_token="opaque", token_hash="old-reply",
                    last_seen_at=datetime.now(timezone.utc)))
                await session.flush()
                value = await AgentUpdateProducer(session, push_available=True).reply_ready(
                    run_id=uuid4(), owner_user_id=owner, thread_id=uuid4(),
                    completed_at=datetime.now(timezone.utc) - timedelta(days=2))
                assert value.send_status == "in_app"
                saved = await session.get(Notification, value.notification_id)
                assert saved.available_at is None
    asyncio.run(run())


@postgres
def test_replaying_run_with_changed_owner_or_thread_is_rejected():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner, other = owners
            thread_id, run_id = uuid4(), uuid4()
            completed_at = datetime.now(timezone.utc)
            async with sessions.begin() as session:
                producer = AgentUpdateProducer(session, push_available=False)
                await producer.reply_ready(run_id=run_id, owner_user_id=owner,
                    thread_id=thread_id, completed_at=completed_at)
                for user, thread, timestamp in [
                    (other, thread_id, completed_at),
                    (owner, uuid4(), completed_at),
                    (owner, thread_id, completed_at.replace(year=completed_at.year - 1)),
                ]:
                    try:
                        await producer.reply_ready(run_id=run_id, owner_user_id=user,
                            thread_id=thread, completed_at=timestamp)
                        raise AssertionError("Run identity must not be reused for a different event")
                    except ApiError as exc:
                        assert exc.status == 409
    asyncio.run(run())
