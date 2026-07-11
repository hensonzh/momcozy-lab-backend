from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "production_backend"

CheckStatus = Literal["pass", "fail"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


def run_checks(root: Path = ROOT) -> list[CheckResult]:
    backend = root / "production_backend"
    return [
        *_check_required_files(root),
        *_check_environment_profiles(backend),
        *_check_ci_workflows(root),
        *_check_makefile_targets(root),
        *_check_legacy_runtime_guard(root),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check backend productization readiness guardrails that do not need live infrastructure.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    args = parser.parse_args()

    results = run_checks()
    failed = [result for result in results if result.status == "fail"]

    if args.json:
        print(json.dumps(_summary(results), indent=2, sort_keys=True))
    else:
        for result in results:
            print(f"[{result.status}] {result.name}: {result.detail}")
        print(f"summary: {len(results) - len(failed)} passed, {len(failed)} failed")

    if failed:
        raise SystemExit(1)


def _check_required_files(root: Path) -> list[CheckResult]:
    required = [
        "production_backend/docs/productization-roadmap.md",
        "production_backend/docs/api-contract-handoff.md",
        "production_backend/docs/api-surface-catalog.md",
        "production_backend/docs/openapi.generated.json",
        "production_backend/docs/deployment-runbook.md",
        "production_backend/docs/release-smoke-checklist.md",
        "production_backend/scripts/run_agent_worker.py",
        "production_backend/scripts/run_outbox_worker.py",
        "production_backend/scripts/run_memory_consolidation.py",
        "production_backend/scripts/inspect_worker_backlog.py",
        "production_backend/scripts/recover_stuck_agent_runs.py",
        "production_backend/scripts/run_agent_seed_eval.py",
        "production_backend/scripts/check_product_asset_storage.py",
    ]
    return [_file_exists(root, relative_path) for relative_path in required]


def _check_environment_profiles(backend: Path) -> list[CheckResult]:
    required_by_file = {
        "env/compose.local.env.example": [
            "APP_ENV=local",
            "DATABASE_URL=postgresql+asyncpg://momcozy:momcozy@postgres",
            "REDIS_URL=redis://redis",
            "OBJECT_STORAGE_PROVIDER=minio",
            "OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000",
            "AGENT_MODEL_PROVIDER=openai",
            "AGENT_MEMORY_CONSOLIDATION_MODEL=gpt-5.4-nano",
        ],
        "env/compose.test.env.example": [
            "APP_ENV=test",
            "DATABASE_URL=postgresql+asyncpg://momcozy_test:momcozy_test@postgres",
            "REDIS_URL=redis://redis",
            "OBJECT_STORAGE_PROVIDER=minio",
            "OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000",
            "AGENT_MODEL_PROVIDER=openai",
            "AGENT_MEMORY_CONSOLIDATION_MODEL=gpt-5.4-nano",
        ],
        "env/compose.prod.env.example": [
            "APP_ENV=production",
            "OBJECT_STORAGE_PROVIDER=oss",
            "AUTH_REQUIRE_ACTIVE_SESSION=true",
            "METRICS_REQUIRE_SERVICE_KEY=true",
            "OUTBOX_WORKER_ENABLED=true",
            "AGENT_MODEL_PROVIDER=openai",
            "AGENT_MEMORY_CONSOLIDATION_MODEL=gpt-5.4-nano",
        ],
    }
    results: list[CheckResult] = []
    for relative_path, phrases in required_by_file.items():
        path = backend / relative_path
        results.append(_file_exists(backend, relative_path))
        if path.exists():
            text = path.read_text()
            for phrase in phrases:
                results.append(_contains(path, text, phrase))
    return results


def _check_ci_workflows(root: Path) -> list[CheckResult]:
    workflow = root / ".github" / "workflows" / "production-backend-ci.yml"
    checks = [
        (workflow, "python -m ruff check app tests scripts"),
        (workflow, "python -m mypy app scripts"),
        (workflow, "python -m pytest production_backend/tests"),
        (workflow, "production_backend/scripts/run_agent_seed_eval.py"),
        (workflow, "python -m alembic -c production_backend/alembic.ini upgrade head --sql"),
        (workflow, "production_backend/scripts/check_redis_runtime_controls.py"),
        (workflow, "production_backend/scripts/check_object_storage_profile.py"),
    ]
    return _contains_by_file(checks)


def _check_makefile_targets(root: Path) -> list[CheckResult]:
    makefile = root / "Makefile"
    checks = [
        (makefile, "backend-check-infra:"),
        (makefile, "backend-productization-status:"),
        (makefile, "backend-smoke:"),
        (makefile, "backend-test-smoke:"),
        (makefile, "backend-prod-readiness:"),
        (makefile, "backend-worker-backlog:"),
        (makefile, "backend-agent-recover-stuck-runs:"),
        (makefile, "production_backend/scripts/check_productization_status.py"),
        (makefile, "production_backend/scripts/check_product_asset_storage.py"),
    ]
    return _contains_by_file(checks)


def _check_legacy_runtime_guard(root: Path) -> list[CheckResult]:
    workflow = root / ".github" / "workflows" / "production-backend-ci.yml"
    checks = [
        (workflow, "previous_response_id|ChatSession|ENTRY_API_KEY"),
        (workflow, "production_backend/app production_backend/migrations production_backend/tests"),
    ]
    return _contains_by_file(checks)


def _contains_by_file(checks: list[tuple[Path, str]]) -> list[CheckResult]:
    results: list[CheckResult] = []
    cache: dict[Path, str] = {}
    for path, phrase in checks:
        if not path.exists():
            results.append(CheckResult(name=str(path), status="fail", detail="missing file"))
            continue
        text = cache.setdefault(path, path.read_text())
        results.append(_contains(path, text, phrase))
    return results


def _file_exists(root: Path, relative_path: str) -> CheckResult:
    path = root / relative_path
    if path.exists():
        return CheckResult(name=relative_path, status="pass", detail="exists")
    return CheckResult(name=relative_path, status="fail", detail="missing")


def _contains(path: Path, text: str, phrase: str) -> CheckResult:
    if phrase in text:
        return CheckResult(name=f"{path.name}:{phrase}", status="pass", detail="present")
    return CheckResult(name=f"{path.name}:{phrase}", status="fail", detail="missing phrase")


def _summary(results: list[CheckResult]) -> dict[str, object]:
    failed = [result for result in results if result.status == "fail"]
    return {
        "status": "fail" if failed else "pass",
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [
            {
                "name": result.name,
                "status": result.status,
                "detail": result.detail,
            }
            for result in results
        ],
    }


if __name__ == "__main__":
    main()
