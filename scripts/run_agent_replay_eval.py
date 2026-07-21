from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.cozymate.evals import (  # noqa: E402
    create_cozymate_eval_assertion_engine,
    load_product_agent_eval_seed_cases,
)
from app.agent_runtime.evals.service import (  # noqa: E402
    AgentEvalReplayAssertionRunner,
)

DEFAULT_CASES = Path("fixtures/agent_eval_cases/product_service_seed.json")


def run_replay_eval(
    *,
    cases_path: Path,
    replay_path: Path,
    suite: str,
    name: str | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    cases = load_product_agent_eval_seed_cases(cases_path)
    case = _select_case(cases=cases, suite=suite, name=name)
    replay_bundle = json.loads(replay_path.read_text())
    result = AgentEvalReplayAssertionRunner(assertion_engine=create_cozymate_eval_assertion_engine()).evaluate_bundle(case=case, replay_bundle=replay_bundle)
    report = {
        "suite": result.suite,
        "name": result.name,
        "passed": result.passed,
        "failures": [asdict(failure) for failure in result.failures],
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate an agent replay bundle against a product seed eval case.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    report = run_replay_eval(
        cases_path=args.cases,
        replay_path=args.replay,
        suite=args.suite,
        name=args.name,
        output_path=args.output,
    )
    if args.output is None:
        print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


def _select_case(*, cases: list[dict[str, Any]], suite: str, name: str | None) -> dict[str, Any]:
    matches = [case for case in cases if case.get("suite") == suite and (name is None or case.get("name") == name)]
    if not matches:
        raise SystemExit(f"No eval case found for suite={suite!r} name={name!r}.")
    if len(matches) > 1:
        raise SystemExit(f"Multiple eval cases found for suite={suite!r}; pass --name to disambiguate.")
    return matches[0]


if __name__ == "__main__":
    main()
