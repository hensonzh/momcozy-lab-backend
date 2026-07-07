from pathlib import Path


DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_deployment_runbook_covers_release_recovery_and_security() -> None:
    text = (DOCS / "deployment-runbook.md").read_text()

    for phrase in [
        "DATABASE_URL",
        "alembic",
        "/v1/health/ready",
        "check_backup_restore_hooks.py",
        "Worker Backlog",
        "Agent Run Recovery",
        "Security Incident",
        "Backup And Restore Drill",
        "Credential Rotation Drill",
        "Provider Eval Budget",
        "make backend-productization-status",
        "make backend-test-smoke",
        "make backend-worker-backlog",
        "make backend-agent-recover-stuck-runs",
        "recover_stuck_agent_runs.py --apply",
        "AGENT_PROVIDER_EVAL_MAX_CASES",
        "AGENT_PROVIDER_EVAL_COST_BUDGET_USD",
        "actor_service",
    ]:
        assert phrase in text


def test_release_smoke_checklist_covers_auth_core_agent_and_observability() -> None:
    text = (DOCS / "release-smoke-checklist.md").read_text()

    for phrase in [
        "/v1/auth/signup",
        "/v1/files/upload",
        "Idempotency-Key",
        "/v1/agent/runs/{run_id}/stream",
        "/v1/health/metrics",
        "make backend-productization-status",
        "make backend-smoke",
        "run_agent_seed_eval.py",
        "run_agent_provider_eval.py",
        "make backend-worker-backlog",
        "OpenAPI snapshot",
    ]:
        assert phrase in text
