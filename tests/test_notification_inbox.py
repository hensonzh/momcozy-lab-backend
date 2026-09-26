import asyncio
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.modules.notifications.models import Notification
from app.modules.notifications.repository import NotificationsRepository
from app.modules.notifications.service import NotificationsService
from app.modules.users.models import User
from tests.test_account_lifecycle import account_case
from tests.product_database import DATABASE_URL, postgres


@postgres
def test_inbox_cursor_count_and_read_all_exclude_future_archived_and_other_accounts():
    async def run():
        async with account_case(DATABASE_URL) as (sessions, _lifecycle, _mail, clock, _settings):
            async with sessions.kw['bind'].begin() as connection:
                await connection.run_sync(Notification.__table__.create)
            owner, other = uuid4(), uuid4()
            async with sessions.begin() as session:
                session.add_all([User(id=owner), User(id=other)])
                await session.flush()
                for index in range(5):
                    session.add(Notification(owner_user_id=owner, notification_type='appointment_created',
                        created_at=clock[0] - timedelta(minutes=index), status='unread', payload={}))
                future = Notification(owner_user_id=owner, notification_type='appointment_reminder',
                    trigger_at=clock[0] + timedelta(days=1), send_status='scheduled', payload={})
                session.add_all([future, Notification(owner_user_id=owner, notification_type='system', status='archived', payload={}),
                    Notification(owner_user_id=other, notification_type='system', payload={})])
            async with sessions.begin() as session:
                service = NotificationsService(repository=NotificationsRepository(session))
                first = await service.list_page(owner_user_id=owner, limit=2)
                assert len(first.items) == 2 and first.unread_count == 5 and first.next_cursor
                assert all(item.created_at is not None for item in first.items)
                second = await service.list_page(owner_user_id=owner, limit=2, cursor=first.next_cursor)
                last = await service.list_page(owner_user_id=owner, limit=2, cursor=second.next_cursor)
                assert len(last.items) == 1 and last.next_cursor is None
                assert len({item.id for page in [first, second, last] for item in page.items}) == 5
                assert (await service.mark_all_read(owner_user_id=owner)) == 5
                assert (await service.list_page(owner_user_id=owner)).unread_count == 0
                assert (await session.get(Notification, future.id)).status == 'unread'
                assert (await session.scalar(select(Notification).where(Notification.owner_user_id == other))).status == 'unread'
    asyncio.run(run())
