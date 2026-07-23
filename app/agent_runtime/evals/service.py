from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import UUID

from app.agent_runtime.events.replay import AgentReplayService
from app.agent_runtime.runs.models import AgentEvalCase
from app.agent_runtime.runs.repository import AgentRuntimeRepository


@dataclass(frozen=True)
class AgentEvalTrace:
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    service_skill_id: str = ""


@dataclass(frozen=True)
class AgentEvalFailure:
    category: str
    assertion: str
    expected: str
    observed: str


@dataclass(frozen=True)
class AgentEvalRunResult:
    suite: str
    name: str
    passed: bool
    failures: list[AgentEvalFailure]


@dataclass(frozen=True)
class AgentEvalRuntimeCaseResult:
    execution_result: Any
    trace: AgentEvalTrace
    eval_result: AgentEvalRunResult


class AgentEvalSeedAssertionEngine:
    def __init__(
        self,
        *,
        write_tool_names: frozenset[str] = frozenset(),
        wrapper_agent_id: str = "",
        scene_service_skill_ids: frozenset[str] = frozenset(),
        non_write_tool_actions: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self.write_tool_names = write_tool_names
        self.wrapper_agent_id = wrapper_agent_id
        self.scene_service_skill_ids = scene_service_skill_ids
        self.non_write_tool_actions = dict(non_write_tool_actions or {})

    def evaluate(self, *, case: dict[str, Any], trace: AgentEvalTrace) -> AgentEvalRunResult:
        failures: list[AgentEvalFailure] = []
        failures.extend(_tool_contract_failures(case=case, trace=trace))
        failures.extend(_tool_sequence_failures(case=case, trace=trace))
        failures.extend(_tool_argument_failures(case=case, trace=trace))
        failures.extend(_forbidden_tool_failures(case=case, trace=trace))
        failures.extend(_required_event_failures(case=case, trace=trace))
        failures.extend(
            _service_skill_routing_failures(
                case=case,
                trace=trace,
                wrapper_agent_id=self.wrapper_agent_id,
                scene_service_skill_ids=self.scene_service_skill_ids,
            )
        )
        failures.extend(_confirmation_failures(case=case, trace=trace))
        failures.extend(
            _forbidden_side_effect_failures(
                case=case,
                trace=trace,
                write_tool_names=self.write_tool_names,
                non_write_tool_actions=self.non_write_tool_actions,
            )
        )
        failures.extend(_final_response_failures(case=case, trace=trace))
        return AgentEvalRunResult(
            suite=str(case.get("suite") or ""),
            name=str(case.get("name") or ""),
            passed=not failures,
            failures=failures,
        )


class AgentEvalReplayAssertionRunner:
    def __init__(self, *, assertion_engine: AgentEvalSeedAssertionEngine | None = None) -> None:
        self.assertion_engine = assertion_engine or AgentEvalSeedAssertionEngine()

    def evaluate_bundle(self, *, case: dict[str, Any], replay_bundle: dict[str, Any]) -> AgentEvalRunResult:
        return self.assertion_engine.evaluate(case=case, trace=agent_eval_trace_from_replay_bundle(replay_bundle))


class AgentEvalRuntimeTraceCollector:
    def __init__(self, *, repository: AgentRuntimeRepository, default_service_skill_id: str = "") -> None:
        self.repository = repository
        self.default_service_skill_id = default_service_skill_id

    async def collect(self, *, run_id: UUID, final_text: str = "") -> AgentEvalTrace:
        tool_calls = await self.repository.list_tool_calls_for_run(run_id=run_id)
        events = await self.repository.list_events_for_run(run_id=run_id)
        actions = await self.repository.list_actions_for_run(run_id=run_id)
        run_getter = getattr(self.repository, "get_run", None)
        run = await run_getter(run_id=run_id) if run_getter is not None else None
        return AgentEvalTrace(
            tool_calls=[_tool_call_trace(tool_call) for tool_call in tool_calls],
            events=[_event_trace(event) for event in events],
            actions=[_action_trace(action) for action in actions],
            final_text=final_text,
            service_skill_id=_runtime_trace_service_skill_id(
                run=run,
                events=events,
                default_service_skill_id=self.default_service_skill_id,
            ),
        )


class AgentEvalRuntimeClient:
    def __init__(
        self,
        *,
        executor: Any,
        repository: AgentRuntimeRepository,
        assertion_engine: AgentEvalSeedAssertionEngine | None = None,
        default_service_skill_id: str = "",
    ) -> None:
        self.executor = executor
        self.collector = AgentEvalRuntimeTraceCollector(
            repository=repository,
            default_service_skill_id=default_service_skill_id,
        )
        self.assertion_engine = assertion_engine or AgentEvalSeedAssertionEngine()

    async def execute_case(self, *, run: Any, case: dict[str, Any]) -> AgentEvalRuntimeCaseResult:
        execution_result = await self.executor.execute(run=run)
        trace = await self.collector.collect(run_id=run.id, final_text=str(getattr(execution_result, "final_text", "") or ""))
        eval_result = self.assertion_engine.evaluate(case=case, trace=trace)
        return AgentEvalRuntimeCaseResult(execution_result=execution_result, trace=trace, eval_result=eval_result)


def agent_eval_trace_from_replay_bundle(bundle: dict[str, Any]) -> AgentEvalTrace:
    return AgentEvalTrace(
        tool_calls=_list_of_dicts(bundle.get("tool_calls")),
        events=_list_of_dicts(bundle.get("events")),
        actions=_list_of_dicts(bundle.get("actions")),
        final_text=_last_assistant_text(bundle),
        service_skill_id=_run_service_skill_id(bundle),
    )


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
            expected_tool_calls=[
                {"tool_name": tool_call["tool_name"], "status": tool_call["status"]} for tool_call in bundle["tool_calls"]
            ],
            source_run_id=run_id,
            status="draft",
            owner_team=owner_team,
        )


