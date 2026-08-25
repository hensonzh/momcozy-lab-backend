from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ROOT = Path(__file__).resolve().parents[1]
CheckStatus = Literal["pass", "fail"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


def run_checks(root: Path = ROOT) -> list[CheckResult]:
    return [
        *_check_required_files(root),
        *_check_environment_profiles(root),
        *_check_ci_compose(root),
        *_check_ci_workflow(root),
        *_check_makefile(root),
        *_check_retired_runtime_absent(root),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=("Check Product Backend readiness guardrails that do not need live infrastructure."))
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
        "docs/api-contract-handoff.md",
        "docs/api-surface-catalog.md",
        "docs/openapi.generated.json",
        "docs/deployment-runbook.md",
        "docs/environment-profiles.md",
        "docs/release-smoke-checklist.md",
        "docker-compose.ci.yml",
        "docker-compose.local.yml",
        "docker-compose.staging.yml",
        "deploy/staging/init-postgres.sh",
        "scripts/check_database_profile.py",
        "scripts/check_redis_profile.py",
        "scripts/check_object_storage_profile.py",
        "scripts/check_product_asset_storage.py",
        "app/modules/auth/jwks_router.py",
        "app/modules/diary/agent_router.py",
        "app/modules/files/agent_router.py",
        "app/modules/plans/agent_router.py",
        "app/modules/profiles/agent_router.py",
        "app/modules/records/agent_router.py",
    ]
    return [_file_exists(root, relative_path) for relative_path in required]


def _check_environment_profiles(root: Path) -> list[CheckResult]:
    required_by_file = {
        "env/compose.local.env.example": [
            "APP_ENV=local",
            "DATABASE_URL=postgresql+asyncpg://momcozy:momcozy@postgres",
            "REDIS_URL=redis://redis",
            "OBJECT_STORAGE_PROVIDER=minio",
            "AUTH_JWT_PRIVATE_KEY_B64=${AUTH_JWT_PRIVATE_KEY_B64}",
            "AUTH_JWT_ISSUER=momcozy-local",
        ],
        "env/compose.staging.env.example": [
            "APP_ENV=staging",
            "DATABASE_URL=postgresql+asyncpg://momcozy_staging:${MOMCOZY_STAGING_PRODUCT_POSTGRES_PASSWORD}@staging-postgres",
            "REDIS_URL=redis://:${MOMCOZY_STAGING_REDIS_PASSWORD}@staging-redis:6379/0",
            "OBJECT_STORAGE_PROVIDER=minio",
            "AUTH_JWT_PRIVATE_KEY_B64=${AUTH_JWT_PRIVATE_KEY_B64}",
            "AUTH_JWT_ISSUER=momcozy-staging",
            "OBJECT_STORAGE_ENDPOINT_URL=http://staging-minio:9000",
        ],
    }
    common = [
        "AUTH_JWT_PRODUCT_AUDIENCE=momcozy-product-api",
        "AUTH_JWT_RUNTIME_AUDIENCE=momcozy-agent-runtime",
        "AGENT_RUNTIME_SERVICE_API_KEY=",
        "AGENT_MODEL_ASSET_PUBLIC_BASE_URL=",
        "AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS=",
        "OPENAI_API_KEY=",
        "VISION_OPENAI_MODEL=gpt-5.4-mini",
    ]
    retired_names = {
        "AGENT_RUNTIME_WORKER_ENABLED",
        "AGENT_RUNTIME_WORKER_BATCH_LIMIT",
        "AGENT_RUNTIME_WORKER_CONCURRENCY",
        "AGENT_RUNTIME_WORKER_IDLE_SECONDS",
        "AGENT_RUNTIME_INTERRUPT_RUNNING_OLDER_THAN_SECONDS",
        "AGENT_RUNTIME_MAX_INLINE_PAYLOAD_BYTES",
        "OPENAI_MODEL",
        "OPENAI_REASONING_EFFORT",
        "OPENAI_RESPONSES_STORE",
        "OPENAI_AGENT_MAX_TURNS",
        "OPENAI_AGENT_TIMEOUT_SECONDS",
        "AGENT_QUICK_REPLY_MODEL",
        "AGENT_QUICK_REPLY_TIMEOUT_SECONDS",
        "AGENT_FACT_EXTRACTION_ENABLED",
        "AGENT_FACT_EXTRACTION_MODEL",
        "AGENT_MEMORY_CONSOLIDATION_ENABLED",
        "AGENT_MEMORY_CONSOLIDATION_MODEL",
    }

    results: list[CheckResult] = []
    for relative_path, phrases in required_by_file.items():
        path = root / relative_path
        results.append(_file_exists(root, relative_path))
        if not path.exists():
            continue
        text = path.read_text()
        results.extend(_contains(path, text, phrase) for phrase in [*phrases, *common])
        names = _environment_names(text)
        for name in sorted(retired_names):
            results.append(_name_absent(path, names, name))
    return results


def _check_ci_compose(root: Path) -> list[CheckResult]:
    compose = root / "docker-compose.ci.yml"
    required = [
        "CI-only override",
        "name: momcozy-lab-backend-ci",
        "image: momcozy-lab-backend:ci",
        "AUTH_JWT_PRIVATE_KEY_B64: ${AUTH_JWT_PRIVATE_KEY_B64:?",
        "/v1/health/ready",
    ]
    return [
        *_required_phrases(compose, required),
        *_retired_phrases(compose, ["BEGIN PRIVATE KEY"]),
    ]


