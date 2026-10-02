#!/usr/bin/env python3
"""B-only same-schema update/rollback of an already running single-host release.

No database migration or volume recreation is permitted. This is deliberately
separate from the empty-host first-release runner. --apply is required to mutate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.b_activate_first import (  # noqa: E402
    check_public_ready, check_ready, check_running_service_provenance, start_services,
)
from scripts.b_docker_context import require_local_docker  # noqa: E402
from scripts.b_first_release import (  # noqa: E402
    check_ingress_tls, existing_recovery_markers, first_release_lock, run_stage,
)
from scripts.b_migrate_first import verify_image  # noqa: E402
from scripts.b_offhost_backup import select_latest  # noqa: E402
from scripts.b_postgres_recovery import _validate_backup_mount  # noqa: E402
from scripts.b_release import _private_json, preflight as backend_preflight  # noqa: E402
from scripts.check_release_target import validate_target as validate_backend_target  # noqa: E402
from scripts.check_b_env import validate as validate_backend_env  # noqa: E402
from scripts.check_b_pair import validate_pair  # noqa: E402
from scripts.check_b_rollback import _manifest, read_live_revision, validate_rollback_pair  # noqa: E402

ROOT = Path("/opt/momcozy-lab-us-east-uat")
SERVICES = {"backend", "agent"}
REVISION = re.compile(r"[A-Za-z0-9_.-]+")


def _local_tag(service: str, commit: str) -> str:
    return f"ghcr.io/hensonzh/momcozy-lab-{service}:b-oci-{commit[:7]}"


def _snapshot(source: Path, commit: str) -> None:
    """An activated worktree may contain only its generated release manifest."""
    if source.is_symlink() or source != ROOT / "releases" / source.parent.name / commit:
        raise ValueError("B release source is outside the isolated root")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=source,
                           capture_output=True, text=True, check=True).stdout.splitlines()
    if head != commit or branch != "dev" or dirty != ["?? release-manifest.json"]:
        raise ValueError("B activated source differs from clean dev plus its generated manifest")


def _slot(service: str, slot: str) -> tuple[Path, dict]:
    path = ROOT / slot / service / "release-manifest.json"
    data = _manifest(path, ROOT, service, slot)
    source = (ROOT / slot / service).resolve(strict=True)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", data.get("local_image_id", "")):
        raise ValueError("B release manifest has no local image identity")
    return source, data


def _runtime(args: argparse.Namespace, source: Path, data: dict) -> argparse.Namespace:
    return SimpleNamespace(service=args.service, source=source, env_file=args.env_file,
                           commit=data["commit"], image=data["image_ref"],
                           local_image=_local_tag(args.service, data["commit"]),
                           image_id=data["local_image_id"])


def admit_target(args: argparse.Namespace) -> None:
    if ROOT.is_symlink() or args.target != ROOT / f"shared/{args.service}/north-america-staging.json" or args.env_file != ROOT / f"shared/{args.service}/north-america-staging.env" or args.agent_env != ROOT / "shared/agent/north-america-staging.env":
        raise ValueError("B update target or env is outside the isolated root")
    owner = ROOT.stat().st_uid
    for folder in (ROOT, ROOT / "shared", ROOT / "current", ROOT / "previous", ROOT / "releases",
                   ROOT / "releases/backend", ROOT / "releases/agent"):
        if (folder.is_symlink() or not folder.is_dir() or folder.stat().st_uid != owner
                or stat.S_IMODE(folder.stat().st_mode) != 0o700):
            raise ValueError("B update release layout is not a private directory")
    target = _private_json(args.target)
    if args.service == "backend":
        validate_backend_target(target)
    if (target.get("deployment_target") != "north-america-staging" or target.get("release_root") != str(ROOT)
            or target.get("service_env_file") != str(args.env_file)
            or target.get("release_lock") != str(ROOT / "shared/north-america-staging-release.lock")):
        raise ValueError("B update target identity differs")
    backend_env = ROOT / "shared/backend/north-america-staging.env"
    validate_backend_env(backend_env)
    validate_pair(backend_env, args.agent_env)
    # Agent's own env validator is kept in its separately versioned repository.
    agent_source, _ = _slot("agent", "current")
    result = subprocess.run([sys.executable, str(agent_source / "scripts/check_b_env.py"),
                             "--env-file", str(args.agent_env)], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("B Agent private env admission failed")
    if args.service == "agent":
        result = subprocess.run([sys.executable, str(agent_source / "scripts/check_release_target.py"),
                                 "--config", str(args.target)], capture_output=True, check=False)
        if result.returncode:
            raise ValueError("B Agent target admission failed")
    for service in SERVICES:
        check_ingress_tls(service)
    _validate_backup_mount(ROOT)


def verify_running(args: argparse.Namespace, manifest: dict, source: Path) -> None:
    _snapshot(source, manifest["commit"])
    runtime = _runtime(args, source, manifest)
    verify_image(runtime.local_image, runtime.commit, args.service, runtime.image_id)
    check_running_service_provenance(runtime)
    check_ready(args.service)
    check_public_ready(args.service)


def admit_new(args: argparse.Namespace) -> None:
    if args.source != ROOT / "releases" / args.service / args.commit or (args.source / "release-manifest.json").exists():
        raise ValueError("B update needs a new, clean commit release source")
    if args.service == "backend":
        backend_preflight(SimpleNamespace(target=args.target, source=args.source, commit=args.commit,
                                          image=args.image, backend_env=args.env_file, agent_env=args.agent_env))
    else:
        result = subprocess.run([sys.executable, str(args.source / "scripts/b_release.py"),
                                 "--target", str(args.target), "--source", str(args.source),
                                 "--commit", args.commit, "--image", args.image,
                                 "--agent-env", str(args.env_file)], capture_output=True, check=False)
        if result.returncode:
            raise ValueError("B Agent new source admission failed")
    verify_image(args.local_image, args.commit, args.service, args.image_id)


def image_head(tag: str) -> str:
    result = subprocess.run(["docker", "run", "--rm", "--network", "none", "--pull", "never",
                             "--entrypoint", "python", tag, "-m", "alembic", "-c", "alembic.ini", "heads"],
                            capture_output=True, text=True, check=True)
    lines = result.stdout.strip().splitlines()
    if len(lines) != 1 or not (match := re.fullmatch(r"([A-Za-z0-9_.-]+) \(head\)", lines[0])):
        raise ValueError("B candidate image has no single Alembic head")
    return match[1]


def prepare(args: argparse.Namespace) -> tuple[argparse.Namespace, argparse.Namespace, str]:
    admit_target(args)
    old_source, old_manifest = _slot(args.service, "current")
    verify_running(args, old_manifest, old_source)
    revision = read_live_revision(args.service)
    if revision != old_manifest["migration_revision"]:
        raise ValueError("B live schema differs from current release")
    old = _runtime(args, old_source, old_manifest)
    if args.operation == "update" and (ROOT / "previous" / args.service).is_symlink():
        _slot(args.service, "previous")  # Never overwrite an invalid recovery pointer.
    if args.operation == "rollback":
        if not args.confirm_client_compatible:
            raise ValueError("B rollback requires explicit client compatibility confirmation")
        new_source, new_manifest = _slot(args.service, "previous")
        validate_rollback_pair(ROOT, ROOT / "current" / args.service / "release-manifest.json",
                               ROOT / "previous" / args.service / "release-manifest.json",
                               args.service, True, database_revision=revision)
        _snapshot(new_source, new_manifest["commit"])
        desired = _runtime(args, new_source, new_manifest)
        verify_image(desired.local_image, desired.commit, args.service, desired.image_id)
        if image_head(desired.local_image) != revision:
            raise ValueError("B rollback image schema differs from the live database")
    else:
        admit_new(args)
        desired = SimpleNamespace(service=args.service, source=args.source, env_file=args.env_file,
                                  commit=args.commit, image=args.image, local_image=args.local_image,
                                  image_id=args.image_id)
        if image_head(args.local_image) != revision:
            raise ValueError("B update requires the same schema; migration needs a separately reviewed plan")
    return old, desired, revision


def fresh_recovery(source: Path) -> None:
    root = _validate_backup_mount(ROOT)
    before = existing_recovery_markers(root)
    for kind in ("postgres", "redis", "minio"):
        run_stage(kind + "-recovery", [str(source / "scripts" / f"b_{kind}_recovery.py"), "--apply"])
    after = select_latest(root)
    if any(kind not in after or after[kind] / "recovery-verified.json" in before
           for kind in ("postgres", "redis", "minio")):
        raise ValueError("B update recovery evidence is not fresh")


def wait_api_healthy(runtime: argparse.Namespace) -> None:
    """HTTP readiness may precede Docker's healthcheck start period."""
    project = f"momcozy-lab-{runtime.service}-us-east-uat"
    deadline = time.monotonic() + 90
    while True:
        ids = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
             "--filter", "label=com.docker.compose.service=api", "--format", "{{.ID}}"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        if len(ids) != 1:
            raise ValueError("B API healthcheck requires exactly one container")
        container = json.loads(subprocess.run(
            ["docker", "inspect", ids[0], "--format", "{{json .State}}"],
            capture_output=True, text=True, check=True,
        ).stdout)
        if container.get("Status") != "running":
            raise ValueError("B API container stopped before healthcheck")
        health = container.get("Health", {}).get("Status")
        if health == "healthy":
            return
        if health != "starting":
            raise ValueError("B API healthcheck failed")
        if time.monotonic() >= deadline:
            raise ValueError("B API healthcheck did not become healthy")
        time.sleep(2)


