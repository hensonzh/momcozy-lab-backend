from pathlib import Path


DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_deployment_runbook_covers_release_recovery_and_security() -> None:
    text = (DOCS / "deployment-runbook.md").read_text()

    for phrase in [
        "DATABASE_URL",
        "alembic",
        "/v1/health/ready",
        "Worker Backlog",
        "Agent Run Recovery",
        "Security Incident",
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
        "OpenAPI snapshot",
    ]:
        assert phrase in text
