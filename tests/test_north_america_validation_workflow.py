"""North America validation gates image publication without deploying to A or North America."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/backend-north-america-validation.yml"


def test_north_america_validation_only_targets_main_and_never_deploys() -> None:
    text = WORKFLOW.read_text()
    assert "branches: [main]" in text
    assert "pull_request:" not in text
    assert "workflow_dispatch:" not in text
    assert "docker-compose.us-east-uat.yml" in text
    assert "env/us-east-uat.env.example" in text
    assert "python -m pytest" in text
    assert "python -m ruff check" in text
    assert "tests/test_north_america_target.py" in text
    assert "tests/test_staging_trust_bundle.py" in text
    assert "tests/test_north_america_ingress_contract.py" in text
    assert "tests/test_north_america_fresh_bootstrap.py" in text
    assert "tests/test_north_america_update_release.py" in text
    assert "tests/test_north_america_private_credentials.py" in text
    assert "tests/test_auth_api.py tests/test_auth_account_service.py tests/test_settings.py" in text
    assert "deploy/Dockerfile" in text
    assert "docker build" in text
    assert "scripts/check_north_america_infra_contract.sh" in text
    assert "tests/test_north_america_redis_recovery.py" in text
    assert "RUN_NORTH_AMERICA_REDIS_RECOVERY=1 python -m pytest -q tests/test_north_america_redis_recovery_docker.py" in text
    assert "scripts/check_north_america_minio_recovery_contract.sh" in text
    assert "deploy/us-east-uat/Minio.Dockerfile" in text
    contract = text.split("\n  north-america-image:", 1)[0]
    for forbidden in (
        "docker push", "docker compose up", "scripts/release.py",
        "backend-singapore-delivery.yml", "ssh ", "kubectl", "secrets.", "permissions: write-all",
    ):
        assert forbidden not in contract


def test_north_america_infra_smoke_checks_service_boundaries() -> None:
    text = (ROOT / "scripts/check_north_america_infra_contract.sh").read_text()
    assert "deploy/us-east-uat/init-postgres.sh" in text
    assert "deploy/us-east-uat/start-redis.sh" in text
    assert "pg_has_role" not in text  # must test actual connections
    assert "NOPERM" in text
    assert "docker run" in text
    assert "trap cleanup EXIT" in text


def test_north_america_image_publication_is_main_only_after_validation() -> None:
    text = WORKFLOW.read_text()
    assert text.count("\n  north-america-image:\n") == 1
    validation, publish = text.split("\n  north-america-image:\n", 1)
    assert "permissions:\n  contents: read" in validation
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in publish
    assert "needs: north-america-contract" in publish
    assert "packages: write" in publish
    assert "secrets.GITHUB_TOKEN" in publish
    assert "deploy/Dockerfile" in publish
    assert "push: true" in publish
    assert "platforms: linux/amd64" in publish
    assert "north-america-main-${{ github.sha }}" in publish
    assert "org.opencontainers.image.revision=${{ github.sha }}" in publish
    assert "org.momcozy.release-target=north-america-staging" in publish
    assert "steps.build.outputs.digest" in publish
    assert "GITHUB_STEP_SUMMARY" in publish
    assert "docker buildx imagetools inspect" in publish
    for forbidden in ("docker compose up", "scripts/release.py", "ssh ", "kubectl"):
        assert forbidden not in publish


def test_published_b_digest_has_runtime_verification() -> None:
    publish = WORKFLOW.read_text().split("\n  north-america-image:\n", 1)[1]
    assert "scripts/check_north_america_published_image.py" in publish
    assert "IMAGE_DIGEST" in publish
    assert "docker pull" not in publish  # helper pulls only the immutable digest
