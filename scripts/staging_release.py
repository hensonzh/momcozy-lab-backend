#!/usr/bin/env python3
"""Deploy or roll back one immutable Product Backend staging release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Sequence, cast


SERVICE_NAME = "product-backend"
IMAGE_REPOSITORY = "ghcr.io/hensonzh/momcozy-lab-backend"
COMPOSE_PROJECT = "momcozy-lab-backend-staging"
STAGING_NETWORK = "momcozy-lab-staging"
EXPECTED_RELEASE_ROOT = Path("/opt/momcozy-lab")
OPENAPI_PATH = Path("docs/openapi.generated.json")
COMPOSE_PATH = Path("docker-compose.staging.yml")
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_REF_PATTERN = re.compile(
    rf"^{re.escape(IMAGE_REPOSITORY)}@sha256:[0-9a-f]{{64}}$"
)
SAFE_REVISION_PATTERN = re.compile(r"^[0-9A-Za-z_.-]+$")


@dataclass(frozen=True)
class BackendReleaseSpec:
    image_ref: str
    commit_sha: str
    repo_dir: Path
    env_file: Path
    release_root: Path
    public_url: str
    ca_file: Path


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
        rendered = " ".join(command)
        print(f"$ {rendered}", flush=True)
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            text=stdout is None,
            capture_output=capture_output,
            stdout=stdout,
            check=check,
        )


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


def validate_release_root(value: Path) -> Path:
    normalized = value.expanduser().resolve()
    if normalized != EXPECTED_RELEASE_ROOT:
        raise ValueError(f"release root must be {EXPECTED_RELEASE_ROOT}")
    return normalized


def build_release_manifest(
    *,
    image_ref: str,
    commit_sha: str,
    migration_revision: str,
    openapi_sha256: str,
    public_url: str,
    released_at: str,
) -> dict[str, Any]:
    validate_image_ref(image_ref)
    validate_commit_sha(commit_sha)
    _validate_sha256(openapi_sha256, "OpenAPI SHA256")
    if not SAFE_REVISION_PATTERN.fullmatch(migration_revision):
        raise ValueError("migration revision contains unsafe characters")
    return {
        "schema_version": 1,
        "service": SERVICE_NAME,
        "environment": "staging",
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


def build_deploy_commands(spec: BackendReleaseSpec) -> list[list[str]]:
    compose = _compose_base(spec)
    return [
        ["docker", "pull", spec.image_ref],
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
            spec.image_ref,
        ],
        [
            "docker",
            "run",
            "--rm",
            "--env-file",
            str(spec.env_file),
            spec.image_ref,
            "python",
            "-c",
            (
                "from app.core.settings import Settings; "
                "Settings.from_env().validate_for_startup()"
            ),
        ],
        [*compose, "config", "--quiet"],
        [
            *compose,
            "up",
            "--detach",
            "--no-build",
            "postgres",
            "redis",
            "minio",
            "minio-init",
        ],
        [
            *compose,
            "exec",
            "-T",
            "postgres",
            "pg_dump",
            "--username",
            "momcozy_staging_admin",
            "--dbname",
            "momcozy_staging",
            "--format",
            "custom",
        ],
        [*compose, "--profile", "tools", "run", "--rm", "--no-deps", "migrate"],
        [
            *compose,
            "up",
            "--detach",
            "--no-build",
            "--force-recreate",
            "api",
        ],
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "20",
            "--retry-all-errors",
            "--retry-delay",
            "3",
            "http://127.0.0.1:8001/v1/health/ready",
        ],
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--cacert",
            str(spec.ca_file),
            f"{spec.public_url.rstrip('/')}/v1/health/ready",
        ],
    ]


def deploy(spec: BackendReleaseSpec, runner: CommandRunner) -> Path:
    _validate_spec_files(spec)
    _validate_env_file(spec.env_file)
    _check_collision_boundaries(runner)
    commands = build_deploy_commands(spec)
    command_env = _command_env(spec)
    backup_path = _backup_path(spec)
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    revision_result: subprocess.CompletedProcess[str] | None = None
    for index, command in enumerate(commands):
        if index == 1:
            revision_result = runner.run(
                command,
                cwd=spec.repo_dir,
                env=command_env,
                capture_output=True,
            )
            observed_revision = (revision_result.stdout or "").strip()
            if observed_revision != spec.commit_sha:
                raise RuntimeError(
                    "image revision label does not match the requested commit"
                )
        elif "pg_dump" in command:
            with backup_path.open("wb") as backup_file:
                runner.run(
                    command,
                    cwd=spec.repo_dir,
                    env=command_env,
                    stdout=backup_file,
                )
        else:
            runner.run(command, cwd=spec.repo_dir, env=command_env)

    migration_revision = _read_migration_revision(spec, runner, command_env)
    openapi_sha256 = _sha256_file(spec.repo_dir / OPENAPI_PATH)
    released_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    manifest = build_release_manifest(
        image_ref=spec.image_ref,
        commit_sha=spec.commit_sha,
        migration_revision=migration_revision,
        openapi_sha256=openapi_sha256,
        public_url=spec.public_url,
        released_at=released_at,
    )
    manifest_path = spec.repo_dir / "release-manifest.json"
    _write_json_atomic(manifest_path, manifest)
    archived_manifest = (
        spec.release_root / "manifests" / f"backend-{spec.commit_sha}.json"
    )
    _write_json_atomic(archived_manifest, manifest)
    _promote_release_pointer(spec.release_root, spec.repo_dir)
    print(f"Product Backend staging release promoted: {manifest_path}")
    return manifest_path


def rollback(
    *,
    env_file: Path,
    release_root: Path,
    public_url: str,
    ca_file: Path,
    confirm_schema_compatible: bool,
    runner: CommandRunner,
) -> Path:
    root = validate_release_root(release_root)
    if not confirm_schema_compatible:
        raise ValueError("rollback requires --confirm-schema-compatible")
    current_link = root / "current" / "backend"
    previous_link = root / "previous" / "backend"
    current_dir = _resolved_release_link(current_link)
    previous_dir = _resolved_release_link(previous_link)
    manifest_path = previous_dir / "release-manifest.json"
    manifest = _read_manifest(manifest_path)
    spec = BackendReleaseSpec(
        image_ref=validate_image_ref(str(manifest["image_ref"])),
        commit_sha=validate_commit_sha(str(manifest["commit"])),
        repo_dir=previous_dir,
        env_file=env_file.resolve(),
        release_root=root,
        public_url=public_url,
        ca_file=ca_file.resolve(),
    )
    _validate_spec_files(spec)
    _validate_env_file(spec.env_file)
    _check_collision_boundaries(runner)
    command_env = _command_env(spec)
    compose = _compose_base(spec)
    runner.run(["docker", "pull", spec.image_ref], env=command_env)
    revision = runner.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
            spec.image_ref,
        ],
        capture_output=True,
        env=command_env,
    )
    if (revision.stdout or "").strip() != spec.commit_sha:
        raise RuntimeError("rollback image revision label does not match manifest")
    runner.run(
        [*compose, "up", "--detach", "--no-build", "--force-recreate", "api"],
        cwd=spec.repo_dir,
        env=command_env,
    )
    runner.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "20",
            "--retry-all-errors",
            "--retry-delay",
            "3",
            "http://127.0.0.1:8001/v1/health/ready",
        ]
    )
    runner.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--cacert",
            str(spec.ca_file),
            f"{public_url.rstrip('/')}/v1/health/ready",
        ]
    )
    _replace_symlink(previous_link, current_dir)
    _replace_symlink(current_link, previous_dir)
    print(f"Product Backend staging rolled back to {spec.commit_sha}")
    return manifest_path


def _compose_base(spec: BackendReleaseSpec) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(spec.env_file),
        "-f",
        str(spec.repo_dir / COMPOSE_PATH),
    ]


def _command_env(spec: BackendReleaseSpec) -> dict[str, str]:
    return {
        **os.environ,
        "MOMCOZY_BACKEND_IMAGE": spec.image_ref,
        "MOMCOZY_BACKEND_ENV_FILE": str(spec.env_file),
    }


def _validate_spec_files(spec: BackendReleaseSpec) -> None:
    validate_commit_sha(spec.commit_sha)
    validate_image_ref(spec.image_ref)
    root = validate_release_root(spec.release_root)
    expected_repo_dir = root / "releases" / "backend" / spec.commit_sha
    if spec.repo_dir.resolve() != expected_repo_dir:
        raise ValueError(f"repo directory must be {expected_repo_dir}")
    if not (spec.repo_dir / COMPOSE_PATH).is_file():
        raise FileNotFoundError(spec.repo_dir / COMPOSE_PATH)
    if not (spec.repo_dir / OPENAPI_PATH).is_file():
        raise FileNotFoundError(spec.repo_dir / OPENAPI_PATH)
    if not spec.env_file.is_file():
        raise FileNotFoundError(spec.env_file)
    if not spec.ca_file.is_file():
        raise FileNotFoundError(spec.ca_file)
    if not spec.public_url.startswith("https://"):
        raise ValueError("public URL must use HTTPS")


def _validate_env_file(path: Path) -> None:
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError(f"staging env must not be group/world readable: {mode:o}")
    values = _read_env_values(path)
    required = (
        "MOMCOZY_STAGING_POSTGRES_ADMIN_PASSWORD",
        "MOMCOZY_STAGING_PRODUCT_POSTGRES_PASSWORD",
        "MOMCOZY_STAGING_AGENT_POSTGRES_PASSWORD",
        "MOMCOZY_STAGING_REDIS_PASSWORD",
        "MOMCOZY_STAGING_MINIO_ROOT_USER",
        "MOMCOZY_STAGING_MINIO_ROOT_PASSWORD",
        "AUTH_JWT_PRIVATE_KEY_B64",
        "AGENT_RUNTIME_SERVICE_API_KEY",
    )
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise ValueError(f"staging env is missing required values: {', '.join(missing)}")


def _read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def _check_collision_boundaries(runner: CommandRunner) -> None:
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
        if "127.0.0.1:8001->" in ports and (
            f"com.docker.compose.project={COMPOSE_PROJECT}" not in labels
        ):
            raise RuntimeError("127.0.0.1:8001 is owned by another container")
        if "127.0.0.1:8001->" in ports:
            port_owned_by_compose = True

    listeners = runner.run(
        ["ss", "-H", "-ltn", "sport = :8001"],
        capture_output=True,
        check=False,
    )
    if listeners.returncode != 0:
        raise RuntimeError("could not inspect host listener 127.0.0.1:8001")
    if (listeners.stdout or "").strip() and not port_owned_by_compose:
        raise RuntimeError("127.0.0.1:8001 is owned by a non-Docker process")

    network = runner.run(
        [
            "docker",
            "network",
            "inspect",
            STAGING_NETWORK,
            "--format",
            '{{ index .Labels "com.docker.compose.project" }}',
        ],
        capture_output=True,
        check=False,
    )
    if network.returncode == 0:
        owner = (network.stdout or "").strip()
        if owner != COMPOSE_PROJECT:
            raise RuntimeError(f"{STAGING_NETWORK} is owned by {owner}")


def _backup_path(spec: BackendReleaseSpec) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (
        spec.release_root
        / "backups"
        / "backend"
        / f"{timestamp}-{spec.commit_sha}.dump"
    )


def _read_migration_revision(
    spec: BackendReleaseSpec,
    runner: CommandRunner,
    env: dict[str, str],
) -> str:
    result = runner.run(
        [
            *_compose_base(spec),
            "exec",
            "-T",
            "postgres",
            "psql",
            "--username",
            "momcozy_staging_admin",
            "--dbname",
            "momcozy_staging",
            "--tuples-only",
            "--no-align",
            "--command",
            "SELECT version_num FROM alembic_version",
        ],
        cwd=spec.repo_dir,
        env=env,
        capture_output=True,
    )
    revision = (result.stdout or "").strip()
    if not SAFE_REVISION_PATTERN.fullmatch(revision):
        raise RuntimeError("could not read a safe Product Backend migration revision")
    return revision


def _promote_release_pointer(release_root: Path, repo_dir: Path) -> None:
    current = release_root / "current" / "backend"
    previous = release_root / "previous" / "backend"
    if current.is_symlink():
        _replace_symlink(previous, current.resolve())
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
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_sha256(value: str, label: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    image_manifest = subparsers.add_parser("image-manifest")
    image_manifest.add_argument("--image-ref", required=True)
    image_manifest.add_argument("--commit-sha", required=True)
    image_manifest.add_argument("--output", type=Path, required=True)

    deploy_parser = subparsers.add_parser("deploy")
    deploy_parser.add_argument("--image-ref", required=True)
    deploy_parser.add_argument("--commit-sha", required=True)
    deploy_parser.add_argument("--repo-dir", type=Path, required=True)
    deploy_parser.add_argument("--env-file", type=Path, required=True)
    deploy_parser.add_argument(
        "--release-root", type=Path, default=EXPECTED_RELEASE_ROOT
    )
    deploy_parser.add_argument("--public-url", required=True)
    deploy_parser.add_argument("--ca-file", type=Path, required=True)

    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--env-file", type=Path, required=True)
    rollback_parser.add_argument(
        "--release-root", type=Path, default=EXPECTED_RELEASE_ROOT
    )
    rollback_parser.add_argument("--public-url", required=True)
    rollback_parser.add_argument("--ca-file", type=Path, required=True)
    rollback_parser.add_argument(
        "--confirm-schema-compatible", action="store_true"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "image-manifest":
            manifest = build_image_manifest(
                image_ref=args.image_ref,
                commit_sha=args.commit_sha,
            )
            _write_json_atomic(args.output, manifest)
            return 0
        runner = CommandRunner()
        if args.command == "deploy":
            spec = BackendReleaseSpec(
                image_ref=validate_image_ref(args.image_ref),
                commit_sha=validate_commit_sha(args.commit_sha),
                repo_dir=args.repo_dir.resolve(),
                env_file=args.env_file.resolve(),
                release_root=validate_release_root(args.release_root),
                public_url=args.public_url,
                ca_file=args.ca_file.resolve(),
            )
            deploy(spec, runner)
            return 0
        rollback(
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
