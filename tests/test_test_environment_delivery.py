import hashlib
import os
import subprocess
import tarfile
from pathlib import Path
from typing import IO, cast

import pytest

import scripts.test_release as test_release
from scripts.test_release import (
    BackendReleaseSpec,
    _check_collision_boundaries,
    _promote_release_pointer,
    _prune_backups,
    _write_secure_backup,
    build_deploy_commands,
    build_release_manifest,
    stage_release_snapshot,
    validate_commit_sha,
    validate_image_ref,
    validate_release_root,
)


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "backend-ci.yml"
DELIVERY_WORKFLOW = (
    ROOT / ".github" / "workflows" / "backend-test-delivery.yml"
)
TEST_COMPOSE = ROOT / "docker-compose.test.yml"
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


class SequenceRunner:
    def __init__(
        self, responses: list[subprocess.CompletedProcess[str]]
    ) -> None:
        self.responses = iter(responses)

    def run(self, *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return next(self.responses)


class BackupRunner:
    def run(self, *_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = cast(IO[bytes], kwargs["stdout"])
        stdout.write(b"database-backup")
        return subprocess.CompletedProcess([], 0, stdout="")


class FailingReadinessRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(
        self, command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command and command[0] == "curl":
            raise RuntimeError("new API is not ready")
        return subprocess.CompletedProcess(command, 0, stdout="")


def test_test_compose_only_consumes_an_explicit_release_image() -> None:
    compose = TEST_COMPOSE.read_text()

    assert "${MOMCOZY_BACKEND_IMAGE:?" in compose
    assert "build:" not in compose


def test_ci_publishes_one_sha_tagged_image_and_records_its_digest() -> None:
    workflow = CI_WORKFLOW.read_text()

    assert "      - name: Push the immutable commit tag" in workflow
    container = workflow.split("  container:\n", 1)[1]
    prerequisites = container.split("    permissions:", 1)[0]
    assert "      - test" in prerequisites
    assert "      - postgres-migration" in prerequisites
    assert "      - redis-product-profile" in prerequisites
    assert "      - object-storage-integration" in prerequisites
    assert "docker save" not in workflow
    assert "docker load" not in workflow
    assert "Download the already verified image" not in workflow
    assert "packages: write" in workflow
    assert "docker/login-action@" in workflow
    assert "docker/build-push-action@" in workflow
    assert "ghcr.io/${{ github.repository }}:${{ github.sha }}" in workflow
    assert "org.opencontainers.image.revision=${{ github.sha }}" in workflow
    assert (
        '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
        in workflow
    )
    assert r'\"org.opencontainers.image.revision\"' not in workflow
    assert "printf 'digest=%s\\n'" in workflow


def test_test_delivery_is_manual_protected_serial_and_host_key_checked() -> None:
    workflow = DELIVERY_WORKFLOW.read_text()

    assert "workflow_dispatch:" in workflow
    assert "environment:" in workflow
    assert "name: test" in workflow
    assert "group: momcozy-lab-backend-test" in workflow
    assert "issues: read" in workflow
    assert "packages: read" in workflow
    assert "Wait for independent test approval" in workflow
    assert "TEST_APPROVERS" in workflow
    assert "TEST_APPROVAL_ISSUE" in workflow
    assert "/approve-test" in workflow
    assert "GITHUB_TRIGGERING_ACTOR" not in workflow
    assert "Ignoring self-approval" not in workflow
    assert "needs: approve" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "ref: main" not in workflow
    assert "timeout-minutes: 45" in workflow
    assert "TEST_SSH_KNOWN_HOSTS" in workflow
    assert "Authenticate the host to GHCR with an ephemeral token" in workflow
    assert "GHCR_TOKEN: ${{ github.token }}" in workflow
    assert "REMOTE_DOCKER_CONFIG:" in workflow
    assert "docker login ghcr.io" in workflow
    assert "docker logout ghcr.io" in workflow
    assert "git archive" in workflow
    assert "scripts/test_release.py" in workflow
    assert "--image-ref" in workflow
    assert "rollback" in workflow
    assert "image_ref:" not in workflow
    assert "REQUESTED_COMMIT_SHA: ${{ inputs.commit_sha }}" in workflow
    assert 'github.ref == \'refs/heads/main\'' in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert "gh api --paginate" in workflow
    assert "users/${GITHUB_REPOSITORY_OWNER}/packages/container/momcozy-lab-backend/versions" in workflow
    assert "backend-image-manifest-" not in workflow
    assert "/usr/bin/flock" in workflow
    assert "test-release.lock" in workflow
    for run_block in _literal_run_blocks(workflow):
        assert "${{ inputs." not in run_block
    assert "StrictHostKeyChecking=no" not in workflow
    assert "docker compose build" not in workflow
    release_script = (ROOT / "scripts" / "test_release.py").read_text()
    assert '"sport = :8001"' in release_script


def test_repromoting_current_backend_preserves_the_distinct_previous_release(
    tmp_path: Path,
) -> None:
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
        "momcozy-lab-backend:test",
        "ghcr.io/hensonzh/momcozy-lab-backend:latest",
        "ghcr.io/hensonzh/momcozy-lab-backend@sha256:short",
    ):
        with pytest.raises(ValueError):
            validate_image_ref(value)
    with pytest.raises(ValueError):
        validate_release_root(Path("/opt/momcozy"))


def test_collision_gate_rejects_an_existing_unowned_test_network() -> None:
    runner = SequenceRunner(
        [
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CompletedProcess([], 0, stdout=""),
        ]
    )

    with pytest.raises(RuntimeError, match="owned by"):
        _check_collision_boundaries(runner)  # type: ignore[arg-type]


def test_deploy_plan_never_reconciles_stateful_services(
    tmp_path: Path,
) -> None:
    spec = BackendReleaseSpec(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        repo_dir=tmp_path / COMMIT_SHA,
        env_file=tmp_path / "backend.env",
        release_root=Path("/opt/momcozy-lab"),
        public_url="https://backend.example.test:8443",
        ca_file=Path("/etc/ssl/test-ca.pem"),
    )

    commands = build_deploy_commands(spec)
    rendered = [" ".join(command) for command in commands]
    joined = "\n".join(rendered)

    assert rendered[0] == f"docker pull {IMAGE_REF}"
    assert "up --detach --no-build postgres" not in joined
    assert "up --detach --no-build redis" not in joined
    assert "up --detach --no-build minio" not in joined
    assert "--no-deps --force-recreate api" in joined
    assert "check_object_storage_profile.py" in joined
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


def test_release_snapshot_retry_reuses_only_the_same_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(test_release, "EXPECTED_RELEASE_ROOT", tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "release.txt").write_text("immutable\n")
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source / "release.txt", arcname="release.txt")
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()

    first = stage_release_snapshot(
        archive=archive,
        archive_sha256=archive_sha,
        commit_sha=COMMIT_SHA,
        release_root=tmp_path,
        attempt_id="101-1",
    )
    second = stage_release_snapshot(
        archive=archive,
        archive_sha256=archive_sha,
        commit_sha=COMMIT_SHA,
        release_root=tmp_path,
        attempt_id="101-2",
    )

    assert first == second
    assert (first / "release.txt").read_text() == "immutable\n"
    assert (first / ".source-archive.sha256").read_text().strip() == archive_sha
    assert len((first / ".source-tree.sha256").read_text().strip()) == 64

    with pytest.raises(RuntimeError, match="checksum"):
        stage_release_snapshot(
            archive=archive,
            archive_sha256="f" * 64,
            commit_sha=COMMIT_SHA,
            release_root=tmp_path,
            attempt_id="101-3",
        )

    (first / "release.txt").write_text("tampered\n")
    with pytest.raises(RuntimeError, match="tree checksum"):
        stage_release_snapshot(
            archive=archive,
            archive_sha256=archive_sha,
            commit_sha=COMMIT_SHA,
            release_root=tmp_path,
            attempt_id="101-4",
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
        path = backup_dir / f"old-{index:02d}.dump"
        path.write_bytes(b"old")
    _prune_backups(backup_dir, keep=10)
    assert len(list(backup_dir.glob("*.dump"))) == 10


def test_failed_backend_switch_restores_current_manifest_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = BackendReleaseSpec(
        image_ref=IMAGE_REF,
        commit_sha=COMMIT_SHA,
        repo_dir=tmp_path / "candidate",
        env_file=tmp_path / "deploy.env",
        release_root=Path("/opt/momcozy-lab"),
        public_url="https://backend.example.test:8443",
        ca_file=tmp_path / "ca.pem",
    )
    current = BackendReleaseSpec(
        image_ref=f"ghcr.io/hensonzh/momcozy-lab-backend@sha256:{'e' * 64}",
        commit_sha="d" * 40,
        repo_dir=tmp_path / "current",
        env_file=candidate.env_file,
        release_root=candidate.release_root,
        public_url=candidate.public_url,
        ca_file=candidate.ca_file,
    )
    restored: list[BackendReleaseSpec | None] = []
    runner = FailingReadinessRunner()

    monkeypatch.setattr(test_release, "_validate_spec_files", lambda _spec: None)
    monkeypatch.setattr(test_release, "_validate_env_file", lambda _path: None)
    monkeypatch.setattr(test_release, "_check_collision_boundaries", lambda _runner: None)
    monkeypatch.setattr(test_release, "_verify_image_revision", lambda *_args: None)
    monkeypatch.setattr(
        test_release,
        "_require_healthy_infrastructure",
        lambda *_args: {"postgres": "postgres-1"},
    )
    monkeypatch.setattr(test_release, "_read_image_migration_head", lambda *_args: "head")
    monkeypatch.setattr(test_release, "_read_database_revision", lambda **_kwargs: "head")
    monkeypatch.setattr(test_release, "_current_release_spec", lambda _spec: current)
    monkeypatch.setattr(
        test_release,
        "_restore_backend",
        lambda *, previous, failed, runner: restored.append(previous),
    )

    with pytest.raises(RuntimeError, match="not ready"):
        test_release.deploy(candidate, runner)  # type: ignore[arg-type]

    assert restored == [current]
    assert not any("migrate" in command for command in runner.commands)