def _check_ci_workflow(root: Path) -> list[CheckResult]:
    workflow = root / ".github" / "workflows" / "backend-ci.yml"
    required = [
        "python -m ruff check app tests scripts",
        "python -m mypy app scripts",
        "python -m pytest tests",
        "python -m alembic -c alembic.ini upgrade head --sql",
        "scripts/check_redis_profile.py",
        "scripts/check_object_storage_profile.py",
        "docker-compose.local.yml",
        "docker-compose.ci.yml",
        "docker-compose.staging.yml",
        "--env-file env/compose.staging.env.example",
        "openssl genpkey",
        "momcozy-lab-backend:ci",
        "--profile tools run --rm migrate",
        "http://127.0.0.1:8000/v1/health/ready",
        "down --volumes",
    ]
    retired = [
        "test_agent_task8_observed_eval",
        "run_agent_fact_eval.py",
        "redis-runtime-controls",
        "check_redis_runtime_controls.py",
    ]
    return [*_required_phrases(workflow, required), *_retired_phrases(workflow, retired)]


def _check_makefile(root: Path) -> list[CheckResult]:
    makefile = root / "Makefile"
    required = [
        "backend-check-infra:",
        "backend-productization-status:",
        "backend-smoke:",
        "backend-staging-smoke:",
        "scripts/check_redis_profile.py",
        "scripts/check_product_asset_storage.py",
    ]
    retired = [
        "backend-workers",
        "backend-worker-backlog",
        "backend-agent-recover-stuck-runs",
        "run_agent_fact_eval.py",
        "check_redis_runtime_controls.py",
        "publish_pump_models_reference.py",
    ]
    return [*_required_phrases(makefile, required), *_retired_phrases(makefile, retired)]


def _check_retired_runtime_absent(root: Path) -> list[CheckResult]:
    retired = [
        "app/agent_runtime",
        "app/agents",
        "app/workers",
        "evals",
        "fixtures/agent_eval_cases",
        "assets/agent-references",
        "scripts/run_agent_worker.py",
        "scripts/run_memory_consolidation.py",
        "scripts/check_redis_runtime_controls.py",
        "scripts/inspect_worker_backlog.py",
        "scripts/recover_stuck_agent_runs.py",
        "scripts/run_agent_fact_eval.py",
        "scripts/run_agent_replay_eval.py",
        "scripts/run_agent_seed_eval.py",
        "docs/agent-context-construction-design.md",
        "docs/agent-fact-catalog.md",
        "docs/agent-tool-catalog.md",
        "docs/main-agent-design.md",
        "docs/redis-runtime-profile.md",
        "docs/product/momcozy-agent-service-test-plan.md",
    ]
    return [_path_absent(root, relative_path) for relative_path in retired]


def _required_phrases(path: Path, phrases: list[str]) -> list[CheckResult]:
    if not path.exists():
        return [CheckResult(name=str(path), status="fail", detail="missing file")]
    text = path.read_text()
    return [_contains(path, text, phrase) for phrase in phrases]


def _retired_phrases(path: Path, phrases: list[str]) -> list[CheckResult]:
    if not path.exists():
        return [CheckResult(name=str(path), status="fail", detail="missing file")]
    text = path.read_text()
    return [_absent(path, text, phrase) for phrase in phrases]


def _environment_names(text: str) -> set[str]:
    return {
        line.partition("=")[0].strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }


def _file_exists(root: Path, relative_path: str) -> CheckResult:
    path = root / relative_path
    if path.exists():
        return CheckResult(name=relative_path, status="pass", detail="exists")
    return CheckResult(name=relative_path, status="fail", detail="missing")


def _path_absent(root: Path, relative_path: str) -> CheckResult:
    path = root / relative_path
    if not path.exists():
        return CheckResult(name=f"retired:{relative_path}", status="pass", detail="absent")
    if path.is_dir():
        remaining = [
            candidate
            for candidate in path.rglob("*")
            if candidate.is_file()
            and "__pycache__" not in candidate.parts
            and candidate.suffix not in {".pyc", ".pyo"}
            and candidate.name != ".DS_Store"
        ]
        if not remaining:
            return CheckResult(name=f"retired:{relative_path}", status="pass", detail="no source files")
    return CheckResult(name=f"retired:{relative_path}", status="fail", detail="still present")


def _contains(path: Path, text: str, phrase: str) -> CheckResult:
    if phrase in text:
        return CheckResult(name=f"{path.name}:{phrase}", status="pass", detail="present")
    return CheckResult(name=f"{path.name}:{phrase}", status="fail", detail="missing phrase")


def _absent(path: Path, text: str, phrase: str) -> CheckResult:
    if phrase not in text:
        return CheckResult(name=f"{path.name}:without:{phrase}", status="pass", detail="absent")
    return CheckResult(name=f"{path.name}:without:{phrase}", status="fail", detail="present")


def _name_absent(path: Path, names: set[str], name: str) -> CheckResult:
    if name not in names:
        return CheckResult(name=f"{path.name}:without:{name}", status="pass", detail="absent")
    return CheckResult(name=f"{path.name}:without:{name}", status="fail", detail="present")


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
