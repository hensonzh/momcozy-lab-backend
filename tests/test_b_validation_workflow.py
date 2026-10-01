"""B validation gates image publication without deploying to A or B."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/backend-b-validation.yml"


def test_b_validation_only_targets_dev_and_never_deploys() -> None:
    text = WORKFLOW.read_text()
    assert "branches: [dev]" in text
    assert "workflow_dispatch:" in text
    assert "docker-compose.us-east-uat.yml" in text
    assert "env/us-east-uat.env.example" in text
    assert "python -m pytest" in text
    assert "python -m ruff check" in text
    assert "tests/test_north_america_target.py" in text
    assert "tests/test_staging_trust_bundle.py" in text
    assert "tests/test_b_ingress_contract.py" in text
    assert "tests/test_b_fresh_bootstrap.py" in text
    assert "deploy/Dockerfile" in text
    assert "docker build" in text
    assert "scripts/check_b_infra_contract.sh" in text
    assert "tests/test_b_redis_recovery.py" in text
    assert "RUN_B_REDIS_RECOVERY=1 python -m pytest -q tests/test_b_redis_recovery_docker.py" in text
    assert "scripts/check_b_minio_recovery_contract.sh" in text
    assert "deploy/shared/Minio.Dockerfile" in text
    contract = text.split("\n  b-image:", 1)[0]
    for forbidden in (
        "docker push", "docker compose up", "scripts/release.py",
        "backend-delivery.yml", "ssh ", "kubectl", "secrets.", "permissions: write-all",
    ):
        assert forbidden not in contract


def test_b_infra_smoke_checks_service_boundaries() -> None:
    text = (ROOT / "scripts/check_b_infra_contract.sh").read_text()
    assert "deploy/us-east-uat/init-postgres.sh" in text
    assert "deploy/us-east-uat/start-redis.sh" in text
    assert "pg_has_role" not in text  # must test actual connections
    assert "NOPERM" in text
    assert "docker run" in text
    assert "trap cleanup EXIT" in text


def test_b_image_publication_is_dev_only_after_validation() -> None:
    text = WORKFLOW.read_text()
    assert text.count("\n  b-image:\n") == 1
    validation, publish = text.split("\n  b-image:\n", 1)
    assert "permissions:\n  contents: read" in validation
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/dev'" in publish
    assert "needs: b-contract" in publish
    assert "packages: write" in publish
    assert "secrets.GITHUB_TOKEN" in publish
    assert "deploy/Dockerfile" in publish
    assert "push: true" in publish
    assert "b-dev-${{ github.sha }}" in publish
    assert "org.opencontainers.image.revision=${{ github.sha }}" in publish
    assert "org.momcozy.release-target=north-america-staging" in publish
    assert "steps.build.outputs.digest" in publish
    assert "GITHUB_STEP_SUMMARY" in publish
    assert "docker buildx imagetools inspect" in publish
    for forbidden in ("docker compose up", "scripts/release.py", "ssh ", "kubectl"):
        assert forbidden not in publish
