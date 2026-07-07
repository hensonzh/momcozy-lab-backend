from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from production_backend.app.core.errors import ApiError  # noqa: E402
from production_backend.app.core.settings import SUPPORTED_AGENT_MODEL_PROVIDERS, Settings  # noqa: E402
from production_backend.app.modules.agent_runtime.evals.service import (  # noqa: E402
    AgentEvalFailure,
    AgentEvalRunResult,
    AgentEvalSeedAssertionEngine,
    AgentEvalTrace,
    load_product_agent_eval_seed_cases,
)
from production_backend.app.modules.agent_runtime.prompts import ContextProjection, ModelInputBuilder  # noqa: E402
from production_backend.app.modules.agent_runtime.run_lifecycle.executor import AgentRuntimeExecutorConfig  # noqa: E402
from production_backend.app.modules.agent_runtime.routing import RoutingContext, SpecialistRoutingService  # noqa: E402
from production_backend.app.modules.agent_runtime.safety.service import DeterministicSafetyGuard  # noqa: E402
from production_backend.app.modules.agent_runtime.sdk import (  # noqa: E402
    OpenAIAgentsSdkRunner,
    SdkNodeRequest,
    SdkToolDefinition,
    create_agent_sdk_runner,
    default_specialist_registry,
    sdk_tool_name,
)
from production_backend.app.modules.agent_runtime.tools import default_tool_registry  # noqa: E402
from production_backend.app.modules.agent_runtime.tools.schemas import tool_input_schema  # noqa: E402


DEFAULT_CASES = Path("production_backend/fixtures/agent_eval_cases/product_service_seed.json")


def run_provider_eval(
    *,
    cases_path: Path,
    suite: str | None = None,
    name: str | None = None,
    output_path: Path | None = None,
    max_cases: int | None = None,
    cost_budget_usd: str = "",
    allow_skip_without_credentials: bool = False,
    sdk_runner: OpenAIAgentsSdkRunner | None = None,
) -> dict[str, Any]:
    cases = _select_cases(load_product_agent_eval_seed_cases(cases_path), suite=suite, name=name, max_cases=max_cases)
    settings = Settings.from_env()
    provider = sdk_runner.provider if sdk_runner is not None else settings.agent_model_provider
    model = sdk_runner.model if sdk_runner is not None else _provider_model(settings)
    if provider not in SUPPORTED_AGENT_MODEL_PROVIDERS:
        raise SystemExit(f"Unsupported AGENT_MODEL_PROVIDER: {provider}.")
    if sdk_runner is None and not _has_provider_credentials(settings):
        if not allow_skip_without_credentials:
            raise SystemExit(f"Credentials are required for provider-backed eval: {provider}.")
        report = _report(
            [_skipped_case_result(case, reason="missing_provider_credentials") for case in cases],
            max_cases=max_cases,
            cost_budget_usd=cost_budget_usd,
            provider=provider,
            model=model,
        )
        _write_report(report=report, output_path=output_path)
        return report

    runner = sdk_runner or create_agent_sdk_runner(settings=settings, trace_enabled=False)
    report = _report(
        asyncio.run(_run_cases(cases=cases, sdk_runner=runner)),
        max_cases=max_cases,
        cost_budget_usd=cost_budget_usd,
        provider=provider,
        model=model,
    )
    _write_report(report=report, output_path=output_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run provider-backed agent evals against product seed cases.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--suite", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--cost-budget-usd", default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--allow-skip-without-credentials", action="store_true")
    args = parser.parse_args()

    report = run_provider_eval(
        cases_path=args.cases,
        suite=args.suite,
        name=args.name,
        output_path=args.output,
        max_cases=args.max_cases if args.max_cases is not None else _env_int("AGENT_PROVIDER_EVAL_MAX_CASES"),
        cost_budget_usd=args.cost_budget_usd or os.getenv("AGENT_PROVIDER_EVAL_COST_BUDGET_USD", ""),
        allow_skip_without_credentials=args.allow_skip_without_credentials,
    )
    if args.output is None:
        print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["failed"] == 0 else 1)


async def _run_cases(*, cases: list[dict[str, Any]], sdk_runner: OpenAIAgentsSdkRunner) -> list[dict[str, Any]]:
    return [await _run_case(case=case, sdk_runner=sdk_runner) for case in cases]


