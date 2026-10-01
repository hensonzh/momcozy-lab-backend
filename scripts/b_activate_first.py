#!/usr/bin/env python3
"""Activate one B service after its verified first migration and recovery.

One service per invocation, Product before Agent. The pointer is created only
once healthy on loopback. No schema downgrade, stateful-volume deletion, A
release entrypoint or implicit rollback is attempted on failure.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.b_migrate_first import verify_image  # noqa: E402
from scripts.b_offhost_backup import select_latest  # noqa: E402
from scripts.b_postgres_recovery import _validate_backup_mount  # noqa: E402
from scripts.b_release import preflight  # noqa: E402
from scripts.check_b_rollback import read_live_revision  # noqa: E402

ROOT = Path("/opt/momcozy-lab-us-east-uat")
PORTS = {"backend": 8001, "agent": 8002}
COMMIT = re.compile(r"[0-9a-f]{40}")


def _run(command: list[str], **kwargs: object) -> str:
    result = subprocess.run(command, capture_output=True, check=False, **kwargs)
    if result.returncode:
        raise ValueError("B activation step failed; private output suppressed")
    return result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout


def check_fresh_pointer(service: str, source: Path) -> None:
    if service not in PORTS or source != ROOT / "releases" / service / source.name or not COMMIT.fullmatch(source.name):
        raise ValueError("B release source is outside the isolated root")
    pointer = ROOT / "current" / service
    previous = ROOT / "previous" / service
    if pointer.exists() or pointer.is_symlink() or previous.exists() or previous.is_symlink():
        raise ValueError("B first release already has a current or previous pointer")
    manifest = source / "release-manifest.json"
    if manifest.exists() or manifest.is_symlink():
        raise ValueError("B first release manifest already exists")
    if service == "agent":
        backend = ROOT / "current/backend"
        if not backend.is_symlink() or backend.resolve().parent != ROOT / "releases/backend":
            raise ValueError("B Agent release requires verified Product release")


def check_service_containers_absent(service: str) -> None:
    project = f"momcozy-lab-{service}-us-east-uat"
    services = ("api", "notification-worker", "auth-email-worker") if service == "backend" else ("api", "worker")
    for name in services:
        result = _run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                       "--filter", f"label=com.docker.compose.service={name}", "--format", "{{.ID}}"])
        if result.strip():
            raise ValueError("B first release already has business containers")


def check_running_service_provenance(args: argparse.Namespace) -> None:
    """Recover only the exact B containers created by this source and image."""
    project = f"momcozy-lab-{args.service}-us-east-uat"
    expected = {"api", "notification-worker", "auth-email-worker"} if args.service == "backend" else {"api", "worker"}
    for service in expected:
        configured_hash = _run(_compose(args, ["config", "--hash", service]),
                               cwd=args.source, env=_environment(args)).strip().split()
        if len(configured_hash) != 2 or configured_hash[0] != service or not re.fullmatch(r"[0-9a-f]{64}", configured_hash[1]):
            raise ValueError("B service configuration hash is unavailable")
        ids = _run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                    "--filter", f"label=com.docker.compose.service={service}", "--format", "{{.ID}}"])
        if len(ids.splitlines()) != 1:
            raise ValueError("B running recovery requires exactly one container per business service")
        container = json.loads(_run(["docker", "inspect", ids.strip(), "--format", "{{json .}}"]))
        labels = container["Config"]["Labels"]
        if (container["State"]["Status"] != "running" or container["Image"] != args.image_id
                or labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.service") != service
                or labels.get("com.docker.compose.project.working_dir") != str(args.source)
                or labels.get("com.docker.compose.project.config_files") != str(args.source / "docker-compose.us-east-uat.yml")
                or labels.get("com.docker.compose.project.environment_file") != str(args.env_file)
                or labels.get("com.docker.compose.config-hash") != configured_hash[1]):
            raise ValueError("B running container provenance differs; refuse first-release promotion")
        if service == "api" and container["State"].get("Health", {}).get("Status") != "healthy":
            raise ValueError("B API container is not healthy")


def _compose(args: argparse.Namespace, command: list[str]) -> list[str]:
    return ["docker", "compose", "--env-file", str(args.env_file), "-f", "docker-compose.us-east-uat.yml", *command]


def _environment(args: argparse.Namespace) -> dict[str, str]:
    result = {**os.environ, f"MOMCOZY_{args.service.upper()}_ENV_FILE": str(args.env_file),
              f"MOMCOZY_{args.service.upper()}_IMAGE": args.local_image}
    if args.service == "agent":
        result["MOMCOZY_AGENT_RELEASE_ID"] = args.commit
    return result


def start_services(args: argparse.Namespace) -> None:
    services = ["api", "notification-worker", "auth-email-worker"] if args.service == "backend" else ["api", "worker"]
    _run(_compose(args, ["up", "-d", "--no-deps", "--pull", "never", *services]),
         cwd=args.source, env=_environment(args))


def check_ready(service: str) -> None:
    port = PORTS[service]
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health/ready", timeout=3) as response:
                data = json.load(response)
                if response.status == 200 and data.get("status") == "ok":
                    break
        except (OSError, ValueError):
            time.sleep(2)
    else:
        raise ValueError("B loopback ready check failed")
    project = f"momcozy-lab-{service}-us-east-uat"
    expected = {"api", "notification-worker", "auth-email-worker"} if service == "backend" else {"api", "worker"}
    for name in expected:
        lines = _run(["docker", "ps", "--filter", f"label=com.docker.compose.project={project}",
                      "--filter", f"label=com.docker.compose.service={name}", "--format", "{{.ID}}"])
        if len(lines.splitlines()) != 1:
            raise ValueError("B required worker or API is not running")
    sockets = _run(["ss", "-ltnH", f"( sport = :{port} )"])
    # ss includes a peer column such as 0.0.0.0:* for a loopback listener.
    # Only the local-address column describes exposure.
    local_addresses = [fields[3] for line in sockets.splitlines()
                       if len(fields := line.split()) >= 5]
    if local_addresses != [f"127.0.0.1:{port}"]:
        raise ValueError("B API loopback port is not isolated")


def activate(args: argparse.Namespace) -> None:
    check_fresh_pointer(args.service, args.source)
    if getattr(args, "recover_running", False):
        check_running_service_provenance(args)
    else:
        check_service_containers_absent(args.service)
    revision = read_live_revision(args.service)
    backups = select_latest(_validate_backup_mount(ROOT))
    if backups["postgres"].is_symlink():
        raise ValueError("B PostgreSQL recovery evidence is unsafe")
    revision_evidence = json.loads((backups["postgres"] / "recovery-verified.json").read_text())
    database = "momcozy_lab_backend_uat" if args.service == "backend" else "momcozy_lab_agent_uat"
    if revision_evidence["databases"][database]["alembic_revision"] != revision:
        raise ValueError("B live schema differs from isolated recovery evidence")
    if not getattr(args, "recover_running", False):
        start_services(args)
    check_ready(args.service)
    check_running_service_provenance(args)
    manifest = args.source / "release-manifest.json"
    payload = {"deployment_target": "north-america-staging", "service": args.service,
               "commit": args.commit, "image_ref": args.image, "migration_revision": revision,
               "local_image_id": args.image_id}
    descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(payload, stream, sort_keys=True)
    pointer = ROOT / "current" / args.service
    pointer.symlink_to(args.source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--recover-running", action="store_true",
                        help="After a partial first activation, verify exact running containers without restarting them")
    parser.add_argument("--service", required=True, choices=PORTS)
    for option in ("source", "env-file", "target"):
        parser.add_argument("--" + option, required=True, type=Path)
    for option in ("commit", "image", "local-image", "image-id"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--agent-env", type=Path)
    args = parser.parse_args(argv)
    if not args.apply:
        print("No B services changed; --apply required.")
        return 0
    try:
        if args.service == "backend":
            if args.agent_env is None:
                raise ValueError("B Product requires Agent env cross-check")
            preflight(argparse.Namespace(target=args.target, source=args.source, commit=args.commit,
                                         image=args.image, backend_env=args.env_file, agent_env=args.agent_env))
        else:
            result = _run(["python3", str(args.source / "scripts/b_release.py"), "--target", str(args.target),
                           "--source", str(args.source), "--commit", args.commit, "--image", args.image,
                           "--agent-env", str(args.env_file)])
            if "static admission passed" not in result:
                raise ValueError("B Agent admission incomplete")
        if args.source != ROOT / "releases" / args.service / args.commit:
            raise ValueError("B source path does not match commit")
        verify_image(args.local_image, args.commit, args.service, args.image_id)
        lock = ROOT / "shared/north-america-staging-release.lock"
        if lock.is_symlink() or not lock.is_file() or stat.S_IMODE(lock.stat().st_mode) != 0o600:
            raise ValueError("B lock is not private")
        with lock.open("r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            activate(args)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print("FAIL B first activation; no success pointer written; inspect B containers", file=sys.stderr)
        return 1
    print(f"B {args.service} loopback and workers verified; B-only current pointer created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
