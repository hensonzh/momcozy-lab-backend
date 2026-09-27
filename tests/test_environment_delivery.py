import hashlib
import os
import subprocess
import tarfile
from pathlib import Path
from typing import IO, cast

import pytest

import scripts.release as release
from scripts.release import (
    BackendReleaseSpec,
    _check_collision_boundaries,
    _promote_release_pointer,
    _prune_backups,
    _write_secure_backup,
    build_deploy_commands,
    build_release_manifest,
    stage_release_snapshot,
    validate_commit_sha,
    validate_environment,
    validate_image_ref,
    validate_release_root,
)


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "backend-ci.yml"
DELIVERY_WORKFLOW = ROOT / ".github" / "workflows" / "backend-delivery.yml"
DEPLOY_COMPOSE = ROOT / "docker-compose.deploy.yml"
DIGEST = "sha256:" + "a" * 64
COMMIT_SHA = "b" * 40
IMAGE_REF = f"ghcr.io/hensonzh/momcozy-lab-backend@{DIGEST}"


def _literal_run_blocks(workflow: str) -> list[str]:
    lines = workflow.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() != "run: |":
            continue
        indentation = len(line) - len(line.lstrip())
        block: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate and len(candidate) - len(candidate.lstrip()) <= indentation:
                break
            block.append(candidate)
        blocks.append("\n".join(block))
    return blocks


def _write_plan_env(path: Path, *, provider: str = "disabled", reports: bool = False) -> None:
    path.write_text(
        "APP_ENV=staging\n"
        "MOMCOZY_BACKEND_COMPOSE_PROJECT=momcozy-lab-backend-staging\n"
        "MOMCOZY_AGENT_COMPOSE_PROJECT=momcozy-lab-agent-staging\n"
        "MOMCOZY_NETWORK_NAME=momcozy-lab-staging\n"
        "MOMCOZY_BACKEND_API_BIND=127.0.0.1:8001\n"
        "MOMCOZY_PRODUCT_POSTGRES_DB=momcozy_staging\n"
        "MOMCOZY_POSTGRES_ADMIN_USER=momcozy_staging_admin\n"
        f"CONSULTATION_VIDEO_PROVIDER={provider}\n"
        + (
            "CARE_REPORT_RUNTIME_URL=http://agent\nCARE_REPORT_SERVICE_KEY=fixture\n"
            if reports
            else ""
        )
    )