async def _run_case(*, case: dict[str, Any], sdk_runner: OpenAIAgentsSdkRunner) -> dict[str, Any]:
    tool_registry = default_tool_registry()
    available_contracts = {contract.name for contract in tool_registry.list()}
    expected_contracts = [_contract(tool_call) for tool_call in case.get("expected_tool_calls", []) if _contract(tool_call)]
    unsupported_contracts = sorted(contract for contract in expected_contracts if contract not in available_contracts)
    if unsupported_contracts:
        return _skipped_case_result(case, reason=f"unsupported_tool_contracts:{','.join(unsupported_contracts)}")

    user_message = _current_user_message(case)
    safety_decision = DeterministicSafetyGuard().evaluate(user_message).decision
    if safety_decision != "allow":
        if expected_contracts:
            return _skipped_case_result(case, reason="safety_and_tool_handoff_requires_full_runtime")
        return _result_payload(
            AgentEvalSeedAssertionEngine().evaluate(
                case=case,
                trace=AgentEvalTrace(safety_decision=safety_decision),
            )
        )

    routing_plan = await SpecialistRoutingService().route(
        RoutingContext(
            run_id=_eval_uuid(case, "run"),
            thread_id=_eval_uuid(case, "thread"),
            actor_user_id=_eval_uuid(case, "actor"),
            message=user_message,
        )
    )
    specialist = default_specialist_registry().get(routing_plan.primary_specialist_id.value)
    tool_names = tuple(sorted(contract.name for contract in tool_registry.list() if specialist.allows_tool(contract)))
    unavailable_for_specialist = sorted(contract for contract in expected_contracts if contract not in tool_names)
    if unavailable_for_specialist:
        return _result_payload(
            AgentEvalRunResult(
                suite=str(case.get("suite") or ""),
                name=str(case.get("name") or ""),
                passed=False,
                failures=[
                    AgentEvalFailure(
                        category="tool_not_available",
                        assertion="specialist.tool_allowlist",
                        expected=", ".join(unavailable_for_specialist),
                        observed=specialist.id,
                    )
                ],
            )
        )

    observed_tool_calls: list[dict[str, Any]] = []
    try:
        result = await sdk_runner.run_reasoning(
            SdkNodeRequest(
                run_id=_eval_id(case, "run"),
                thread_id=_eval_id(case, "thread"),
                actor_user_id="provider-eval-user",
                instructions=_instructions(specialist_id=specialist.id, specialist_instructions=specialist.instructions),
                model_input=_model_input(case=case, specialist_id=specialist.id),
                tool_names=tool_names,
                tools=tuple(
                    _tool_definition(tool_name=tool_name, observed_tool_calls=observed_tool_calls, fixtures=_fixtures(case))
                    for tool_name in tool_names
                ),
                prompt_version="provider-eval",
                trace_id=_eval_id(case, "trace"),
                specialist_id=specialist.id,
            )
        )
    except ApiError as exc:
        return _result_payload(
            AgentEvalRunResult(
                suite=str(case.get("suite") or ""),
                name=str(case.get("name") or ""),
                passed=False,
                failures=[
                    AgentEvalFailure(
                        category="provider_error",
                        assertion="provider.run",
                        expected="provider-backed run completes",
                        observed=exc.code,
                    )
                ],
            )
        )

    trace = AgentEvalTrace(
        tool_calls=observed_tool_calls,
        events=_synthetic_action_events(observed_tool_calls),
        actions=_synthetic_actions(observed_tool_calls),
        safety_decision=safety_decision,
        final_text=result.final_text,
        specialist_id=specialist.id,
    )
    payload = _result_payload(AgentEvalSeedAssertionEngine().evaluate(case=case, trace=trace))
    payload["specialist_id"] = specialist.id
    payload["routing_source"] = routing_plan.source.value
    payload["observed_tool_calls"] = observed_tool_calls
    payload["final_text"] = result.final_text
    return payload


def _tool_definition(*, tool_name: str, observed_tool_calls: list[dict[str, Any]], fixtures: dict[str, Any]) -> SdkToolDefinition:
    async def invoke_json(args_json: str) -> str:
        observed_tool_calls.append({"tool_name": tool_name, "status": "completed", "args": _json_object(args_json)})
        return json.dumps(
            {
                "status": "ok",
                "tool_name": tool_name,
                "fixture_summary": fixtures,
            },
            sort_keys=True,
        )

    registry = default_tool_registry()
    contract = registry.get(tool_name)
    return SdkToolDefinition(
        contract_name=contract.name,
        sdk_name=sdk_tool_name(contract.name),
        description=contract.description,
        params_json_schema=tool_input_schema(contract.input_schema_ref),
        invoke_json=invoke_json,
    )


