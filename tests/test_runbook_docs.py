from pathlib import Path


DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_deployment_runbook_covers_release_recovery_and_security() -> None:
    text = (DOCS / "deployment-runbook.md").read_text()

    for phrase in [
        "DATABASE_URL",
        "alembic",
        "/v1/health/ready",
        "check_backup_restore_hooks.py",
        "Security Incident",
        "Backup And Restore Drill",
        "Credential Rotation Drill",
        "/.well-known/jwks.json",
        "/v1/internal/agent/",
        "AGENT_RUNTIME_SERVICE_API_KEY",
        "Idempotency-Key",
        "actor_user_id",
        "make backend-productization-status",
        "make backend-staging-smoke",
        "momcozy-lab-staging",
        "agent_runtime_staging",
        "agent-runtime-staging",
        "empty database",
        "upgrading an older Product Backend schema",
    ]:
        assert phrase in text

    for retired in [
        "make backend-worker-backlog",
        "make backend-production-readiness",
        "docker-compose.production.yml",
        "make backend-agent-recover-stuck-runs",
        "recover_stuck_agent_runs.py",
        "AGENT_PROVIDER_EVAL_MAX_CASES",
        "AGENT_PROVIDER_EVAL_COST_BUDGET_USD",
    ]:
        assert retired not in text


def test_release_smoke_checklist_covers_product_and_runtime_boundary() -> None:
    text = (DOCS / "release-smoke-checklist.md").read_text()

    for phrase in [
        "/v1/auth/signup",
        "/v1/auth/invite-login",
        "/v1/admin/invite-codes",
        "/v1/admin/invite-codes/{code}/disable",
        "/v1/files/upload",
        "Idempotency-Key",
        "/.well-known/jwks.json",
        "/v1/internal/agent/",
        "AGENT_RUNTIME_SERVICE_API_KEY",
        "/v1/health/metrics",
        "make backend-productization-status",
        "make backend-smoke",
        "make backend-staging-smoke",
        "OpenAPI snapshot",
    ]:
        assert phrase in text

    for retired in [
        "/v1/agent/runs/",
        "test_agent_task8_observed_eval.py",
        "make backend-worker-backlog",
        "run_agent_fact_eval.py",
        "make backend-production-readiness",
    ]:
        assert retired not in text
