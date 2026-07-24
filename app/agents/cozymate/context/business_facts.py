from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.modules.auth import CurrentUser
from app.agent_runtime.tools import ToolHandler, ToolHandlerContext, ToolResult, strip_instructional_tool_output_keys

from ..service_skills import ServiceSkillId


@dataclass(frozen=True)
class BusinessFactsProjectorConfig:
    default_limit: int = 5
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

    async def project(
        self,
        *,
        actor: CurrentUser,
        run_id: UUID,
        service_skill_id: ServiceSkillId | None,
    ) -> dict[str, Any]:
        if service_skill_id is None:
            return {}
        sources = _sources_for_skill(skill_id=service_skill_id, config=self.config)
        if not sources:
            return {}

        loaded_at_datetime = self.clock().astimezone(timezone.utc)
        loaded_at = loaded_at_datetime.isoformat()
        facts: dict[str, Any] = {
            "schema_version": "v1",
            "loaded_at": loaded_at,
            "service_skill_id": service_skill_id.value,
            "sources": [],
        }

        for source in sources:
            if source.tool_name not in self.handlers:
                continue
            if source.tool_name == "profile_read":
                source = BusinessFactSource(
                    tool_name=source.tool_name,
                    context_key=source.context_key,
                    args={
                        **source.args,
                        "runtime_local_date": loaded_at_datetime.date().isoformat(),
                    },
                )
            source, payload = await self._project_source(actor=actor, run_id=run_id, source=source)
            facts[source.context_key] = strip_instructional_tool_output_keys(payload)
            facts["sources"].append({"key": source.context_key, "tool_name": source.tool_name})

        if not facts["sources"]:
            return {}
        return facts

    async def _project_source(
        self, *, actor: CurrentUser, run_id: UUID, source: BusinessFactSource
    ) -> tuple[BusinessFactSource, dict[str, Any]]:
        handler = self.handlers[source.tool_name]
        result = await _maybe_await(
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
        observation = result.to_observation()
        payload = observation if isinstance(observation, dict) else {}
        return source, payload


async def _maybe_await(
    value: Awaitable[ToolResult] | ToolResult,
) -> ToolResult:
    if hasattr(value, "__await__"):
        return await value
    return value


def _sources_for_skill(*, skill_id: ServiceSkillId, config: BusinessFactsProjectorConfig) -> tuple[BusinessFactSource, ...]:
    default_limit = config.default_limit
    if skill_id == ServiceSkillId.MILK_MANAGEMENT:
        return (
            BusinessFactSource("profile_read", "lactation_context"),
            BusinessFactSource(
                "milk_analysis_manage",
                "milk_status",
                {
                    "operation": "review",
                    "detail_level": "summary",
                    "days": config.milk_days,
                    "limit": default_limit,
                },
            ),
        )
    if skill_id == ServiceSkillId.DEVICE_GUIDANCE:
        return (
            BusinessFactSource(
                "profile_read",
                "profile",
                {"infant_scope": "all"},
            ),
        )
    return ()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