def _model_input(*, case: dict[str, Any], specialist_id: str) -> list[dict[str, Any]]:
    messages = _messages(case)
    current_message = messages[-1] if messages else {"role": "user", "content": ""}
    history = messages[:-1]
    config = AgentRuntimeExecutorConfig()
    model_input: list[dict[str, Any]] = ModelInputBuilder().build(
        projection=ContextProjection(
            stable_system_prompt=config.stable_system_prompt,
            stable_developer_prompt=config.stable_developer_prompt,
            selected_conversation_history=history,
            current_state_projection={"eval_suite": str(case.get("suite") or ""), "specialist_id": specialist_id},
            memory_projection=[],
            fresh_business_facts=_fixtures(case),
        ),
        current_user_message=current_message,
    )
    return model_input


def _instructions(*, specialist_id: str, specialist_instructions: str) -> str:
    config = AgentRuntimeExecutorConfig()
    return "\n\n".join(
        [
            config.stable_system_prompt,
            config.stable_developer_prompt,
            f"Specialist profile: {specialist_id}.",
            specialist_instructions,
        ]
    )


def _synthetic_action_events(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": "action.confirmation_required"} for tool_call in tool_calls if str(tool_call.get("tool_name") or "").endswith(".propose")
    ]


def _synthetic_actions(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"status": "confirmation_required"} for tool_call in tool_calls if str(tool_call.get("tool_name") or "").endswith(".propose")]


def _select_cases(cases: list[dict[str, Any]], *, suite: str | None, name: str | None, max_cases: int | None) -> list[dict[str, Any]]:
    selected = [case for case in cases if (suite is None or case.get("suite") == suite) and (name is None or case.get("name") == name)]
    if max_cases is not None:
        selected = selected[: max(0, max_cases)]
    if not selected:
        raise SystemExit("No provider eval cases selected.")
    return selected


def _skipped_case_result(case: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "suite": str(case.get("suite") or ""),
        "name": str(case.get("name") or ""),
        "passed": True,
        "status": "skipped",
        "skip_reason": reason,
        "failures": [],
    }


def _result_payload(result: AgentEvalRunResult) -> dict[str, Any]:
    return {
        "suite": result.suite,
        "name": result.name,
        "passed": result.passed,
        "status": "passed" if result.passed else "failed",
        "failures": [asdict(failure) for failure in result.failures],
    }


def _report(
    results: list[dict[str, Any]],
    *,
    max_cases: int | None,
    cost_budget_usd: str,
    provider: str,
    model: str,
) -> dict[str, Any]:
    return {
        "total": len(results),
        "passed": sum(1 for result in results if result["status"] == "passed"),
        "failed": sum(1 for result in results if result["status"] == "failed"),
        "skipped": sum(1 for result in results if result["status"] == "skipped"),
        "provider": provider,
        "model": model,
        "budget": {
            "max_cases": max_cases,
            "cost_budget_usd": cost_budget_usd,
        },
        "results": results,
    }


def _write_report(*, report: dict[str, Any], output_path: Path | None) -> None:
    if output_path is None:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def _current_user_message(case: dict[str, Any]) -> str:
    messages = _messages(case)
    return str(messages[-1].get("content") or "") if messages else ""


def _messages(case: dict[str, Any]) -> list[dict[str, Any]]:
    input_payload = case.get("input") if isinstance(case.get("input"), dict) else {}
    messages = input_payload.get("messages") if isinstance(input_payload, dict) else []
    if not isinstance(messages, list):
        return []
    return [
        {"role": str(message.get("role") or "user"), "content": str(message.get("content") or "")}
        for message in messages
        if isinstance(message, dict)
    ]


def _fixtures(case: dict[str, Any]) -> dict[str, Any]:
    input_payload = case.get("input") if isinstance(case.get("input"), dict) else {}
    fixtures = input_payload.get("fixtures") if isinstance(input_payload, dict) else {}
    return fixtures if isinstance(fixtures, dict) else {}


def _contract(tool_call: Any) -> str:
    if not isinstance(tool_call, dict):
        return ""
    return str(tool_call.get("contract") or tool_call.get("tool_name") or "").strip()


def _json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _eval_id(case: dict[str, Any], prefix: str) -> str:
    suite = str(case.get("suite") or "suite").replace(" ", "_")
    name = str(case.get("name") or "case").replace(" ", "_")
    return f"{prefix}-{suite}-{name}"[:120]


def _eval_uuid(case: dict[str, Any], prefix: str) -> UUID:
    return uuid5(NAMESPACE_URL, _eval_id(case, prefix))


def _has_provider_credentials(settings: Settings) -> bool:
    if settings.agent_model_provider == "minimax":
        return bool(settings.minimax_api_key)
    return bool(settings.openai_api_key or os.getenv("OPENAI_ADMIN_KEY"))


def _provider_model(settings: Settings) -> str:
    if settings.agent_model_provider == "minimax":
        return str(settings.minimax_model)
    return str(settings.openai_model)


def _env_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


if __name__ == "__main__":
    main()
