"""B CI is read-only and independent of A delivery."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/backend-b-validation.yml"


def test_b_validation_only_targets_dev_and_never_delivers() -> None:
    text = WORKFLOW.read_text()
    assert "branches: [dev]" in text
    assert "workflow_dispatch:" in text
    assert "docker-compose.us-east-uat.yml" in text
    assert "env/us-east-uat.env.example" in text
    assert "python -m pytest" in text
    assert "python -m ruff check" in text
    assert "deploy/Dockerfile" in text
    assert "docker build" in text
    assert "scripts/check_b_infra_contract.sh" in text
    for forbidden in (
        "docker push", "docker compose up", "scripts/release.py",
        "backend-delivery.yml", "ssh ", "kubectl", "secrets.", "permissions: write-all",
    ):
        assert forbidden not in text


def test_b_infra_smoke_checks_service_boundaries() -> None:
    text = (ROOT / "scripts/check_b_infra_contract.sh").read_text()
    assert "deploy/us-east-uat/init-postgres.sh" in text
    assert "deploy/us-east-uat/start-redis.sh" in text
    assert "pg_has_role" not in text  # must test actual connections
    assert "NOPERM" in text
    assert "docker run" in text
    assert "trap cleanup EXIT" in text
