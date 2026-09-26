#!/usr/bin/env python3
"""Stage, bootstrap, deploy, restart, or roll back Product Backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Sequence, cast


SERVICE_NAME = "product-backend"
IMAGE_REPOSITORY = "ghcr.io/hensonzh/momcozy-lab-backend"
DEPLOY_ENVIRONMENTS = frozenset({"staging", "production"})
RELEASE_ROOTS = {
    "staging": Path("/opt/momcozy-lab"),  # Existing host path; no staging data migration.
    "production": Path("/opt/momcozy-lab-production"),
}
OPENAPI_PATH = Path("docs/openapi.generated.json")
COMPOSE_PATH = Path("docker-compose.deploy.yml")
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IMAGE_REF_PATTERN = re.compile(
    rf"^{re.escape(IMAGE_REPOSITORY)}@sha256:[0-9a-f]{{64}}$"
)
SAFE_REVISION_PATTERN = re.compile(r"^[0-9A-Za-z_.-]+$")
SAFE_ATTEMPT_PATTERN = re.compile(r"^[0-9]+-[0-9]+$")
BACKUP_RETENTION_COUNT = 10
SOURCE_ARCHIVE_MARKER = ".source-archive.sha256"
SOURCE_TREE_MARKER = ".source-tree.sha256"
SOURCE_TREE_EXCLUSIONS = frozenset(
    {SOURCE_ARCHIVE_MARKER, SOURCE_TREE_MARKER, "release-manifest.json"}
)
RUNTIME_SERVICES = (
    "api", "notification-worker", "auth-email-worker",
)
KNOWN_SECRET_PLACEHOLDERS = frozenset(
    {
        "test-service-key-with-at-least-32-bytes",
        "test-agent-runtime-service-key-with-at-least-32-bytes",
        "replace-me",
        "changeme",
        "minioadmin",
        "momcozy-beta",
    }
)


@dataclass(frozen=True)
class BackendReleaseSpec:
    image_ref: str
    commit_sha: str
    repo_dir: Path
    env_file: Path
    release_root: Path
    public_url: str
    ca_file: Path
    environment: str = "staging"


class CommandRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        stdout: IO[bytes] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        print(f"$ {' '.join(command)}", flush=True)
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            text=stdout is None,
            capture_output=capture_output,
            stdout=stdout,
            check=check,
        )


def validate_environment(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in DEPLOY_ENVIRONMENTS:
        raise ValueError("environment must be staging or production")
    return normalized


def validate_commit_sha(value: str) -> str:
    normalized = value.strip().lower()
    if not FULL_SHA_PATTERN.fullmatch(normalized):
        raise ValueError("commit SHA must contain exactly 40 lowercase hex characters")
    return normalized


def validate_image_ref(value: str) -> str:
    normalized = value.strip().lower()
    if not IMAGE_REF_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"image ref must pin {IMAGE_REPOSITORY} by a sha256 digest"
        )
    return normalized


def validate_release_root(value: Path, environment: str) -> Path:
    normalized = value.expanduser().resolve()
    expected = RELEASE_ROOTS[validate_environment(environment)]
    if normalized != expected:
        raise ValueError(f"{environment} release root must be {expected}")
    return normalized


def build_release_manifest(
    *,
    image_ref: str,
    commit_sha: str,
    migration_revision: str,
    openapi_sha256: str,
    public_url: str,
    released_at: str,
    environment: str = "staging",
) -> dict[str, Any]:
    validate_image_ref(image_ref)
    validate_commit_sha(commit_sha)
    environment = validate_environment(environment)
    _validate_sha256(openapi_sha256, "OpenAPI SHA256")
    if not SAFE_REVISION_PATTERN.fullmatch(migration_revision):
        raise ValueError("migration revision contains unsafe characters")
    return {
        "schema_version": 1,
        "service": SERVICE_NAME,
        "environment": environment,
        "commit": commit_sha,
        "image_ref": image_ref,
        "image_digest": image_ref.rsplit("@", maxsplit=1)[1],
        "migration_revision": migration_revision,
        "openapi_sha256": openapi_sha256,
        "public_url": public_url.rstrip("/"),
        "released_at": released_at,
    }


def build_image_manifest(*, image_ref: str, commit_sha: str) -> dict[str, Any]:
    validate_image_ref(image_ref)
    validate_commit_sha(commit_sha)
    return {
        "schema_version": 1,
        "service": SERVICE_NAME,
        "commit": commit_sha,
        "image_ref": image_ref,
        "image_digest": image_ref.rsplit("@", maxsplit=1)[1],
    }


def stage_release_snapshot(
    *,
    archive: Path,
    archive_sha256: str,
    commit_sha: str,
    release_root: Path,
    attempt_id: str,
    environment: str,
) -> Path:
    root = validate_release_root(release_root, environment)
    commit = validate_commit_sha(commit_sha)
    _validate_sha256(archive_sha256, "archive SHA256")
    if not SAFE_ATTEMPT_PATTERN.fullmatch(attempt_id):
        raise ValueError("attempt id must be <run-id>-<attempt-number>")
    if not archive.is_file() or archive.is_symlink():
        raise FileNotFoundError(archive)
    if _sha256_file(archive) != archive_sha256:
        raise RuntimeError("release archive checksum does not match")

    release_dir = root / "releases" / "backend" / commit
    if release_dir.exists():
        _verify_staged_snapshot(release_dir, archive_sha256)
        print(f"Reusing verified Product Backend snapshot: {release_dir}")
        return release_dir

    attempts_root = root / "releases" / ".attempts" / "backend"
    attempts_root.mkdir(parents=True, mode=0o750, exist_ok=True)
    attempt_dir = attempts_root / f"{commit}-{attempt_id}"
    attempt_dir.mkdir(mode=0o750)
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            bundle.extractall(attempt_dir, filter="data")
        for reserved_name in SOURCE_TREE_EXCLUSIONS:
            reserved_path = attempt_dir / reserved_name
            if reserved_path.exists() or reserved_path.is_symlink():
                raise RuntimeError(
                    f"release archive contains reserved path: {reserved_name}"
                )
        source_tree_sha256 = _source_tree_sha256(attempt_dir)
        _write_text_exclusive(
            attempt_dir / SOURCE_ARCHIVE_MARKER,
            f"{archive_sha256}\n",
            mode=0o444,
        )
        _write_text_exclusive(
            attempt_dir / SOURCE_TREE_MARKER,
            f"{source_tree_sha256}\n",
            mode=0o444,
        )
        release_dir.parent.mkdir(parents=True, mode=0o750, exist_ok=True)
        os.rename(attempt_dir, release_dir)
    except Exception:
        print(f"Failed snapshot retained for diagnosis: {attempt_dir}", file=sys.stderr)
        raise
    _verify_staged_snapshot(release_dir, archive_sha256)
    print(f"Staged immutable Product Backend snapshot: {release_dir}")
    return release_dir


def build_deploy_commands(spec: BackendReleaseSpec) -> list[list[str]]:
    compose = _compose_base(spec)
    return [
        ["docker", "pull", spec.image_ref],
        _image_revision_command(spec.image_ref),
        [*compose, "config", "--quiet"],
        [
            *compose,
            "run",
            "--rm",
            "--no-deps",
            "api",
            "python",
            "-c",
            (
                "from app.core.settings import Settings; "
                "Settings.from_env().validate_for_startup()"
            ),
        ],
        [
            *compose,
            "run",
            "--rm",
            "--no-deps",
            "api",
            "python",
            "scripts/check_object_storage_profile.py",
        ],
        [*compose, "stop", "--timeout", "30", *_runtime_services(spec, enabled_only=False)],
        [
            *compose,
            "up",
            "--detach",
            "--no-build",
            "--no-deps",
            "--force-recreate",
            *_runtime_services(spec),
        ],
        _local_readiness_command(spec),
        _public_readiness_command(spec.public_url, spec.ca_file),
    ]


def bootstrap(spec: BackendReleaseSpec, runner: CommandRunner) -> None:
    """Create shared deployment infrastructure without deploying application code."""
    _validate_spec_files(spec)
    _validate_env_file(spec.env_file, spec.environment)
    _check_collision_boundaries(spec, runner)
    env = _command_env(spec)
    compose = _compose_base(spec)
    runner.run(["docker", "pull", spec.image_ref], env=env)
    _verify_image_revision(spec, runner, env)
    runner.run([*compose, "config", "--quiet"], cwd=spec.repo_dir, env=env)

    ids = {
        service: _service_container_ids(spec, service, runner, env)
        for service in ("postgres", "redis", "minio")
    }
    present = sum(bool(values) for values in ids.values())
    if present not in {0, len(ids)}:
        raise RuntimeError(
            "shared infrastructure is partially present; repair it explicitly before bootstrap"
        )
    if present == 0:
        runner.run(
            [
                *compose,
                "up",
                "--detach",
                "--wait",
                "--wait-timeout",
                "120",
                "--no-build",
                "postgres",
                "redis",
                "minio",
            ],
            cwd=spec.repo_dir,
            env=env,
        )
    _require_healthy_infrastructure(spec, runner, env)
    runner.run(
        [
            *compose,
            "--profile",
            "tools",
            "run",
            "--rm",
            "--no-deps",
            "minio-init",
        ],
        cwd=spec.repo_dir,
        env=env,
    )
    print("Product Backend shared deployment infrastructure is healthy and initialized.")


def deploy(spec: BackendReleaseSpec, runner: CommandRunner) -> Path:
    _validate_spec_files(spec)
    _validate_env_file(spec.env_file, spec.environment)
    _check_collision_boundaries(spec, runner)
    env = _command_env(spec)
    commands = build_deploy_commands(spec)

    runner.run(commands[0], cwd=spec.repo_dir, env=env)
    _verify_image_revision(spec, runner, env)
    runner.run(commands[2], cwd=spec.repo_dir, env=env)
    infrastructure = _require_healthy_infrastructure(spec, runner, env)
    runner.run(commands[3], cwd=spec.repo_dir, env=env)

    image_head = _read_image_migration_head(spec, runner, env)
    deployment = _deployment_values(spec)
    database_revision = _read_database_revision(
        postgres_container=infrastructure["postgres"],
        database=deployment["MOMCOZY_PRODUCT_POSTGRES_DB"],
        username=deployment["MOMCOZY_POSTGRES_ADMIN_USER"],
        runner=runner,
    )
    if database_revision != image_head:
        backup_path = _backup_path(spec)
        _write_secure_backup(
            runner=runner,
            command=_pg_dump_command(
                infrastructure["postgres"],
                database=deployment["MOMCOZY_PRODUCT_POSTGRES_DB"],
                username=deployment["MOMCOZY_POSTGRES_ADMIN_USER"],
            ),
            backup_path=backup_path,
            cwd=spec.repo_dir,
            env=env,
        )
        runner.run(
            [
                *_compose_base(spec),
                "--profile",
                "tools",
                "run",
                "--rm",
                "--no-deps",
                "migrate",
            ],
            cwd=spec.repo_dir,
            env=env,
        )
        database_revision = _read_database_revision(
            postgres_container=infrastructure["postgres"],
            database=deployment["MOMCOZY_PRODUCT_POSTGRES_DB"],
            username=deployment["MOMCOZY_POSTGRES_ADMIN_USER"],
            runner=runner,
        )
        if database_revision != image_head:
            raise RuntimeError("Product Backend migration did not reach the image head")
        _prune_backups(backup_path.parent, keep=BACKUP_RETENTION_COUNT)
    else:
        print(f"Schema unchanged at {image_head}; skipping backup and migration.")

    runner.run(commands[4], cwd=spec.repo_dir, env=env)
    previous = _current_release_spec(spec)
    switched = False
    try:
        switched = True
        for command in commands[5:]:
            runner.run(command, cwd=spec.repo_dir, env=env)
        manifest_path = _write_and_promote_manifest(
            spec,
            migration_revision=image_head,
        )
    except Exception as deploy_error:
        if switched:
            try:
                _restore_backend(previous=previous, failed=spec, runner=runner)
            except Exception as restore_error:
                raise RuntimeError(
                    "Product Backend deploy failed and the previous release could not be restored: "
                    f"{restore_error}"
                ) from deploy_error
        raise
    print(f"Product Backend release promoted: {manifest_path}")
    return manifest_path


def restart_current(
    *,
    environment: str,
    env_file: Path,
    release_root: Path,
    public_url: str,
    ca_file: Path,
    runner: CommandRunner,
) -> Path:
    root = validate_release_root(release_root, environment)
    current_dir = _resolved_release_link(root / "current" / "backend")
    spec = _spec_from_manifest(
        current_dir,
        environment=environment,
        env_file=env_file,
        release_root=root,
        public_url=public_url,
        ca_file=ca_file,
    )
    _validate_spec_files(spec)
    _validate_env_file(spec.env_file, spec.environment)
    _check_collision_boundaries(spec, runner)
    env = _command_env(spec)
    _require_healthy_infrastructure(spec, runner, env)
    runner.run(["docker", "pull", spec.image_ref], env=env)
    _verify_image_revision(spec, runner, env)
    _start_backend(spec, runner)
    return current_dir / "release-manifest.json"


def rollback(
    *,
    environment: str,
    env_file: Path,
    release_root: Path,
    public_url: str,
    ca_file: Path,
    confirm_schema_compatible: bool,
    runner: CommandRunner,
) -> Path:
    root = validate_release_root(release_root, environment)
    if not confirm_schema_compatible:
        raise ValueError("rollback requires --confirm-schema-compatible")
    current_link = root / "current" / "backend"
    previous_link = root / "previous" / "backend"
    current_dir = _resolved_release_link(current_link)
    previous_dir = _resolved_release_link(previous_link)
    current = _spec_from_manifest(
        current_dir,
        environment=environment,
        env_file=env_file,
        release_root=root,
        public_url=public_url,
        ca_file=ca_file,
    )
    previous = _spec_from_manifest(
        previous_dir,
        environment=environment,
        env_file=env_file,
        release_root=root,
        public_url=public_url,
        ca_file=ca_file,
    )
    _validate_spec_files(previous)
    _validate_env_file(previous.env_file, previous.environment)
    _check_collision_boundaries(previous, runner)
    env = _command_env(previous)
    _require_healthy_infrastructure(previous, runner, env)
    runner.run(["docker", "pull", previous.image_ref], env=env)
    _verify_image_revision(previous, runner, env)
    try:
        _stop_backend(current, runner)
        _start_backend(previous, runner)
    except Exception as rollback_error:
        try:
            _stop_backend(previous, runner)
            _start_backend(current, runner)
        except Exception as restore_error:
            raise RuntimeError(
                "rollback failed and the current Product Backend could not be restored: "
                f"{restore_error}"
            ) from rollback_error
        raise
    _replace_symlink(previous_link, current_dir)
    _replace_symlink(current_link, previous_dir)
    print(f"Product Backend rolled back to {previous.commit_sha}")
    return previous_dir / "release-manifest.json"


def _compose_base(spec: BackendReleaseSpec) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(spec.env_file),
        "-f",
        str(spec.repo_dir / COMPOSE_PATH),
    ]


def _runtime_services(
    spec: BackendReleaseSpec, *, enabled_only: bool = True
) -> list[str]:
    # Source snapshots use the repository's two-space Compose service layout.
    # Older releases may not define workers added by a newer release.
    compose = (spec.repo_dir / COMPOSE_PATH).read_text()
    defined = set(re.findall(r"^  ([a-z][a-z-]+):\s*$", compose, re.MULTILINE))
    services = [name for name in RUNTIME_SERVICES if name in defined]
    if "api" not in services:
        raise ValueError("release Compose must define the API service")
    if not enabled_only:
        return services
    return services


def _stop_backend(spec: BackendReleaseSpec, runner: CommandRunner) -> None:
    runner.run(
        [*_compose_base(spec), "stop", "--timeout", "30",
         *_runtime_services(spec, enabled_only=False)],
        cwd=spec.repo_dir,
        env=_command_env(spec),
    )


def _command_env(spec: BackendReleaseSpec) -> dict[str, str]:
    return {
        **os.environ,
        "MOMCOZY_BACKEND_IMAGE": spec.image_ref,
        "MOMCOZY_BACKEND_ENV_FILE": str(spec.env_file),
    }


def _validate_spec_files(spec: BackendReleaseSpec) -> None:
    validate_environment(spec.environment)
    validate_commit_sha(spec.commit_sha)
    validate_image_ref(spec.image_ref)
    root = validate_release_root(spec.release_root, spec.environment)
    expected_repo_dir = root / "releases" / "backend" / spec.commit_sha
    if spec.repo_dir.resolve() != expected_repo_dir:
        raise ValueError(f"repo directory must be {expected_repo_dir}")
    for relative_path in (COMPOSE_PATH, OPENAPI_PATH):
        if not (spec.repo_dir / relative_path).is_file():
            raise FileNotFoundError(spec.repo_dir / relative_path)
    if not spec.env_file.is_file():
        raise FileNotFoundError(spec.env_file)
    if not spec.ca_file.is_file():
        raise FileNotFoundError(spec.ca_file)
    if not spec.public_url.startswith("https://"):
        raise ValueError("public URL must use HTTPS")
    values = _read_env_values(spec.env_file)
    expected_public_url = values.get("MOMCOZY_BACKEND_PUBLIC_URL", "").rstrip("/")
    if expected_public_url and spec.public_url.rstrip("/") != expected_public_url:
        raise ValueError("public URL does not match MOMCOZY_BACKEND_PUBLIC_URL")


def _validate_env_file(path: Path, expected_environment: str) -> None:
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError(f"deployment env must not be group/world readable: {mode:o}")
    values = _read_env_values(path)
    environment = validate_environment(expected_environment)
    if values.get("APP_ENV", "").strip().lower() != environment:
        raise ValueError(f"deployment env APP_ENV must be {environment}")
    if "MOMCOZY_BACKEND_IMAGE" in values:
        raise ValueError("MOMCOZY_BACKEND_IMAGE is release-owned and must not be in deploy.env")
    required = (
        "APP_ENV",
        "MOMCOZY_BACKEND_COMPOSE_PROJECT",
        "MOMCOZY_AGENT_COMPOSE_PROJECT",
        "MOMCOZY_NETWORK_NAME",
        "MOMCOZY_BACKEND_API_BIND",
        "MOMCOZY_AGENT_API_BIND",
        "MOMCOZY_BACKEND_PUBLIC_URL",
        "MOMCOZY_AGENT_PUBLIC_URL",
        "MOMCOZY_POSTGRES_ADMIN_USER",
        "MOMCOZY_PRODUCT_POSTGRES_DB",
        "MOMCOZY_PRODUCT_POSTGRES_USER",
        "MOMCOZY_AGENT_POSTGRES_DB",
        "MOMCOZY_AGENT_POSTGRES_USER",
        "MOMCOZY_PRODUCT_MINIO_BUCKET",
        "MOMCOZY_AGENT_MINIO_BUCKET",
        "MOMCOZY_POSTGRES_ADMIN_PASSWORD",
        "MOMCOZY_PRODUCT_POSTGRES_PASSWORD",
        "MOMCOZY_AGENT_POSTGRES_PASSWORD",
        "MOMCOZY_REDIS_ADMIN_PASSWORD",
        "MOMCOZY_PRODUCT_REDIS_PASSWORD",
        "MOMCOZY_AGENT_REDIS_PASSWORD",
        "MOMCOZY_MINIO_ROOT_USER",
        "MOMCOZY_MINIO_ROOT_PASSWORD",
        "MOMCOZY_PRODUCT_MINIO_ACCESS_KEY",
        "MOMCOZY_PRODUCT_MINIO_SECRET_KEY",
        "MOMCOZY_AGENT_MINIO_ACCESS_KEY",
        "MOMCOZY_AGENT_MINIO_SECRET_KEY",
        "AUTH_JWT_PRIVATE_KEY_B64",
        "AUTH_INVITE_CODES",
        "SERVICE_API_KEY",
        "AGENT_RUNTIME_SERVICE_API_KEY",
    )
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise ValueError(f"deployment env is missing required values: {', '.join(missing)}")
    secret_names = (
        "MOMCOZY_POSTGRES_ADMIN_PASSWORD",
        "MOMCOZY_PRODUCT_POSTGRES_PASSWORD",
        "MOMCOZY_AGENT_POSTGRES_PASSWORD",
        "MOMCOZY_REDIS_ADMIN_PASSWORD",
        "MOMCOZY_PRODUCT_REDIS_PASSWORD",
        "MOMCOZY_AGENT_REDIS_PASSWORD",
        "MOMCOZY_MINIO_ROOT_USER",
        "MOMCOZY_MINIO_ROOT_PASSWORD",
        "MOMCOZY_PRODUCT_MINIO_ACCESS_KEY",
        "MOMCOZY_PRODUCT_MINIO_SECRET_KEY",
        "MOMCOZY_AGENT_MINIO_ACCESS_KEY",
        "MOMCOZY_AGENT_MINIO_SECRET_KEY",
        "AUTH_JWT_PRIVATE_KEY_B64",
        "AUTH_INVITE_CODES",
        "SERVICE_API_KEY",
        "AGENT_RUNTIME_SERVICE_API_KEY",
    )
    unsafe = [
        name for name in secret_names if _is_known_placeholder(values[name])
    ]
    if any(
        _is_known_placeholder(code)
        for code in values["AUTH_INVITE_CODES"].split(",")
    ) and "AUTH_INVITE_CODES" not in unsafe:
        unsafe.append("AUTH_INVITE_CODES")
    if unsafe:
        raise ValueError(f"deployment env contains placeholder values: {', '.join(unsafe)}")
    _require_distinct(
        values,
        (
            "MOMCOZY_POSTGRES_ADMIN_PASSWORD",
            "MOMCOZY_PRODUCT_POSTGRES_PASSWORD",
            "MOMCOZY_AGENT_POSTGRES_PASSWORD",
        ),
    )
    _require_distinct(
        values,
        (
            "MOMCOZY_REDIS_ADMIN_PASSWORD",
            "MOMCOZY_PRODUCT_REDIS_PASSWORD",
            "MOMCOZY_AGENT_REDIS_PASSWORD",
        ),
    )
    _require_distinct(
        values,
        (
            "MOMCOZY_MINIO_ROOT_USER",
            "MOMCOZY_PRODUCT_MINIO_ACCESS_KEY",
            "MOMCOZY_AGENT_MINIO_ACCESS_KEY",
        ),
    )
    _require_distinct(
        values,
        (
            "MOMCOZY_MINIO_ROOT_PASSWORD",
            "MOMCOZY_PRODUCT_MINIO_SECRET_KEY",
            "MOMCOZY_AGENT_MINIO_SECRET_KEY",
        ),
    )
    _require_distinct(values, ("SERVICE_API_KEY", "AGENT_RUNTIME_SERVICE_API_KEY"))


def _is_known_placeholder(value: str) -> bool:
    normalized = value.strip()
    return (
        normalized.lower() in KNOWN_SECRET_PLACEHOLDERS
        or (normalized.startswith("${") and normalized.endswith("}"))
        or normalized.upper().startswith("REPLACE_WITH_")
        or normalized == "0" * 64
    )


def _require_distinct(values: dict[str, str], names: Sequence[str]) -> None:
    selected = [values[name].strip() for name in names]
    if len(selected) != len(set(selected)):
        raise ValueError(f"deployment credentials must be distinct: {', '.join(names)}")


def _read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def _check_collision_boundaries(
    spec: BackendReleaseSpec, runner: CommandRunner
) -> None:
    values = _deployment_values(spec)
    compose_project = values["MOMCOZY_BACKEND_COMPOSE_PROJECT"]
    network_name = values["MOMCOZY_NETWORK_NAME"]
    host, port = _loopback_bind(values["MOMCOZY_BACKEND_API_BIND"])
    published_port = f"{host}:{port}->"

    containers = runner.run(
        ["docker", "ps", "--format", "{{json .}}"],
        capture_output=True,
    )
    port_owned_by_compose = False
    for line in (containers.stdout or "").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        ports = str(item.get("Ports", ""))
        labels = str(item.get("Labels", ""))
        if published_port in ports and (
            f"com.docker.compose.project={compose_project}" not in labels
        ):
            raise RuntimeError(f"{host}:{port} is owned by another container")
        if published_port in ports:
            port_owned_by_compose = True

    listeners = runner.run(
        ["ss", "-H", "-ltn", "sport", "=", f":{port}"],
        capture_output=True,
        check=False,
    )
    if listeners.returncode != 0:
        raise RuntimeError(f"could not inspect host listener {host}:{port}")
    if (listeners.stdout or "").strip() and not port_owned_by_compose:
        raise RuntimeError(f"{host}:{port} is owned by a non-Docker process")

    network = runner.run(
        [
            "docker",
            "network",
            "inspect",
            network_name,
            "--format",
            '{{ index .Labels "com.docker.compose.project" }}',
        ],
        capture_output=True,
        check=False,
    )
    if network.returncode == 0:
        owner = (network.stdout or "").strip()
        if owner != compose_project:
            raise RuntimeError(f"{network_name} is owned by {owner}")


def _deployment_values(spec: BackendReleaseSpec) -> dict[str, str]:
    values = _read_env_values(spec.env_file)
    if values.get("APP_ENV", "").strip().lower() != spec.environment:
        raise ValueError(
            f"deployment env APP_ENV must be {spec.environment}"
        )
    return values


def _loopback_bind(value: str) -> tuple[str, int]:
    match = re.fullmatch(r"(127\.0\.0\.1):([0-9]{1,5})", value.strip())
    if not match:
        raise ValueError("deployment API bind must use 127.0.0.1:<port>")
    port = int(match.group(2))
    if not 1 <= port <= 65535:
        raise ValueError("deployment API bind port is invalid")
    return match.group(1), port


def _require_healthy_infrastructure(
    spec: BackendReleaseSpec,
    runner: CommandRunner,
    env: dict[str, str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for service in ("postgres", "redis", "minio"):
        ids = _service_container_ids(spec, service, runner, env)
        if len(ids) != 1:
            raise RuntimeError(
                f"expected one existing {service} container; run bootstrap explicitly first"
            )
        state = runner.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                ids[0],
            ],
            capture_output=True,
        )
        if (state.stdout or "").strip() != "true healthy":
            raise RuntimeError(f"existing shared {service} container is not healthy")
        result[service] = ids[0]
    return result


def _service_container_ids(
    spec: BackendReleaseSpec,
    service: str,
    runner: CommandRunner,
    env: dict[str, str],
) -> list[str]:
    result = runner.run(
        [*_compose_base(spec), "ps", "--quiet", service],
        cwd=spec.repo_dir,
        env=env,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not inspect shared {service} container")
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _image_revision_command(image_ref: str) -> list[str]:
    return [
        "docker",
        "image",
        "inspect",
        "--format",
        '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
        image_ref,
    ]


def _verify_image_revision(
    spec: BackendReleaseSpec,
    runner: CommandRunner,
    env: dict[str, str],
) -> None:
    revision = runner.run(
        _image_revision_command(spec.image_ref),
        capture_output=True,
        env=env,
    )
    if (revision.stdout or "").strip() != spec.commit_sha:
        raise RuntimeError("image revision label does not match the requested commit")


def _read_image_migration_head(
    spec: BackendReleaseSpec,
    runner: CommandRunner,
    env: dict[str, str],
) -> str:
    result = runner.run(
        [
            "docker",
            "run",
            "--rm",
            spec.image_ref,
            "python",
            "-c",
            (
                "from alembic.config import Config; "
                "from alembic.script import ScriptDirectory; "
                "heads=ScriptDirectory.from_config(Config('alembic.ini')).get_heads(); "
                "assert len(heads) == 1, f'expected one Alembic head, got {heads}'; "
                "print(heads[0])"
            ),
        ],
        capture_output=True,
        env=env,
    )
    revision = (result.stdout or "").strip()
    if not SAFE_REVISION_PATTERN.fullmatch(revision):
        raise RuntimeError("image does not contain one safe Product Backend migration head")
    return revision


def _read_database_revision(
    *,
    postgres_container: str,
    database: str,
    username: str,
    runner: CommandRunner,
) -> str:
    existence = _psql_query(
        postgres_container,
        database=database,
        username=username,
        sql="SELECT to_regclass('public.alembic_version') IS NOT NULL",
        runner=runner,
    ).lower()
    if existence in {"f", "false"}:
        return ""
    if existence not in {"t", "true"}:
        raise RuntimeError("could not determine whether alembic_version exists")
    revision = _psql_query(
        postgres_container,
        database=database,
        username=username,
        sql="SELECT version_num FROM alembic_version",
        runner=runner,
    )
    if not SAFE_REVISION_PATTERN.fullmatch(revision):
        raise RuntimeError("could not read a safe Product Backend migration revision")
    return revision


def _psql_query(
    postgres_container: str,
    *,
    database: str,
    username: str,
    sql: str,
    runner: CommandRunner,
) -> str:
    result = runner.run(
        [
            "docker",
            "exec",
            postgres_container,
            "psql",
            "--username",
            username,
            "--dbname",
            database,
            "--tuples-only",
            "--no-align",
            "--command",
            sql,
        ],
        capture_output=True,
    )
    return (result.stdout or "").strip()


def _pg_dump_command(
    postgres_container: str, *, database: str, username: str
) -> list[str]:
    return [
        "docker",
        "exec",
        postgres_container,
        "pg_dump",
        "--username",
        username,
        "--dbname",
        database,
        "--format",
        "custom",
    ]


def _write_secure_backup(
    *,
    runner: CommandRunner,
    command: Sequence[str],
    backup_path: Path,
    cwd: Path,
    env: dict[str, str],
) -> None:
    backup_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(backup_path.parent, 0o700)
    partial = backup_path.with_name(f".{backup_path.name}.partial-{os.getpid()}")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as backup_file:
            runner.run(command, cwd=cwd, env=env, stdout=backup_file)
        os.replace(partial, backup_path)
        os.chmod(backup_path, 0o600)
    except Exception:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
        raise
    print(f"Created private Product Backend database backup: {backup_path}")


def _prune_backups(directory: Path, *, keep: int) -> None:
    if keep < 1:
        raise ValueError("backup retention must be positive")
    backups = sorted(
        path
        for path in directory.glob("*.dump")
        if path.is_file() and not path.is_symlink()
    )
    for expired in backups[:-keep]:
        expired.unlink()


def _backup_path(spec: BackendReleaseSpec) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return (
        spec.release_root
        / "backups"
        / "backend"
        / f"{timestamp}-{spec.commit_sha}.dump"
    )


def _write_and_promote_manifest(
    spec: BackendReleaseSpec,
    *,
    migration_revision: str,
) -> Path:
    manifest = build_release_manifest(
        image_ref=spec.image_ref,
        commit_sha=spec.commit_sha,
        migration_revision=migration_revision,
        openapi_sha256=_sha256_file(spec.repo_dir / OPENAPI_PATH),
        public_url=spec.public_url,
        released_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        environment=spec.environment,
    )
    manifest = {**manifest, "environment": spec.environment}
    manifest_path = spec.repo_dir / "release-manifest.json"
    _write_json_atomic(manifest_path, manifest)
    _write_json_atomic(
        spec.release_root / "manifests" / f"backend-{spec.commit_sha}.json",
        manifest,
    )
    _promote_release_pointer(spec.release_root, spec.repo_dir)
    return manifest_path


def _current_release_spec(spec: BackendReleaseSpec) -> BackendReleaseSpec | None:
    current = spec.release_root / "current" / "backend"
    if not current.is_symlink():
        if current.exists():
            raise RuntimeError(f"release pointer is not a symlink: {current}")
        return None
    return _spec_from_manifest(
        current.resolve(),
        environment=spec.environment,
        env_file=spec.env_file,
        release_root=spec.release_root,
        public_url=spec.public_url,
        ca_file=spec.ca_file,
    )


def _spec_from_manifest(
    repo_dir: Path,
    *,
    environment: str,
    env_file: Path,
    release_root: Path,
    public_url: str,
    ca_file: Path,
) -> BackendReleaseSpec:
    manifest = _read_manifest(repo_dir / "release-manifest.json")
    expected_environment = validate_environment(environment)
    if manifest.get("environment") != expected_environment:
        raise ValueError("release manifest environment does not match requested environment")
    return BackendReleaseSpec(
        image_ref=validate_image_ref(str(manifest["image_ref"])),
        commit_sha=validate_commit_sha(str(manifest["commit"])),
        repo_dir=repo_dir.resolve(),
        env_file=env_file.resolve(),
        release_root=release_root.resolve(),
        public_url=public_url,
        ca_file=ca_file.resolve(),
        environment=validate_environment(environment),
    )


def _restore_backend(
    *,
    previous: BackendReleaseSpec | None,
    failed: BackendReleaseSpec,
    runner: CommandRunner,
) -> None:
    _stop_backend(failed, runner)
    if previous is None:
        print("First Product Backend deploy failed; failed services were stopped.", file=sys.stderr)
        return
    _start_backend(previous, runner)
    print(
        f"Restored Product Backend {previous.commit_sha} after failed deploy.",
        file=sys.stderr,
    )


def _start_backend(spec: BackendReleaseSpec, runner: CommandRunner) -> None:
    _validate_spec_files(spec)
    _stop_backend(spec, runner)
    env = _command_env(spec)
    runner.run(
        [
            *_compose_base(spec),
            "up",
            "--detach",
            "--no-build",
            "--no-deps",
            "--force-recreate",
            *_runtime_services(spec),
        ],
        cwd=spec.repo_dir,
        env=env,
    )
    runner.run(_local_readiness_command(spec))
    runner.run(_public_readiness_command(spec.public_url, spec.ca_file))


def _local_readiness_command(spec: BackendReleaseSpec) -> list[str]:
    values = _read_env_values(spec.env_file) if spec.env_file.is_file() else {}
    host, port = _loopback_bind(
        values.get("MOMCOZY_BACKEND_API_BIND", "127.0.0.1:8001")
    )
    return [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--retry",
        "20",
        "--retry-all-errors",
        "--retry-delay",
        "3",
        f"http://{host}:{port}/v1/health/ready",
    ]


def _public_readiness_command(public_url: str, ca_file: Path) -> list[str]:
    return [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--cacert",
        str(ca_file),
        f"{public_url.rstrip('/')}/v1/health/ready",
    ]


def _promote_release_pointer(release_root: Path, repo_dir: Path) -> None:
    current = release_root / "current" / "backend"
    previous = release_root / "previous" / "backend"
    if current.is_symlink():
        current_target = current.resolve()
        if current_target == repo_dir.resolve():
            return
        _replace_symlink(previous, current_target)
    elif current.exists():
        raise RuntimeError(f"release pointer is not a symlink: {current}")
    _replace_symlink(current, repo_dir)


def _replace_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.tmp-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, link)


def _resolved_release_link(link: Path) -> Path:
    if not link.is_symlink():
        raise FileNotFoundError(f"missing release symlink: {link}")
    target = link.resolve()
    if not target.is_dir():
        raise FileNotFoundError(target)
    return target


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = cast(dict[str, Any], json.loads(path.read_text()))
    if payload.get("service") != SERVICE_NAME:
        raise ValueError("release manifest service does not match Product Backend")
    validate_commit_sha(str(payload.get("commit", "")))
    validate_image_ref(str(payload.get("image_ref", "")))
    return payload


def _verify_staged_snapshot(release_dir: Path, archive_sha256: str) -> None:
    archive_marker = release_dir / SOURCE_ARCHIVE_MARKER
    tree_marker = release_dir / SOURCE_TREE_MARKER
    if not release_dir.is_dir() or release_dir.is_symlink():
        raise RuntimeError(f"release path is not a regular directory: {release_dir}")
    if not archive_marker.is_file() or archive_marker.is_symlink():
        raise RuntimeError(f"existing release is missing its checksum marker: {release_dir}")
    if archive_marker.read_text().strip() != archive_sha256:
        raise RuntimeError(f"existing release checksum does not match: {release_dir}")
    if not tree_marker.is_file() or tree_marker.is_symlink():
        raise RuntimeError(f"existing release is missing its tree marker: {release_dir}")
    expected_tree_sha256 = tree_marker.read_text().strip()
    _validate_sha256(expected_tree_sha256, "source tree SHA256")
    if _source_tree_sha256(release_dir) != expected_tree_sha256:
        raise RuntimeError(f"existing release tree checksum does not match: {release_dir}")


def _source_tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if relative in SOURCE_TREE_EXCLUSIONS:
            continue
        if path.is_symlink():
            record = f"link\0{relative}\0{os.readlink(path)}\n"
        elif path.is_dir():
            record = f"directory\0{relative}\n"
        elif path.is_file():
            executable = int(bool(path.stat().st_mode & 0o111))
            record = (
                f"file\0{relative}\0{executable}\0{path.stat().st_size}\0"
                f"{_sha256_file(path)}\n"
            )
        else:
            raise RuntimeError(f"release tree contains unsupported path: {relative}")
        digest.update(record.encode("utf-8"))
    return digest.hexdigest()


def _write_text_exclusive(path: Path, value: str, *, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_sha256(value: str, label: str) -> None:
    if not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must contain 64 lowercase hex characters")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def _add_common_release_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--environment", choices=sorted(DEPLOY_ENVIRONMENTS), required=True)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--repo-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--ca-file", type=Path, required=True)


def _add_current_release_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--environment", choices=sorted(DEPLOY_ENVIRONMENTS), required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--ca-file", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    image_manifest = subparsers.add_parser("image-manifest")
    image_manifest.add_argument("--image-ref", required=True)
    image_manifest.add_argument("--commit-sha", required=True)
    image_manifest.add_argument("--output", type=Path, required=True)

    snapshot = subparsers.add_parser("stage-snapshot")
    snapshot.add_argument("--environment", choices=sorted(DEPLOY_ENVIRONMENTS), required=True)
    snapshot.add_argument("--archive", type=Path, required=True)
    snapshot.add_argument("--archive-sha256", required=True)
    snapshot.add_argument("--commit-sha", required=True)
    snapshot.add_argument("--release-root", type=Path, required=True)
    snapshot.add_argument("--attempt-id", required=True)

    _add_common_release_arguments(subparsers.add_parser("bootstrap"))
    _add_common_release_arguments(subparsers.add_parser("deploy"))
    _add_current_release_arguments(subparsers.add_parser("restart-current"))
    rollback_parser = subparsers.add_parser("rollback")
    _add_current_release_arguments(rollback_parser)
    rollback_parser.add_argument("--confirm-schema-compatible", action="store_true")
    return parser


def _release_spec_from_args(args: argparse.Namespace) -> BackendReleaseSpec:
    return BackendReleaseSpec(
        image_ref=validate_image_ref(args.image_ref),
        commit_sha=validate_commit_sha(args.commit_sha),
        repo_dir=args.repo_dir.resolve(),
        env_file=args.env_file.resolve(),
        release_root=validate_release_root(args.release_root, args.environment),
        public_url=args.public_url,
        ca_file=args.ca_file.resolve(),
        environment=validate_environment(args.environment),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "image-manifest":
            _write_json_atomic(
                args.output,
                build_image_manifest(
                    image_ref=args.image_ref,
                    commit_sha=args.commit_sha,
                ),
            )
            return 0
        if args.command == "stage-snapshot":
            stage_release_snapshot(
                archive=args.archive,
                archive_sha256=args.archive_sha256,
                commit_sha=args.commit_sha,
                release_root=args.release_root,
                attempt_id=args.attempt_id,
                environment=args.environment,
            )
            return 0
        runner = CommandRunner()
        if args.command == "bootstrap":
            bootstrap(_release_spec_from_args(args), runner)
            return 0
        if args.command == "deploy":
            deploy(_release_spec_from_args(args), runner)
            return 0
        if args.command == "restart-current":
            restart_current(
                environment=args.environment,
                env_file=args.env_file,
                release_root=args.release_root,
                public_url=args.public_url,
                ca_file=args.ca_file,
                runner=runner,
            )
            return 0
        rollback(
            environment=args.environment,
            env_file=args.env_file,
            release_root=args.release_root,
            public_url=args.public_url,
            ca_file=args.ca_file,
            confirm_schema_compatible=args.confirm_schema_compatible,
            runner=runner,
        )
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
