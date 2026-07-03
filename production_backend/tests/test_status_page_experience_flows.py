import asyncio
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from production_backend.app.modules.notifications.models import Notification
from production_backend.app.modules.notifications.service import NotificationsService
from production_backend.app.modules.plans.models import Plan, PlanTask
from production_backend.app.modules.plans.service import PlansService
from production_backend.app.modules.profiles.models import InfantProfile, UserProfile
from production_backend.app.modules.profiles.service import ProfileService
from production_backend.app.modules.records.models import FeedingRecord, PumpingRecord
from production_backend.app.modules.records.service import RecordsService
from production_backend.app.modules.status_page.service import StatusPageService


def test_status_page_main_flow_reflects_home_snapshot_and_user_actions() -> None:
    asyncio.run(_run_status_page_main_flow())


async def _run_status_page_main_flow() -> None:
    owner_user_id = uuid4()
    other_user_id = uuid4()
    day = date(2026, 7, 3)
    start_at = datetime(2026, 7, 3, tzinfo=timezone.utc)
    next_midnight = datetime(2026, 7, 4, tzinfo=timezone.utc)
    profile_repository = InMemoryProfileRepository()
    records_repository = InMemoryRecordsRepository(profile_repository=profile_repository)
    plans_repository = InMemoryPlansRepository()
    notifications_repository = InMemoryNotificationsRepository()
    audit_service = FlowAuditService()
    profile_service = ProfileService(repository=profile_repository, audit_service=audit_service)
    records_service = RecordsService(repository=records_repository, audit_service=audit_service)
    plans_service = PlansService(repository=plans_repository, audit_service=audit_service)
    notifications_service = NotificationsService(repository=notifications_repository, audit_service=audit_service)
    status_service = StatusPageService(
        profile_service=profile_service,
        records_service=records_service,
        plans_service=plans_service,
        notifications_service=notifications_service,
    )

    await profile_service.update_user_profile(
        user_id=owner_user_id,
        values={"display_name": "Mia"},
        request_id="req_profile",
    )
    await profile_service.update_status_summary(
        user_id=owner_user_id,
        lactation_advice="Keep pumping comfortable.",
        feeding_advice="Follow hunger cues.",
        daily_summary="Two pumping sessions today.",
        request_id="req_summary",
    )
    infant = await profile_service.create_infant(
        owner_user_id=owner_user_id,
        infant_name="Baby",
        birth_date=date(2026, 6, 1),
        request_id="req_infant",
    )
    other_infant = await profile_service.create_infant(
        owner_user_id=other_user_id,
        infant_name="Other baby",
        birth_date=date(2026, 5, 1),
    )
    await records_service.create_feeding(
        owner_user_id=owner_user_id,
        infant_id=infant.id,
        feed_time=start_at.replace(hour=7, minute=15),
        feed_type="bottle",
        volume_ml=80,
        title="Morning bottle",
        request_id="req_feeding",
    )
    await records_service.create_feeding(
        owner_user_id=owner_user_id,
        infant_id=infant.id,
        feed_time=next_midnight,
        feed_type="bottle",
        volume_ml=55,
        title="Next day boundary bottle",
    )
    await records_service.create_feeding(
        owner_user_id=other_user_id,
        infant_id=other_infant.id,
        feed_time=start_at.replace(hour=8),
        feed_type="bottle",
        volume_ml=999,
    )
    first_pumping = await records_service.create_pumping(
        owner_user_id=owner_user_id,
        pump_start_time=start_at.replace(hour=9),
        milk_volume_ml=95,
        pump_type="electric",
        duration_seconds=1200,
        source="manual",
        title="Morning pumping",
        request_id="req_pump_1",
    )
    second_pumping = await records_service.create_pumping(
        owner_user_id=owner_user_id,
        pump_start_time=start_at.replace(hour=14, minute=30),
        milk_volume_ml=62.5,
        pump_type="electric",
        duration_seconds=900,
        source="manual",
        title="Afternoon pumping",
        request_id="req_pump_2",
    )
    await records_service.create_pumping(
        owner_user_id=other_user_id,
        pump_start_time=start_at.replace(hour=10),
        milk_volume_ml=500,
        source="manual",
    )
    plan = await plans_service.create_plan(
        owner_user_id=owner_user_id,
        plan_type="birth_prep",
        title="Hospital bag",
        request_id="req_plan",
    )
    first_task = await plans_service.create_task(
        owner_user_id=owner_user_id,
        plan_id=plan.id,
        task_date=day,
        task_time="09:00",
        title="Pack nursing bra",
        request_id="req_task_1",
    )
    await plans_service.create_task(
        owner_user_id=owner_user_id,
        plan_id=plan.id,
        task_date=day,
        task_time="15:00",
        title="Charge pump",
        request_id="req_task_2",
    )
    await plans_service.create_task(
        owner_user_id=other_user_id,
        task_date=day,
        task_time="12:00",
        title="Other user task",
    )
    first_notification = await notifications_service.create_notification(
        owner_user_id=owner_user_id,
        notification_type="feeding_due",
        title="Feeding due",
        body="Bottle is due",
        request_id="req_notify_1",
        actor_service="scheduler",
    )
    await notifications_service.create_notification(
        owner_user_id=owner_user_id,
        notification_type="pumping_due",
        title="Pumping due",
        body="Pump session due",
        request_id="req_notify_2",
        actor_service="scheduler",
    )
    await notifications_service.create_notification(
        owner_user_id=other_user_id,
        notification_type="feeding_due",
        title="Other user notification",
    )

    snapshot = await status_service.get_today_status(owner_user_id=owner_user_id, day=day)

    assert snapshot.profile.display_name == "Mia"
    assert snapshot.profile.daily_summary == "Two pumping sessions today."
    assert snapshot.profile.lactation_advice == "Keep pumping comfortable."
    assert snapshot.infant_count == 1
    assert snapshot.feeding_count == 1
    assert snapshot.pumping_count == 2
    assert snapshot.pumped_milk_volume_ml == 157.5
    assert snapshot.task_count == 2
    assert snapshot.completed_task_count == 0
    assert snapshot.pending_task_count == 2
    assert snapshot.unread_notification_count == 2

    await plans_service.set_task_completed(
        owner_user_id=owner_user_id,
        task_id=first_task.id,
        completed=True,
        request_id="req_task_complete",
    )
    await notifications_service.set_read_state(
        owner_user_id=owner_user_id,
        notification_id=first_notification.id,
        read=True,
        request_id="req_notify_read",
    )
    await records_service.delete_pumping(
        owner_user_id=owner_user_id,
        record_id=second_pumping.id,
        request_id="req_pump_delete",
    )

    snapshot_after_actions = await status_service.get_today_status(owner_user_id=owner_user_id, day=day)

    assert first_pumping.id != second_pumping.id
    assert snapshot_after_actions.feeding_count == 1
    assert snapshot_after_actions.pumping_count == 1
    assert snapshot_after_actions.pumped_milk_volume_ml == 95
    assert snapshot_after_actions.task_count == 2
    assert snapshot_after_actions.completed_task_count == 1
    assert snapshot_after_actions.pending_task_count == 1
    assert snapshot_after_actions.unread_notification_count == 1
    assert "profiles.status_summary.update" in [entry["action"] for entry in audit_service.entries]
    assert audit_service.entries[-2]["action"] == "notifications.read"
    assert audit_service.entries[-1]["action"] == "records.pumping.delete"


