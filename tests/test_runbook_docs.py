from pathlib import Path


DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_deployment_runbook_covers_release_recovery_and_security() -> None:
    text = (DOCS / "deployment-runbook.md").read_text()
    for phrase in [
        "staging",
        "production",
        "backend-delivery.yml",
        "scripts/release.py",
        "RELEASE_LOCK_PATH",
        "immutable",
        "mode-`0600`",
        "/v1/health/ready",
        "/.well-known/jwks.json",
        "Rollback",
        "never downgrades",
        "Backend first",
    ]:
        assert phrase in text
    for retired in [
        "backend-test-delivery.yml",
        "scripts/test_release.py",
        "docker-compose.test.yml",
        "MOMCOZY_TEST_",
    ]:
        assert retired not in text


def test_release_smoke_checklist_covers_product_and_runtime_boundary() -> None:
    text = (DOCS / "release-smoke-checklist.md").read_text()
    for phrase in [
        "/v1/auth/signup",
        "/v1/auth/invite-login",
        "/v1/admin/invite-codes",
        "/v1/files/upload",
        "Idempotency-Key",
        "/.well-known/jwks.json",
        "/v1/internal/agent/",
        "AGENT_RUNTIME_SERVICE_API_KEY",
        "/v1/health/metrics",
        "make backend-productization-status",
        "make backend-smoke",
        "OpenAPI snapshot",
    ]:
        assert phrase in text
