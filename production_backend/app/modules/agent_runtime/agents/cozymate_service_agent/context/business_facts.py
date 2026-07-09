from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from production_backend.app.modules.agent_runtime.agents.main_coordinator_agent import RoutingPlan, ServiceSkillId
from production_backend.app.modules.auth import CurrentUser

from ..tools import ToolHandlerContext
from ..tools.executor import ToolHandler
from ..tools.output_policy import strip_instructional_tool_output_keys


@dataclass(frozen=True)
class BusinessFactsProjectorConfig:
    default_limit: int = 5
    recent_limit: int = 3
    milk_days: int = 7


@dataclass(frozen=True)
class BusinessFactSource:
    tool_name: str
    context_key: str
    args: dict[str, Any] = field(default_factory=dict)


class BusinessFactsProjector:
    """Projects small authoritative business facts into the current model turn."""

    def __init__(
        self,
        *,
        handlers: Mapping[str, ToolHandler],
        config: BusinessFactsProjectorConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.handlers = handlers
        self.config = config or BusinessFactsProjectorConfig()
        self.clock = clock or _utcnow

    async def project(self, *, actor: CurrentUser, run_id: UUID, routing_plan: RoutingPlan) -> dict[str, Any]:
        skill_id = routing_plan.selected_service_skill_id
        if skill_id is None:
            return {}
        sources = _sources_for_skill(skill_id=skill_id, config=self.config)
        if not sources:
            return {}

        loaded_at = self.clock().astimezone(timezone.utc).isoformat()
        facts: dict[str, Any] = {
            "schema_version": "v1",
            "loaded_at": loaded_at,
            "service_skill_id": skill_id.value,
            "sources": [],
        }

        for source in sources:
            if source.tool_name not in self.handlers:
                continue
            source, payload = await self._project_source(actor=actor, run_id=run_id, source=source)
            facts[source.context_key] = strip_instructional_tool_output_keys(payload)
            facts["sources"].append({"key": source.context_key, "tool_name": source.tool_name})

        if not facts["sources"]:
            return {}
        return facts

    async def _project_source(self, *, actor: CurrentUser, run_id: UUID, source: BusinessFactSource) -> tuple[BusinessFactSource, dict[str, Any]]:
        handler = self.handlers[source.tool_name]
        payload = await _maybe_await(
            handler(
                ToolHandlerContext(
                    actor=actor,
                    run_id=run_id,
                    tool_name=source.tool_name,
                    call_id=f"context-projection:{source.tool_name}",
                    args=dict(source.args),
                )
            )
        )
        return source, payload


async def _maybe_await(value: Awaitable[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    if hasattr(value, "__await__"):
        return await value
    return value


def _sources_for_skill(*, skill_id: ServiceSkillId, config: BusinessFactsProjectorConfig) -> tuple[BusinessFactSource, ...]:
    default_limit = config.default_limit
    recent_limit = config.recent_limit
    if skill_id == ServiceSkillId.BIRTH_PREP:
        return (
            BusinessFactSource("pregnancy.plan_context.read", "pregnancy", {"limit": default_limit}),
        )
    if skill_id == ServiceSkillId.MILK_MANAGEMENT:
        return (
            BusinessFactSource("profile.read", "profile"),
            BusinessFactSource("records.milk_status.read", "milk_status", {"days": config.milk_days, "limit": default_limit}),
        )
    if skill_id == ServiceSkillId.HEALTH_CONSULTATION:
        return (
            BusinessFactSource("profile.read", "profile"),
            BusinessFactSource("plans.current.read", "plans", {"limit": default_limit}),
            BusinessFactSource("diary.recent.read", "diary", {"limit": recent_limit}),
            BusinessFactSource("records.milk_summary.read", "milk_summary", {"days": config.milk_days, "limit": recent_limit}),
        )
    if skill_id == ServiceSkillId.EMOTION_SUPPORT:
        return (
            BusinessFactSource("profile.read", "profile"),
            BusinessFactSource("diary.recent.read", "diary", {"limit": recent_limit}),
        )
    if skill_id == ServiceSkillId.DEVICE_GUIDANCE:
        return (
            BusinessFactSource("profile.read", "profile"),
            BusinessFactSource("devices.pump_status.read", "devices", {"limit": default_limit}),
        )
    return ()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