class InMemoryProfileRepository:
    def __init__(self) -> None:
        self.profiles: dict[UUID, UserProfile] = {}
        self.infants: list[InfantProfile] = []

    async def get_user_profile(self, *, user_id: UUID):
        return self.profiles.get(user_id)

    async def upsert_user_profile(self, *, user_id: UUID, values: dict):
        profile = self.profiles.get(user_id) or UserProfile(id=uuid4(), user_id=user_id)
        for field, value in values.items():
            setattr(profile, field, value)
        self.profiles[user_id] = profile
        return profile

    async def list_infants(self, *, owner_user_id: UUID):
        return [
            infant
            for infant in self.infants
            if infant.owner_user_id == owner_user_id and infant.status == "active" and infant.deleted_at is None
        ]

    async def get_infant_for_owner(self, *, infant_id: UUID, owner_user_id: UUID):
        return next(
            (
                infant
                for infant in self.infants
                if infant.id == infant_id
                and infant.owner_user_id == owner_user_id
                and infant.status == "active"
                and infant.deleted_at is None
            ),
            None,
        )

    async def create_infant(self, **kwargs):
        infant = InfantProfile(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            infant_name=kwargs["infant_name"],
            sex=kwargs["sex"],
            birth_date=kwargs["birth_date"],
            status="active",
            deleted_at=None,
        )
        self.infants.append(infant)
        return infant