def _runtime_trace_service_skill_id(*, run: Any, events: list[Any], default_service_skill_id: str = "") -> str:
    return str(getattr(run, "service_skill_id", "") or "").strip() or default_service_skill_id


def _tool_call_trace(tool_call: Any) -> dict[str, Any]:
    return {
        "tool_name": str(getattr(tool_call, "tool_name", "") or ""),
        "status": str(getattr(tool_call, "status", "") or ""),
        "error_code": str(getattr(tool_call, "error_code", "") or ""),
        "safe_args": getattr(tool_call, "safe_args", {}) if isinstance(getattr(tool_call, "safe_args", {}), dict) else {},
    }


def _event_trace(event: Any) -> dict[str, Any]:
    return {
        "type": str(getattr(event, "event_type", "") or ""),
        "sequence": int(getattr(event, "sequence", 0) or 0),
        "payload": getattr(event, "payload", {}) if isinstance(getattr(event, "payload", {}), dict) else {},
    }


def _action_trace(action: Any) -> dict[str, Any]:
    return {
        "action_type": str(getattr(action, "action_type", "") or ""),
        "status": str(getattr(action, "status", "") or ""),
        "target_type": str(getattr(action, "target_type", "") or ""),
    }


def _tool_contract_failures(*, case: dict[str, Any], trace: AgentEvalTrace) -> list[AgentEvalFailure]:
    expected_contracts = [_contract(tool_call) for tool_call in case.get("expected_tool_calls", []) if _contract(tool_call)]
    observed_contracts = {_observed_tool_contract(tool_call) for tool_call in trace.tool_calls}
    observed_contracts.discard("")
    return [
        AgentEvalFailure(
            category="missing_tool",
            assertion="tool.required",
            expected=contract,
            observed=", ".join(sorted(observed_contracts)) or "<none>",
        )
        for contract in expected_contracts
        if contract not in observed_contracts
    ]


def _tool_sequence_failures(*, case: dict[str, Any], trace: AgentEvalTrace) -> list[AgentEvalFailure]:
    expected_calls = [tool_call for tool_call in case.get("expected_tool_calls", []) if _contract(tool_call)]
    if len(expected_calls) < 2:
        return []
    observed_calls = [tool_call for tool_call in trace.tool_calls if _observed_tool_contract(tool_call)]
    cursor = 0
    for observed_call in observed_calls:
        if cursor < len(expected_calls) and _tool_call_matches_expected(
            expected_call=expected_calls[cursor],
            observed_call=observed_call,
        ):
            cursor += 1
    if cursor == len(expected_calls):
        return []
    return [
        AgentEvalFailure(
            category="tool_order_mismatch",
            assertion="tool.sequence",
            expected=" -> ".join(_tool_call_sequence_label(tool_call, expected=True) for tool_call in expected_calls),
            observed=" -> ".join(_tool_call_sequence_label(tool_call, expected=False) for tool_call in observed_calls) or "<none>",
        )
    ]


def _tool_call_matches_expected(*, expected_call: Any, observed_call: Any) -> bool:
    if _contract(expected_call) != _observed_tool_contract(observed_call):
        return False
    if not isinstance(expected_call, dict):
        return True
    expected_args = expected_call.get("args_subset")
    if not isinstance(expected_args, dict) or not expected_args:
        return True
    observed_args = _observed_tool_args(observed_call)
    return all(observed_args.get(key) == value for key, value in expected_args.items())


