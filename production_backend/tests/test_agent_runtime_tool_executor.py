import asyncio
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.modules.agent_runtime.models import AgentToolCall
from production_backend.app.modules.agent_runtime.tools import ToolExecutor, ToolHandlerContext, default_tool_registry
from production_backend.app.modules.auth import CurrentUser


def test_tool_executor_persists_safe_args_and_output() -> None:
    actor = _user(permissions={"profile:read:self"})
    repository = FakeToolRepository()
    executor = ToolExecutor(
        registry=default_tool_registry(),
        repository=repository,
        handlers={"profile.read": profile_read_handler},
    )

    result = asyncio.run(
        executor.execute(
            actor=actor,
            run_id=uuid4(),
            tool_name="profile.read",
            call_id="call-1",
            args={"owner_user_id": str(actor.user_id), "api_token": "secret-token"},
        )
    )

    assert result.tool_call.status == "completed"
    assert repository.tool_call.safe_args["api_token"] == "[redacted]"
    assert result.safe_output["profile"]["name"] == "Mai"
    assert repository.output.safe_output["session_token"] == "[redacted]"


def test_tool_executor_denies_missing_permission_before_persisting_call() -> None:
    repository = FakeToolRepository()
    executor = ToolExecutor(
        registry=default_tool_registry(),
        repository=repository,
        handlers={"profile.read": profile_read_handler},
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            executor.execute(
                actor=_user(roles={"limited"}),
                run_id=uuid4(),
                tool_name="profile.read",
                call_id="call-1",
                args={},
            )
        )

    assert exc_info.value.code == "permission_denied"
    assert repository.tool_call is None


def test_tool_executor_blocks_cross_owner_actor_scoped_args() -> None:
    actor = _user(permissions={"profile:read:self"})
    repository = FakeToolRepository()
    executor = ToolExecutor(
        registry=default_tool_registry(),
        repository=repository,
        handlers={"profile.read": profile_read_handler},
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            executor.execute(
                actor=actor,
                run_id=uuid4(),
                tool_name="profile.read",
                call_id="call-1",
                args={"owner_user_id": str(uuid4())},
            )
        )

    assert exc_info.value.code == "owner_scope_violation"
    assert repository.tool_call is None


def test_tool_executor_marks_tool_call_failed_on_handler_error() -> None:
    actor = _user(permissions={"profile:read:self"})
    repository = FakeToolRepository()
    executor = ToolExecutor(
        registry=default_tool_registry(),
        repository=repository,
        handlers={"profile.read": failing_handler},
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            executor.execute(
                actor=actor,
                run_id=uuid4(),
                tool_name="profile.read",
                call_id="call-1",
                args={},
            )
        )

    assert exc_info.value.code == "dependency_failed"
    assert repository.tool_call.status == "failed"
    assert repository.tool_call.error_code == "dependency_failed"


def test_tool_executor_records_success_and_authorization_failure_metrics() -> None:
    actor = _user(permissions={"profile:read:self"})
    metrics = RequestMetrics()
    executor = ToolExecutor(
        registry=default_tool_registry(),
        repository=FakeToolRepository(),
        handlers={"profile.read": profile_read_handler},
        metrics=metrics,
    )

    asyncio.run(
        executor.execute(
            actor=actor,
            run_id=uuid4(),
            tool_name="profile.read",
            call_id="call-1",
            args={"owner_user_id": str(actor.user_id)},
        )
    )
    with pytest.raises(ApiError):
        asyncio.run(
            executor.execute(
                actor=_user(roles={"limited"}),
                run_id=uuid4(),
                tool_name="profile.read",
                call_id="call-2",
                args={},
            )
        )

    tool_metrics = metrics.snapshot()["agent_tools"][0]
    assert tool_metrics["tool_name"] == "profile.read"
    assert tool_metrics["outcome_counts"]["completed"] == 1
    assert tool_metrics["outcome_counts"]["failed"] == 1
    assert tool_metrics["error_code_counts"]["permission_denied"] == 1


async def profile_read_handler(context: ToolHandlerContext):
    return {"profile": {"name": "Mai"}, "session_token": "secret-token"}


async def failing_handler(context: ToolHandlerContext):
    raise ApiError(code="dependency_failed", message="Profile service unavailable.", status=503)


def _user(*, roles: set[str] | None = None, permissions: set[str] | None = None) -> CurrentUser:
    user_id = uuid4()
    return CurrentUser(
        user_id=user_id,
        subject=str(user_id),
        session_id="session",
        token_id="token",
        roles=frozenset(roles or {"user"}),
        permissions=frozenset(permissions or set()),
    )


class FakeToolRepository:
    def __init__(self) -> None:
        self.tool_call = None
        self.output = None

    async def start_tool_call(self, **kwargs):
        self.tool_call = AgentToolCall(
            id=uuid4(),
            run_id=kwargs["run_id"],
            tool_name=kwargs["tool_name"],
            call_id=kwargs["call_id"],
            status="started",
            safe_args=kwargs["safe_args"],
            started_at=kwargs["started_at"],
            error_code="",
        )
        return self.tool_call

    async def complete_tool_call(self, **kwargs):
        self.tool_call.status = "completed"
        self.tool_call.completed_at = kwargs["completed_at"]
        return self.tool_call

    async def fail_tool_call(self, **kwargs):
        self.tool_call.status = "failed"
        self.tool_call.completed_at = kwargs["completed_at"]
        self.tool_call.error_code = kwargs["error_code"]
        return self.tool_call

    async def create_tool_output(self, **kwargs):
        self.output = FakeToolOutput(tool_call_id=kwargs["tool_call_id"], safe_output=kwargs["safe_output"])
        return self.output


class FakeToolOutput:
    def __init__(self, *, tool_call_id, safe_output):
        self.tool_call_id = tool_call_id
        self.safe_output = safe_output