class InMemoryRecordsRepository:
    def __init__(self, *, profile_repository: InMemoryProfileRepository) -> None:
        self.profile_repository = profile_repository
        self.feedings: list[FeedingRecord] = []
        self.pumpings: list[PumpingRecord] = []

    async def infant_belongs_to_owner(self, *, infant_id: UUID, owner_user_id: UUID) -> bool:
        return await self.profile_repository.get_infant_for_owner(
            infant_id=infant_id,
            owner_user_id=owner_user_id,
        ) is not None

    async def create_feeding(self, **kwargs):
        record = FeedingRecord(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            infant_id=kwargs["infant_id"],
            feed_time=kwargs["feed_time"],
            feed_type=kwargs["feed_type"],
            feed_action=kwargs["feed_action"],
            volume_ml=kwargs["volume_ml"],
            duration_seconds=kwargs["duration_seconds"],
            title=kwargs["title"],
            status="active",
            deleted_at=None,
        )
        self.feedings.append(record)
        return record

    async def list_feedings(self, *, owner_user_id: UUID, start_at, end_at, limit: int):
        records = [
            record
            for record in self.feedings
            if record.owner_user_id == owner_user_id
            and record.status == "active"
            and record.deleted_at is None
            and (start_at is None or record.feed_time >= start_at)
            and (end_at is None or record.feed_time < end_at)
        ]
        return sorted(records, key=lambda record: record.feed_time, reverse=True)[:limit]

    async def create_pumping(self, **kwargs):
        record = PumpingRecord(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            pump_start_time=kwargs["pump_start_time"],
            pump_end_time=kwargs["pump_end_time"],
            milk_volume_ml=kwargs["milk_volume_ml"],
            pump_type=kwargs["pump_type"],
            duration_seconds=kwargs["duration_seconds"],
            source=kwargs["source"],
            title=kwargs["title"],
            status="active",
            deleted_at=None,
        )
        self.pumpings.append(record)
        return record

    async def get_pumping_for_owner(self, *, record_id: UUID, owner_user_id: UUID):
        return next(
            (
                record
                for record in self.pumpings
                if record.id == record_id
                and record.owner_user_id == owner_user_id
                and record.status == "active"
                and record.deleted_at is None
            ),
            None,
        )

    async def list_pumpings(self, *, owner_user_id: UUID, start_at, end_at, limit: int):
        records = [
            record
            for record in self.pumpings
            if record.owner_user_id == owner_user_id
            and record.status == "active"
            and record.deleted_at is None
            and (start_at is None or record.pump_start_time >= start_at)
            and (end_at is None or record.pump_start_time < end_at)
        ]
        return sorted(records, key=lambda record: record.pump_start_time, reverse=True)[:limit]

    async def soft_delete_pumping(self, *, record_id: UUID, owner_user_id: UUID, deleted_at):
        record = await self.get_pumping_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            return None
        record.status = "deleted"
        record.deleted_at = deleted_at
        return record


class InMemoryPlansRepository:
    def __init__(self) -> None:
        self.plans: list[Plan] = []
        self.tasks: list[PlanTask] = []

    async def create_plan(self, **kwargs):
        plan = Plan(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            plan_type=kwargs["plan_type"],
            title=kwargs["title"],
            summary=kwargs["summary"],
            source=kwargs["source"],
            payload=kwargs["payload"],
            status="active",
            deleted_at=None,
        )
        self.plans.append(plan)
        return plan

    async def get_plan_for_owner(self, *, plan_id: UUID, owner_user_id: UUID):
        return next(
            (
                plan
                for plan in self.plans
                if plan.id == plan_id
                and plan.owner_user_id == owner_user_id
                and plan.status == "active"
                and plan.deleted_at is None
            ),
            None,
        )

    async def create_task(self, **kwargs):
        task = PlanTask(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            plan_id=kwargs["plan_id"],
            task_date=kwargs["task_date"],
            task_time=kwargs["task_time"],
            title=kwargs["title"],
            description=kwargs["description"],
            payload=kwargs["payload"],
            status="pending",
            deleted_at=None,
        )
        self.tasks.append(task)
        return task

    async def list_tasks(self, *, owner_user_id: UUID, task_date: date | None, status: str | None, limit: int):
        tasks = [
            task
            for task in self.tasks
            if task.owner_user_id == owner_user_id
            and task.deleted_at is None
            and (task_date is None or task.task_date == task_date)
            and (status is None or task.status == status)
        ]
        return tasks[:limit]

    async def get_task_for_owner(self, *, task_id: UUID, owner_user_id: UUID):
        return next(
            (
                task
                for task in self.tasks
                if task.id == task_id and task.owner_user_id == owner_user_id and task.deleted_at is None
            ),
            None,
        )

    async def set_task_completed(self, *, task_id: UUID, owner_user_id: UUID, completed: bool, completed_at):
        task = await self.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            return None
        task.status = "completed" if completed else "pending"
        task.completed_at = completed_at
        return task


class InMemoryNotificationsRepository:
    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    async def create_notification(self, **kwargs):
        notification = Notification(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            notification_type=kwargs["notification_type"],
            title=kwargs["title"],
            body=kwargs["body"],
            status="unread",
            source=kwargs["source"],
            payload=kwargs["payload"],
            delivered_at=kwargs["delivered_at"],
            read_at=None,
        )
        self.notifications.append(notification)
        return notification

    async def list_for_owner(self, *, owner_user_id: UUID, status: str | None, notification_type: str | None, limit: int):
        notifications = [
            notification
            for notification in self.notifications
            if notification.owner_user_id == owner_user_id
            and (status is None or notification.status == status)
            and (notification_type is None or notification.notification_type == notification_type)
        ]
        return notifications[:limit]

    async def get_for_owner(self, *, notification_id: UUID, owner_user_id: UUID):
        return next(
            (
                notification
                for notification in self.notifications
                if notification.id == notification_id and notification.owner_user_id == owner_user_id
            ),
            None,
        )

    async def set_read_state(self, *, notification_id: UUID, owner_user_id: UUID, read: bool, read_at):
        notification = await self.get_for_owner(notification_id=notification_id, owner_user_id=owner_user_id)
        if notification is None:
            return None
        notification.status = "read" if read else "unread"
        notification.read_at = read_at
        return notification


class FlowAuditService:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)