def _tool_call_sequence_label(tool_call: Any, *, expected: bool) -> str:
    contract = _contract(tool_call) if expected else _observed_tool_contract(tool_call)
    if not isinstance(tool_call, dict):
        return contract
    args = tool_call.get("args_subset") if expected else _observed_tool_args(tool_call)
    action = str(args.get("action") or "").strip() if isinstance(args, dict) else ""
    operation = str(args.get("operation") or "").strip() if isinstance(args, dict) else ""
    if action:
        return f"{contract}[action={action}]"
    if operation:
        return f"{contract}[operation={operation}]"
    return contract


def _observed_tool_args(tool_call: Any) -> dict[str, Any]:
    if not isinstance(tool_call, dict):
        return {}
    raw_args = tool_call.get("safe_args")
    if not isinstance(raw_args, dict) or not raw_args:
        raw_args = tool_call.get("args")
    return raw_args if isinstance(raw_args, dict) else {}


def _tool_argument_failures(*, case: dict[str, Any], trace: AgentEvalTrace) -> list[AgentEvalFailure]:
    failures: list[AgentEvalFailure] = []
    observed_by_contract: dict[str, list[dict[str, Any]]] = {}
    for tool_call in trace.tool_calls:
        contract = _observed_tool_contract(tool_call)
        if contract:
            observed_by_contract.setdefault(contract, []).append(tool_call)
    expected_occurrences: dict[str, int] = {}
    for expected_call in case.get("expected_tool_calls", []):
        contract = _contract(expected_call)
        if not contract or not isinstance(expected_call, dict):
            continue
        expected_args = expected_call.get("args_subset")
        if not isinstance(expected_args, dict) or not expected_args:
            continue
        occurrence = expected_occurrences.get(contract, 0)
        expected_occurrences[contract] = occurrence + 1
        observed_calls = observed_by_contract.get(contract, [])
        if occurrence >= len(observed_calls):
            continue
        observed_call = observed_calls[occurrence]
        observed_args = _observed_tool_args(observed_call)
        for key, expected_value in expected_args.items():
            if observed_args.get(key) == expected_value:
                continue
            failures.append(
                AgentEvalFailure(
                    category="tool_argument_mismatch",
                    assertion=f"tool.args.{key}",
                    expected=f"{contract}.{key}={expected_value!r}",
                    observed=repr(observed_args.get(key, "<missing>")),
                )
            )
    return failures


def _forbidden_tool_failures(*, case: dict[str, Any], trace: AgentEvalTrace) -> list[AgentEvalFailure]:
    forbidden_calls = [tool_call for tool_call in case.get("forbidden_tool_calls", []) if _contract(tool_call)]
    if not forbidden_calls:
        return []
    failures: list[AgentEvalFailure] = []
    for forbidden_call in forbidden_calls:
        matched_call = next(
            (
                observed_call
                for observed_call in trace.tool_calls
                if _tool_call_matches_expected(
                    expected_call=forbidden_call,
                    observed_call=observed_call,
                )
            ),
            None,
        )
        if matched_call is None:
            continue
        forbidden_args = forbidden_call.get("args_subset") if isinstance(forbidden_call, dict) else None
        observed_label = (
            _tool_call_sequence_label(matched_call, expected=False)
            if isinstance(forbidden_args, dict) and forbidden_args
            else _observed_tool_contract(matched_call)
        )
        failures.append(
            AgentEvalFailure(
                category="forbidden_tool",
                assertion="tool.forbidden",
                expected=f"do not call {_tool_call_sequence_label(forbidden_call, expected=True)}",
                observed=observed_label,
            )
        )
    return failures


def _required_event_failures(*, case: dict[str, Any], trace: AgentEvalTrace) -> list[AgentEvalFailure]:
    failures: list[AgentEvalFailure] = []
    for expected_event in case.get("expected_events", []):
        if not isinstance(expected_event, dict):
            continue
        if any(_event_matches_expected(event=event, expected=expected_event) for event in trace.events):
            continue
        failures.append(
            AgentEvalFailure(
                category="missing_event",
                assertion="event.required",
                expected=json.dumps(expected_event, sort_keys=True),
                observed=json.dumps(trace.events, sort_keys=True),
            )
        )
    return failures


def _event_matches_expected(*, event: dict[str, Any], expected: dict[str, Any]) -> bool:
    raw_payload = event.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    return all(event.get(key, payload.get(key)) == value for key, value in expected.items())


def _service_skill_routing_failures(
    *,
    case: dict[str, Any],
    trace: AgentEvalTrace,
    wrapper_agent_id: str,
    scene_service_skill_ids: frozenset[str],
) -> list[AgentEvalFailure]:
    raw_behavior = case.get("expected_behavior")
    behavior = raw_behavior if isinstance(raw_behavior, dict) else {}
    expected = str(behavior.get("service_skill_id") or "").strip()
    if not expected or not trace.service_skill_id or trace.service_skill_id == expected:
        return []
    if expected in scene_service_skill_ids and trace.service_skill_id == wrapper_agent_id:
        return []
    return [
        AgentEvalFailure(
            category="routing_mismatch",
            assertion="routing.service_skill",
            expected=expected,
            observed=trace.service_skill_id or "<none>",
        )
    ]