def start_checked(runtime: argparse.Namespace) -> None:
    start_services(runtime)
    check_ready(runtime.service)
    wait_api_healthy(runtime)
    check_running_service_provenance(runtime)
    check_public_ready(runtime.service)


def _replace_link(slot: str, service: str, target: Path) -> None:
    link = ROOT / slot / service
    temporary = link.with_name(f".{service}-{os.getpid()}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("B release pointer temporary path already exists")
    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def switch_links(service: str, old: Path, desired: Path) -> None:
    previous = ROOT / "previous" / service
    former_previous = previous.resolve(strict=True) if previous.is_symlink() else None
    if previous.exists() and not previous.is_symlink():
        raise ValueError("B previous pointer is not a symlink")
    _replace_link("previous", service, old)
    try:
        _replace_link("current", service, desired)
    except Exception:
        if former_previous is None:
            previous.unlink()
        else:
            _replace_link("previous", service, former_previous)
        raise


def perform(args: argparse.Namespace) -> None:
    old, desired, revision = prepare(args)
    fresh_recovery(old.source)
    current_source, current_manifest = _slot(args.service, "current")
    if current_source != old.source or current_manifest["local_image_id"] != old.image_id:
        raise ValueError("B current release changed during backup")
    if read_live_revision(args.service) != revision:
        raise ValueError("B schema changed during backup; service switch refused")
    verify_running(args, current_manifest, current_source)
    try:
        start_checked(desired)
        if args.operation == "update":
            payload = {"deployment_target": "north-america-staging", "service": args.service,
                       "commit": args.commit, "image_ref": args.image,
                       "migration_revision": revision, "local_image_id": args.image_id}
            manifest = args.source / "release-manifest.json"
            descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(payload, stream, sort_keys=True)
        switch_links(args.service, old.source, desired.source)
    except Exception as error:
        try:
            start_checked(old)
        except Exception as restore_error:
            raise ValueError("B switch failed and prior service could not be restored; inspect before retry") from restore_error
        raise ValueError("B switch failed; prior service restored, pointers left unchanged") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", required=True, choices=("update", "rollback"))
    parser.add_argument("--service", required=True, choices=sorted(SERVICES))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-client-compatible", action="store_true")
    for name in ("source", "target", "env-file", "agent-env"):
        parser.add_argument("--" + name, type=Path)
    for name in ("commit", "image", "local-image", "image-id"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    try:
        require_local_docker()
        if args.apply:
            with first_release_lock():
                perform(args)
        else:
            prepare(args)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print("FAIL B existing-release admission/switch; inspect B-only state, no automatic schema change", file=sys.stderr)
        return 1
    print("B existing release switch verified." if args.apply else "B existing-release preflight passed; no state changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
