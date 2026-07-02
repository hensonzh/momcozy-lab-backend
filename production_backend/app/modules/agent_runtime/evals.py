from __future__ import annotations

from typing import Any
from uuid import UUID

from .models import AgentEvalCase
from .repository import AgentRuntimeRepository
from .replay import AgentReplayService


class AgentEvalService:
    def __init__(self, *, repository: AgentRuntimeRepository, replay_service: AgentReplayService | None = None) -> None:
        self.repository = repository
        self.replay_service = replay_service or AgentReplayService(repository=repository)

    async def create_case_from_run(
        self,
        *,
        run_id: UUID,
        suite: str,
        name: str,
        domain: str = "",
        owner_team: str = "",
    ) -> AgentEvalCase:
        bundle = await self.replay_service.export_run_bundle(run_id=run_id, include_message_content=False)
        return await self.repository.create_eval_case(
            suite=suite,
            name=name,
            domain=domain,
            input_payload={"replay_bundle": bundle},
            expected_behavior={
                "final_run_status": bundle["run"]["status"],
                "event_types": [event["type"] for event in bundle["events"]],
                "action_statuses": [action["status"] for action in bundle["actions"]],
            },
            expected_tool_calls=[{"tool_name": tool_call["tool_name"], "status": tool_call["status"]} for tool_call in bundle["tool_calls"]],
            expected_safety_decision=_last_safety_decision(bundle),
            source_run_id=run_id,
            status="draft",
            owner_team=owner_team,
        )


def _last_safety_decision(bundle: dict[str, Any]) -> str:
    safety_events = bundle.get("safety_events")
    if not isinstance(safety_events, list) or not safety_events:
        return ""
    last = safety_events[-1]
    if not isinstance(last, dict):
        return ""
    return str(last.get("decision") or "")