def _confirmation_failures(*, case: dict[str, Any], trace: AgentEvalTrace) -> list[AgentEvalFailure]:
    raw_behavior = case.get("expected_behavior")
    behavior = raw_behavior if isinstance(raw_behavior, dict) else {}
    if not bool(behavior.get("requires_confirmation_before_write")):
        return []
    proposal_contracts = [
        _contract(tool_call) for tool_call in case.get("expected_tool_calls", []) if _contract(tool_call).endswith("_propose")
    ]
    if not proposal_contracts:
        return []
    if _has_confirmation(trace):
        return []
    return [
        AgentEvalFailure(
            category="missing_confirmation",
            assertion="action.confirmation_required",
            expected="confirmation_required before write",
            observed=_observed_action_statuses(trace),
        )
    ]


def _forbidden_side_effect_failures(
    *,
    case: dict[str, Any],
    trace: AgentEvalTrace,
    write_tool_names: frozenset[str],
    non_write_tool_actions: Mapping[str, frozenset[str]],
) -> list[AgentEvalFailure]:
    raw_behavior = case.get("expected_behavior")
    behavior = raw_behavior if isinstance(raw_behavior, dict) else {}
    expected_contracts = [_contract(tool_call) for tool_call in case.get("expected_tool_calls", []) if _contract(tool_call)]
    if not bool(behavior.get("forbids_side_effects")) or expected_contracts:
        return []
    observed_write_tools = _observed_write_tool_contracts(
        trace,
        write_tool_names=write_tool_names,
        non_write_tool_actions=non_write_tool_actions,
    )
    if not observed_write_tools and not trace.actions:
        return []
    observed = ", ".join(observed_write_tools) or _observed_action_statuses(trace)
    return [
        AgentEvalFailure(
            category="forbidden_side_effect",
            assertion="side_effect.none",
            expected="no write proposal or action in safety-only flow",
            observed=observed,
        )
    ]


def _final_response_failures(*, case: dict[str, Any], trace: AgentEvalTrace) -> list[AgentEvalFailure]:
    raw_behavior = case.get("expected_behavior")
    behavior = raw_behavior if isinstance(raw_behavior, dict) else {}
    if not bool(behavior.get("requires_final_response_after_tools")) or trace.final_text.strip():
        return []
    return [
        AgentEvalFailure(
            category="missing_final_response",
            assertion="response.after_tools",
            expected="non-empty final response after tool execution",
            observed="<empty>",
        )
    ]


def _has_confirmation(trace: AgentEvalTrace) -> bool:
    if any(str(event.get("type") or event.get("event_type") or "") == "action.confirmation_required" for event in trace.events):
        return True
    return any(str(action.get("status") or "") == "confirmation_required" for action in trace.actions)


def _observed_action_statuses(trace: AgentEvalTrace) -> str:
    statuses = sorted({str(action.get("status") or "") for action in trace.actions if str(action.get("status") or "")})
    return ", ".join(statuses) or "<none>"


def _observed_write_tool_contracts(
    trace: AgentEvalTrace,
    *,
    write_tool_names: frozenset[str],
    non_write_tool_actions: Mapping[str, frozenset[str]],
) -> list[str]:
    return sorted(
        {
            contract
            for tool_call in trace.tool_calls
            if ((contract := _observed_tool_contract(tool_call)) in write_tool_names or contract.endswith("_propose"))
            and str(_observed_tool_args(tool_call).get("action") or "").strip()
            not in non_write_tool_actions.get(contract, frozenset())
        }
    )


def _contract(tool_call: Any) -> str:
    if not isinstance(tool_call, dict):
        return ""
    return str(tool_call.get("contract") or tool_call.get("tool_name") or "").strip()


def _observed_tool_contract(tool_call: Any) -> str:
    if not isinstance(tool_call, dict):
        return ""
    return str(tool_call.get("contract") or tool_call.get("tool_name") or tool_call.get("name") or "").strip()


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _last_assistant_text(bundle: dict[str, Any]) -> str:
    messages = bundle.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, dict):
            return str(content.get("text") or "").strip()
        if isinstance(content, str):
            return content.strip()
    return ""


def _run_service_skill_id(bundle: dict[str, Any]) -> str:
    run = bundle.get("run")
    if not isinstance(run, dict):
        return ""
    return str(run.get("service_skill_id") or "").strip()
