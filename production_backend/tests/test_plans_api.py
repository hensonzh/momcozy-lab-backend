from datetime import date
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.plans.models import Plan, PlanTask
from production_backend.app.modules.plans.router import get_plans_service


def test_create_plan_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).post("/v1/plans", json={"title": "Birth plan"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_create_plan_uses_current_user_request_id_and_idempotency() -> None:
    user_id = uuid4()
    fake_service = FakePlansService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_plans_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/plans",
        headers={"X-Request-ID": "req_plan", "Idempotency-Key": " idem-plan "},
        json={"title": "Birth plan"},
    )

    assert response.status_code == 201
    assert fake_service.create_plan_kwargs["owner_user_id"] == user_id
    assert fake_service.create_plan_kwargs["request_id"] == "req_plan"
    assert fake_service.create_plan_kwargs["idempotency_key"] == "idem-plan"


def test_task_apis_use_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakePlansService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_plans_service] = lambda: fake_service

    create_response = TestClient(app).post("/v1/plans/tasks", json={"title": "Pack bag"})
    list_response = TestClient(app).get("/v1/plans/tasks/list?limit=10")
    update_response = TestClient(app).patch(
        f"/v1/plans/tasks/{fake_service.task_id}",
        headers={"X-Request-ID": "req_task_update"},
        json={"title": "Pack hospital bag"},
    )
    complete_response = TestClient(app).patch(f"/v1/plans/tasks/{fake_service.task_id}/completion", json={"completed": True})

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert update_response.status_code == 200
    assert complete_response.status_code == 200
    assert fake_service.create_task_kwargs["owner_user_id"] == user_id
    assert fake_service.list_tasks_kwargs["limit"] == 10
    assert fake_service.update_task_kwargs["owner_user_id"] == user_id
    assert fake_service.update_task_kwargs["updates"] == {"title": "Pack hospital bag"}
    assert fake_service.update_task_kwargs["request_id"] == "req_task_update"
    assert fake_service.set_task_completed_kwargs["owner_user_id"] == user_id


def _override_current_user(app, user_id: UUID) -> None:
    from production_backend.app.api.dependencies import require_current_user

    async def fake_current_user() -> CurrentUser:
        return CurrentUser(
            user_id=user_id,
            subject=str(user_id),
            session_id="session",
            token_id="token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        )

    app.dependency_overrides[require_current_user] = fake_current_user


class FakePlansService:
    def __init__(self, *, user_id: UUID) -> None:
        self.user_id = user_id
        self.plan_id = uuid4()
        self.task_id = uuid4()
        self.create_plan_kwargs = {}
        self.create_task_kwargs = {}
        self.list_tasks_kwargs = {}
        self.update_task_kwargs = {}
        self.set_task_completed_kwargs = {}

    async def create_plan(self, **kwargs):
        self.create_plan_kwargs = kwargs
        return self._plan()

    async def list_plans(self, **kwargs):
        return [self._plan()]

    async def get_plan(self, **kwargs):
        return self._plan()

    async def delete_plan(self, **kwargs):
        return None

    async def create_task(self, **kwargs):
        self.create_task_kwargs = kwargs
        return self._task()

    async def list_tasks(self, **kwargs):
        self.list_tasks_kwargs = kwargs
        return [self._task()]

    async def set_task_completed(self, **kwargs):
        self.set_task_completed_kwargs = kwargs
        task = self._task()
        task.status = "completed"
        return task

    async def update_task(self, **kwargs):
        self.update_task_kwargs = kwargs
        task = self._task()
        for field, value in kwargs["updates"].items():
            setattr(task, field, value)
        return task

    async def delete_task(self, **kwargs):
        return None

    def _plan(self) -> Plan:
        return Plan(id=self.plan_id, owner_user_id=self.user_id, title="Birth plan", plan_type="", summary="", source="manual", payload={}, status="active")

    def _task(self) -> PlanTask:
        return PlanTask(
            id=self.task_id,
            owner_user_id=self.user_id,
            task_date=date(2026, 7, 2),
            task_time="",
            title="Pack bag",
            description="",
            status="pending",
            payload={},
        )
