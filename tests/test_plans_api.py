from datetime import date
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import CurrentUser
from app.modules.plans.models import Plan, PlanTask
from app.modules.plans.router import get_plans_service


def test_create_plan_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).post("/v1/plans", json={"title": "Birth plan"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_list_plans_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).get("/v1/plans?plan_type=postpartum_recovery")

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


def test_list_plans_filters_by_type_status_and_limit_with_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakePlansService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_plans_service] = lambda: fake_service

    response = TestClient(app).get("/v1/plans?plan_type=postpartum_recovery&status=active&limit=5")

    assert response.status_code == 200
    assert fake_service.list_plans_kwargs == {
        "owner_user_id": user_id,
        "plan_type": "postpartum_recovery",
        "status": "active",
        "limit": 5,
    }


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
        json={"title": "Prepare appointment"},
    )
    complete_response = TestClient(app).patch(f"/v1/plans/tasks/{fake_service.task_id}/completion", json={"completed": True})
    skip_response = TestClient(app).patch(
        f"/v1/plans/tasks/{fake_service.task_id}/state",
        json={"state": "skipped"},
    )

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert update_response.status_code == 200
    assert complete_response.status_code == 200
    assert skip_response.status_code == 200
    assert fake_service.create_task_kwargs["owner_user_id"] == user_id
    assert fake_service.list_tasks_kwargs["limit"] == 10
    assert fake_service.update_task_kwargs["owner_user_id"] == user_id
    assert fake_service.update_task_kwargs["updates"] == {"title": "Prepare appointment"}
    assert fake_service.update_task_kwargs["request_id"] == "req_task_update"
    assert fake_service.set_task_completed_kwargs["owner_user_id"] == user_id
    assert fake_service.set_task_state_kwargs["owner_user_id"] == user_id
    assert fake_service.set_task_state_kwargs["task_id"] == fake_service.task_id
    assert fake_service.set_task_state_kwargs["state"] == "skipped"
    assert fake_service.set_task_state_kwargs["request_id"].startswith("req_")




def _override_current_user(app, user_id: UUID) -> None:
    from app.api.dependencies import require_current_user

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
        self.list_plans_kwargs = {}
        self.create_task_kwargs = {}
        self.list_tasks_kwargs = {}
        self.update_task_kwargs = {}
        self.set_task_completed_kwargs = {}
        self.set_task_state_kwargs = {}
        self.update_plan_todo_completion_kwargs = {}

    async def create_plan(self, **kwargs):
        self.create_plan_kwargs = kwargs
        return self._plan()

    async def list_plans(self, **kwargs):
        self.list_plans_kwargs = kwargs
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

    async def set_task_state(self, **kwargs):
        self.set_task_state_kwargs = kwargs
        task = self._task()
        task.status = kwargs["state"]
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
        return Plan(id=self.plan_id, owner_user_id=self.user_id, title="Birth plan", plan_type="", summary="", source="manual", payload={}, status="active", version=1)

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
