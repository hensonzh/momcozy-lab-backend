#!/usr/bin/env python3
"""B-only first-release sequence. No mutation without --apply; never auto-roll back.

Uses the existing fail-closed stages and stops at the first failed gate. This
is not an update/rollback runner. Recovery of a partially applied first release
requires an explicit inspection; never rerun this script with --force.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import json
import socket
import ssl
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.b_docker_context import require_local_docker  # noqa: E402
from scripts.b_migrate_first import verify_image  # noqa: E402
from scripts.b_offhost_backup import select_latest  # noqa: E402
from scripts.b_postgres_recovery import ROOT, POSTGRES_IMAGE, _validate_backup_mount  # noqa: E402
from scripts.b_redis_recovery import REDIS_IMAGE  # noqa: E402
from scripts.b_minio_recovery import IMAGE as MINIO_IMAGE, REVISION as MINIO_REVISION  # noqa: E402
from scripts.b_release import preflight as product_preflight  # noqa: E402
from scripts.check_b_fresh_bootstrap import validate_fresh  # noqa: E402

ORIGINS = {"backend": "https://backend-us-dev.lute-momcozylab.luteos.cloud",
           "agent": "https://agent-us-dev.lute-momcozylab.luteos.cloud"}


def _arguments(args: argparse.Namespace) -> None:
    for key in ("backend_source", "agent_source", "backend_env", "agent_env", "backend_target", "agent_target",
                "backend_commit", "agent_commit", "backend_image", "agent_image", "backend_local_image",
                "agent_local_image", "backend_image_id", "agent_image_id"):
        if not getattr(args, key, None):
            raise ValueError("B first release needs both reviewed source/image/env pairs")
    for service in ("backend", "agent"):
        source = getattr(args, service + "_source")
        commit = getattr(args, service + "_commit")
        if source != ROOT / "releases" / service / commit:
            raise ValueError("B source is outside the isolated release root")


def run_stage(name: str, argv: list[str]) -> None:
    if name not in {"bootstrap", "migrate", "postgres-recovery", "redis-recovery", "minio-recovery",
                    "backend-activate", "agent-activate"}:
        raise ValueError("unknown B first-release stage")
    result = subprocess.run([sys.executable, *argv], capture_output=True, check=False)
    if result.returncode:
        # Docker/Compose error output may include the private env. Never log it.
        raise ValueError(f"B stage failed: {name}; state preserved for manual inspection")


def preflight(args: argparse.Namespace) -> None:
    _arguments(args)
    product_preflight(argparse.Namespace(target=args.backend_target, source=args.backend_source,
                                        commit=args.backend_commit, image=args.backend_image,
                                        backend_env=args.backend_env, agent_env=args.agent_env))
    run = subprocess.run([sys.executable, str(args.agent_source / "scripts/b_release.py"),
                          "--target", str(args.agent_target), "--source", str(args.agent_source),
                          "--commit", args.agent_commit, "--image", args.agent_image,
                          "--agent-env", str(args.agent_env)], capture_output=True, check=False)
    if run.returncode:
        raise ValueError("B Agent admission failed")
    for service in ("backend", "agent"):
        verify_image(getattr(args, service + "_local_image"), getattr(args, service + "_commit"),
                     service, getattr(args, service + "_image_id"))
    lock = ROOT / "shared/north-america-staging-release.lock"
    if lock.is_symlink() or not lock.is_file() or stat.S_IMODE(lock.stat().st_mode) != 0o600:
        raise ValueError("B release lock is not private")


def check_fresh(args: argparse.Namespace) -> None:
    validate_fresh()


def verify_stateful_images() -> None:
    for image in (POSTGRES_IMAGE, REDIS_IMAGE, MINIO_IMAGE):
        result = subprocess.run(["docker", "image", "inspect", "--platform", "linux/amd64", image, "--format", "{{json .}}"],
                                capture_output=True, text=True, check=True)
        inspected = json.loads(result.stdout)
        if inspected.get("Architecture") != "amd64" or inspected.get("Os") != "linux":
            raise ValueError("B stateful image architecture differs")
        if image == MINIO_IMAGE:
            labels = (inspected.get("Config") or {}).get("Labels") or {}
            if labels.get("org.opencontainers.image.revision") != MINIO_REVISION:
                raise ValueError("B MinIO local source identity differs")
        elif not any(entry.rsplit("@", 1)[0].split("/")[-1] == image.split(":", 1)[0]
                     and entry.rsplit("@", 1)[1] == image.rsplit("@", 1)[1]
                     for entry in inspected.get("RepoDigests", []) if "@" in entry):
            raise ValueError("B stateful local image is not the approved digest")


def existing_recovery_markers(root: Path) -> set[Path]:
    """Snapshot complete prior evidence so a failed drill cannot reuse it."""
    return {entry for kind in ("postgres", "redis", "minio")
            for entry in (root / kind).glob("*/recovery-verified.json")}


def check_ingress_tls(service: str) -> None:
    """Require a trusted B public certificate before changing target state."""
    host = ORIGINS[service].removeprefix("https://")
    with socket.create_connection((host, 443), timeout=4) as connection:
        with ssl.create_default_context().wrap_socket(connection, server_hostname=host) as secured:
            if secured.version() is None or not secured.getpeercert():
                raise ValueError("B public TLS is not trustworthy")


def check_public(service: str) -> None:
    """Real trusted HTTPS readiness. Mail/model-provider failures are not App gates."""
    url = ORIGINS[service] + "/v1/health/ready"
    for attempt in range(20):
        try:
            with urllib.request.urlopen(url, timeout=3, context=ssl.create_default_context()) as response:
                if response.status == 200 and json.load(response).get("status") == "ok":
                    return
        except (OSError, ValueError, KeyError):
            pass
        if attempt != 19:
            time.sleep(2)
    raise ValueError(f"B public HTTPS readiness failed: {service}")


@contextmanager
def first_release_lock() -> Iterator[None]:
    """Serialize the entire first release; stage-specific locks remain independent."""
    shared = ROOT / "shared"
    if (shared.is_symlink() or not shared.is_dir()
            or stat.S_IMODE(shared.stat().st_mode) != 0o700
            or shared.stat().st_uid != os.geteuid()):
        raise ValueError("B first-release lock directory is not private")
    path = shared / "north-america-staging-first-release.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.geteuid() or metadata.st_nlink != 1):
            raise ValueError("B first-release lock is not private")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("B first-release lock already held") from exc
        yield
    finally:
        os.close(fd)


def read_only_preflight(args: argparse.Namespace) -> Path:
    """Check B source, private topology, empty state, TLS, images and backup mount."""
    preflight(args)
    check_fresh(args)
    for service in ("backend", "agent"):
        check_ingress_tls(service)
    verify_stateful_images()
    return _validate_backup_mount(ROOT)


def run_first_release(args: argparse.Namespace) -> None:
    backup_root = read_only_preflight(args)
    prior_markers = existing_recovery_markers(backup_root)
    backend = args.backend_source / "scripts"
    product = ["--source", str(args.backend_source), "--commit", args.backend_commit,
               "--image", args.backend_image, "--target", str(args.backend_target),
               "--backend-env", str(args.backend_env), "--agent-env", str(args.agent_env)]
    pair = ["--backend-source", str(args.backend_source), "--agent-source", str(args.agent_source),
            "--backend-env", str(args.backend_env), "--agent-env", str(args.agent_env),
            "--backend-target", str(args.backend_target), "--agent-target", str(args.agent_target),
            "--backend-commit", args.backend_commit, "--agent-commit", args.agent_commit,
            "--backend-image", args.backend_image, "--agent-image", args.agent_image,
            "--backend-local-image", args.backend_local_image, "--agent-local-image", args.agent_local_image,
            "--backend-image-id", args.backend_image_id, "--agent-image-id", args.agent_image_id]
    def activation(service: str, source: Path) -> list[str]:
        options = ["--service", service, "--source", str(source),
                   "--env-file", str(getattr(args, service + "_env")),
                   "--target", str(getattr(args, service + "_target")),
                   "--commit", getattr(args, service + "_commit"),
                   "--image", getattr(args, service + "_image"),
                   "--local-image", getattr(args, service + "_local_image"),
                   "--image-id", getattr(args, service + "_image_id")]
        if service == "backend":
            options += ["--agent-env", str(args.agent_env)]
        return options
    run_stage("bootstrap", [str(backend / "b_bootstrap_infra.py"), "--apply", *product])
    run_stage("migrate", [str(backend / "b_migrate_first.py"), "--apply", *pair])
    for name in ("postgres", "redis", "minio"):
        run_stage(name + "-recovery", [str(backend / f"b_{name}_recovery.py"), "--apply"])
    evidence = select_latest(backup_root)
    # The recovery runs above must be the runs promoted for this first release;
    # never accept an older valid manifest if a new drill failed or disappeared.
    for kind in ("postgres", "redis", "minio"):
        if kind not in evidence or evidence[kind] / "recovery-verified.json" in prior_markers:
            raise ValueError("B recovery evidence is incomplete or not fresh")
    # The activation stage itself checks current DB revision, image identity,
    # on-host evidence, loopback readiness, running service provenance and lock.
    run_stage("backend-activate", [str(backend / "b_activate_first.py"), "--apply",
                                   *activation("backend", args.backend_source)])
    check_public("backend")
    run_stage("agent-activate", [str(backend / "b_activate_first.py"), "--apply",
                                 *activation("agent", args.agent_source)])
    check_public("agent")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--preflight", action="store_true", help="Read-only B target admission; no services started")
    for name in ("backend-source", "agent-source", "backend-env", "agent-env", "backend-target", "agent-target"):
        parser.add_argument("--" + name, type=Path)
    for name in ("backend-commit", "agent-commit", "backend-image", "agent-image", "backend-local-image",
                 "agent-local-image", "backend-image-id", "agent-image-id"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    if not args.apply and not args.preflight:
        print("B first release: no action; use --preflight or --apply with both complete release pairs.")
        return 0
    try:
        require_local_docker()
        if args.preflight:
            read_only_preflight(args)
            print("B first-release admission passed; no service or data changed. Live recovery and public readiness remain unverified.")
            return 0
        with first_release_lock():
            run_first_release(args)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print("FAIL B first release: inspect stage and B-only state; never auto-rollback or rerun blindly", file=sys.stderr)
        return 1
    print("B first release: Product and Agent passed local and public HTTPS readiness; provider flows not verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