class SequenceRunner:
    def __init__(self, responses: list[subprocess.CompletedProcess[str]]) -> None:
        self.responses = iter(responses)

    def run(self, *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return next(self.responses)


class BackupRunner:
    def run(self, *_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = cast(IO[bytes], kwargs["stdout"])
        stdout.write(b"database-backup")
        return subprocess.CompletedProcess([], 0, stdout="")


def test_deploy_compose_only_consumes_an_explicit_release_image() -> None:
    compose = DEPLOY_COMPOSE.read_text()
    assert "${MOMCOZY_BACKEND_IMAGE:?" in compose
    assert "build:" not in compose


def test_ci_publishes_one_sha_tagged_image_without_quota_bound_artifacts() -> None:
    workflow = CI_WORKFLOW.read_text()
    assert "Push the immutable commit tag" in workflow
    assert "docker/build-push-action@" in workflow
    assert "ghcr.io/${{ github.repository }}:${{ github.sha }}" in workflow
    assert "org.opencontainers.image.revision=${{ github.sha }}" in workflow
    assert 'docker push "${IMMUTABLE_IMAGE_TAG}"' in workflow
    assert "actions/upload-artifact" not in workflow


def test_delivery_is_environment_scoped_serial_and_host_key_checked() -> None:
    workflow = DELIVERY_WORKFLOW.read_text()
    assert "workflow_dispatch:" in workflow
    assert "options:\n          - staging\n          - production" in workflow
    assert "name: ${{ inputs.environment }}" in workflow
    assert "group: momcozy-lab-backend-${{ inputs.environment }}" in workflow
    assert "RELEASE_APPROVERS" in workflow
    assert "secrets.SSH_KNOWN_HOSTS" in workflow
    assert "StrictHostKeyChecking=no" not in workflow
    assert "scripts/release.py" in workflow
    assert "scripts/check_deployed_openapi_compatibility.py" in workflow
    assert 'git show "${RELEASE_COMMIT_SHA}:docs/openapi.generated.json"' in workflow
    assert workflow.index("Reject breaking changes against the live Product API") < workflow.index("Stage the exact commit without uncommitted files")
    assert "--environment '${DEPLOY_ENVIRONMENT}'" in workflow
    assert '"DEPLOY_ENV_FILE": root / "shared" / "backend" / f"{environment}.env"' in workflow
    assert '"DEPLOY_RELEASE_LOCK": root / "shared" / f"{environment}-release.lock"' in workflow
    assert "docker compose build" not in workflow


def test_release_identifiers_and_environment_are_strict() -> None:
    assert validate_commit_sha(COMMIT_SHA) == COMMIT_SHA
    assert validate_image_ref(IMAGE_REF) == IMAGE_REF
    assert validate_environment("staging") == "staging"
    assert validate_environment("production") == "production"
    assert validate_release_root(Path("/opt/momcozy-lab"), "staging") == Path("/opt/momcozy-lab")
    assert validate_release_root(Path("/opt/momcozy-lab-production"), "production") == Path("/opt/momcozy-lab-production")
    for environment, wrong_root in (
        ("production", Path("/opt/momcozy-lab")),
        ("staging", Path("/opt/momcozy-lab-production")),
    ):
        with pytest.raises(ValueError, match="release root"):
            validate_release_root(wrong_root, environment)
    assert "stage-snapshot --environment '${DEPLOY_ENVIRONMENT}'" in DELIVERY_WORKFLOW.read_text()
    for value in ("test", "prod", ""):
        with pytest.raises(ValueError):
            validate_environment(value)


def test_collision_gate_rejects_an_existing_unowned_network(tmp_path: Path) -> None:
    env_file = tmp_path / "backend.env"
    _write_plan_env(env_file)
    spec = BackendReleaseSpec(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        repo_dir=tmp_path,
        env_file=env_file,
        release_root=Path("/opt/momcozy-lab"),
        public_url="https://backend.example.test:8443",
        ca_file=tmp_path / "ca.pem",
    )
    runner = SequenceRunner(
        [
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CompletedProcess([], 0, stdout="other-project\n"),
        ]
    )
    with pytest.raises(RuntimeError, match="owned by"):
        _check_collision_boundaries(spec, runner)  # type: ignore[arg-type]


def test_deploy_plan_switches_only_enabled_application_services(
    tmp_path: Path
) -> None:
    env_file = tmp_path / "backend.env"
    _write_plan_env(env_file)
    spec = BackendReleaseSpec(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        repo_dir=tmp_path,
        env_file=env_file,
        release_root=Path("/opt/momcozy-lab"),
        public_url="https://backend.example.test:8443",
        ca_file=tmp_path / "ca.pem",
    )
    (tmp_path / release.COMPOSE_PATH).write_text(DEPLOY_COMPOSE.read_text())
    commands = build_deploy_commands(spec)
    start = next(command for command in commands if "up" in command)
    stop = next(command for command in commands if "stop" in command)
    assert all(name in start for name in ("api", "notification-worker", "auth-email-worker"))
    assert all(name in stop for name in release.RUNTIME_SERVICES)
    assert all(name not in start + stop for name in ("postgres", "redis", "minio"))


def test_release_manifest_contains_environment_and_traceability() -> None:
    manifest = build_release_manifest(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        migration_revision="20260924_0001",
        openapi_sha256="c" * 64,
        public_url="https://backend.example.test:8443/",
        released_at="2026-09-24T00:00:00Z",
        environment="production",
    )
    assert manifest["environment"] == "production"
    assert manifest["image_digest"] == DIGEST
    assert manifest["public_url"] == "https://backend.example.test:8443"
    assert not any("password" in key or "secret" in key for key in manifest)


def test_release_snapshot_retry_reuses_only_the_same_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(release.RELEASE_ROOTS, "staging", tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "release.txt").write_text("immutable\n")
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source / "release.txt", arcname="release.txt")
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="production release root"):
        stage_release_snapshot(
            archive=archive,
            archive_sha256=archive_sha,
            commit_sha=COMMIT_SHA,
            release_root=tmp_path,
            attempt_id="101-0",
            environment="production",
        )
    assert not (tmp_path / "releases").exists()

    first = stage_release_snapshot(
        archive=archive,
        archive_sha256=archive_sha,
        commit_sha=COMMIT_SHA,
        release_root=tmp_path,
        attempt_id="101-1",
        environment="staging",
    )
    second = stage_release_snapshot(
        archive=archive,
        archive_sha256=archive_sha,
        commit_sha=COMMIT_SHA,
        release_root=tmp_path,
        attempt_id="101-2",
        environment="staging",
    )
    assert first == second
    (first / "release.txt").write_text("tampered\n")
    with pytest.raises(RuntimeError, match="tree checksum"):
        stage_release_snapshot(
            archive=archive,
            archive_sha256=archive_sha,
            commit_sha=COMMIT_SHA,
            release_root=tmp_path,
            attempt_id="101-3",
            environment="staging",
        )


def test_database_backups_are_private_and_retained(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_path = backup_dir / "new.dump"
    _write_secure_backup(
        runner=BackupRunner(),  # type: ignore[arg-type]
        command=["pg_dump"],
        backup_path=backup_path,
        cwd=tmp_path,
        env={},
    )
    assert backup_path.read_bytes() == b"database-backup"
    assert os.stat(backup_dir).st_mode & 0o777 == 0o700
    assert os.stat(backup_path).st_mode & 0o777 == 0o600
    for index in range(12):
        (backup_dir / f"old-{index:02d}.dump").write_bytes(b"old")
    _prune_backups(backup_dir, keep=10)
    assert len(list(backup_dir.glob("*.dump"))) == 10


def test_promoting_current_release_preserves_distinct_previous(tmp_path: Path) -> None:
    current_release = tmp_path / "releases" / "backend" / ("a" * 40)
    previous_release = tmp_path / "releases" / "backend" / ("b" * 40)
    current_release.mkdir(parents=True)
    previous_release.mkdir(parents=True)
    current_link = tmp_path / "current" / "backend"
    previous_link = tmp_path / "previous" / "backend"
    current_link.parent.mkdir(parents=True)
    previous_link.parent.mkdir(parents=True)
    current_link.symlink_to(current_release)
    previous_link.symlink_to(previous_release)
    _promote_release_pointer(tmp_path, current_release)
    assert current_link.resolve() == current_release
    assert previous_link.resolve() == previous_release


@pytest.mark.parametrize(
    ("allowlist", "actor", "rerun_actor", "allowed"),
    [
        ("Operator, Second", "operator", "SECOND", True),
        ("operator", "stranger", "operator", False),
        ("operator", "operator", "stranger", False),
        ("", "operator", "operator", False),
    ],
)
def test_manual_release_operator_gate(
    allowlist: str, actor: str, rerun_actor: str, allowed: bool
) -> None:
    script = _literal_run_blocks(DELIVERY_WORKFLOW.read_text())[0]
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "RELEASE_APPROVERS": allowlist,
            "GITHUB_ACTOR": actor,
            "GITHUB_TRIGGERING_ACTOR": rerun_actor,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is allowed, result.stderr
