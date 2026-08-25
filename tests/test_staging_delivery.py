import subprocess
from pathlib import Path

import pytest

from scripts.staging_release import (
    BackendReleaseSpec,
    _check_collision_boundaries,
    build_deploy_commands,
    build_release_manifest,
    validate_commit_sha,
    validate_image_ref,
    validate_release_root,
)


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "backend-ci.yml"
DELIVERY_WORKFLOW = (
    ROOT / ".github" / "workflows" / "backend-staging-delivery.yml"
)
STAGING_COMPOSE = ROOT / "docker-compose.staging.yml"
DIGEST = "sha256:" + "a" * 64
COMMIT_SHA = "b" * 40
IMAGE_REF = f"ghcr.io/hensonzh/momcozy-lab-backend@{DIGEST}"


class SequenceRunner:
    def __init__(
        self, responses: list[subprocess.CompletedProcess[str]]
    ) -> None:
        self.responses = iter(responses)

    def run(self, *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return next(self.responses)


def test_staging_compose_only_consumes_an_explicit_release_image() -> None:
    compose = STAGING_COMPOSE.read_text()

    assert "${MOMCOZY_BACKEND_IMAGE:?" in compose
    assert "build:" not in compose


def test_ci_publishes_one_sha_tagged_image_and_records_its_digest() -> None:
    workflow = CI_WORKFLOW.read_text()

    assert "publish-image:" in workflow
    assert "packages: write" in workflow
    assert "docker/login-action@" in workflow
    assert "docker/build-push-action@" in workflow
    assert "ghcr.io/${{ github.repository }}:${{ github.sha }}" in workflow
    assert "org.opencontainers.image.revision=${{ github.sha }}" in workflow
    assert "steps.push.outputs.digest" in workflow
    assert "backend-image-manifest-${{ github.sha }}" in workflow


def test_staging_delivery_is_manual_protected_serial_and_host_key_checked() -> None:
    workflow = DELIVERY_WORKFLOW.read_text()

    assert "workflow_dispatch:" in workflow
    assert "environment:" in workflow
    assert "name: staging" in workflow
    assert "group: momcozy-lab-backend-staging" in workflow
    assert "STAGING_SSH_KNOWN_HOSTS" in workflow
    assert "git archive" in workflow
    assert "scripts/staging_release.py" in workflow
    assert "--image-ref" in workflow
    assert "rollback" in workflow
    assert "StrictHostKeyChecking=no" not in workflow
    assert "docker compose build" not in workflow
    release_script = (ROOT / "scripts" / "staging_release.py").read_text()
    assert '"sport = :8001"' in release_script


def test_release_identifiers_reject_mutable_or_ambiguous_values() -> None:
    assert validate_commit_sha(COMMIT_SHA) == COMMIT_SHA
    assert validate_image_ref(IMAGE_REF) == IMAGE_REF
    assert validate_release_root(Path("/opt/momcozy-lab")) == Path(
        "/opt/momcozy-lab"
    )

    for value in ("abc1234", "g" * 40, ""):
        with pytest.raises(ValueError):
            validate_commit_sha(value)
    for value in (
        "momcozy-lab-backend:staging",
        "ghcr.io/hensonzh/momcozy-lab-backend:latest",
        "ghcr.io/hensonzh/momcozy-lab-backend@sha256:short",
    ):
        with pytest.raises(ValueError):
            validate_image_ref(value)
    with pytest.raises(ValueError):
        validate_release_root(Path("/opt/momcozy"))


def test_collision_gate_rejects_an_existing_unowned_staging_network() -> None:
    runner = SequenceRunner(
        [
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CompletedProcess([], 0, stdout=""),
        ]
    )

    with pytest.raises(RuntimeError, match="owned by"):
        _check_collision_boundaries(runner)  # type: ignore[arg-type]


def test_deploy_plan_pulls_backs_up_migrates_then_replaces_without_building(
    tmp_path: Path,
) -> None:
    spec = BackendReleaseSpec(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        repo_dir=tmp_path / COMMIT_SHA,
        env_file=tmp_path / "backend.env",
        release_root=Path("/opt/momcozy-lab"),
        public_url="https://backend.example.test:8443",
        ca_file=Path("/etc/ssl/staging-ca.pem"),
    )

    commands = build_deploy_commands(spec)
    rendered = [" ".join(command) for command in commands]
    joined = "\n".join(rendered)

    assert rendered[0] == f"docker pull {IMAGE_REF}"
    assert "pg_dump" in joined
    assert "run --rm --no-deps migrate" in joined
    assert joined.index("run --rm --no-deps migrate") < joined.index(
        "--force-recreate api"
    )
    assert "--no-build" in joined
    assert "docker compose build" not in joined
    assert "down --volumes" not in joined


def test_release_manifest_contains_traceability_but_no_secret_values() -> None:
    manifest = build_release_manifest(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        migration_revision="backend_head",
        openapi_sha256="c" * 64,
        public_url="https://backend.example.test:8443",
        released_at="2026-08-25T00:00:00Z",
    )

    assert manifest["image_digest"] == DIGEST
    assert manifest["commit"] == COMMIT_SHA
    assert manifest["migration_revision"] == "backend_head"
    assert manifest["openapi_sha256"] == "c" * 64
    assert not any("password" in key or "secret" in key for key in manifest)
