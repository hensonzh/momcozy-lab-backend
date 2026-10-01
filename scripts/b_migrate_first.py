#!/usr/bin/env python3
"""Migrate only the two fresh B databases; never start APIs or change pointers.

The initial empty databases have no Alembic revision to preserve. After both
migrations, run real PostgreSQL backup and isolated restore before activation.
Partial failure leaves state intact for manual inspection, never auto-downgrades.
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
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.b_release import B_ROOT, preflight  # noqa: E402

COMMIT = re.compile(r"[0-9a-f]{40}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")


def check_first_release() -> None:
    for service in ("backend", "agent"):
        for slot in ("current", "previous"):
            path = B_ROOT / slot / service
            if path.exists() or path.is_symlink():
                raise ValueError("B first release already has a pointer")
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=momcozy-lab-agent-us-east-uat",
         "--format", "{{.ID}}"], capture_output=True, text=True, check=True,
    )
    if result.stdout.strip():
        raise ValueError("B first release already has Agent containers")
    for service in ("api", "notification-worker", "auth-email-worker"):
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=momcozy-lab-backend-us-east-uat",
             "--filter", f"label=com.docker.compose.service={service}", "--format", "{{.ID}}"],
            capture_output=True, text=True, check=True,
        )
        if result.stdout.strip():
            raise ValueError("B first release already has business containers")


def verify_image(tag: str, commit: str, service: str, expected_id: str) -> None:
    if not COMMIT.fullmatch(commit) or not IMAGE_ID.fullmatch(expected_id):
        raise ValueError("invalid B image provenance expectation")
    if tag != f"ghcr.io/hensonzh/momcozy-lab-{service}:b-oci-{commit[:7]}":
        raise ValueError("B local image tag differs from imported commit")
    result = subprocess.run(["docker", "image", "inspect", tag, "--format", "{{json .}}"],
                            capture_output=True, text=True, check=True)
    image = json.loads(result.stdout)
    labels = image.get("Config", {}).get("Labels") or {}
    if (image.get("Id") != expected_id or labels.get("org.opencontainers.image.revision") != commit
            or labels.get("org.opencontainers.image.source") != f"https://github.com/hensonzh/momcozy-lab-{service}"
            or labels.get("org.momcozy.release-target") != "north-america-staging"):
        raise ValueError("B image provenance does not match expected source and config")


def _compose(source: Path, env_file: Path, image: str, service: str, commit: str, command: list[str]) -> None:
    variables = {**os.environ, f"MOMCOZY_{service.upper()}_ENV_FILE": str(env_file),
                 f"MOMCOZY_{service.upper()}_IMAGE": image}
    if service == "agent":
        variables["MOMCOZY_AGENT_RELEASE_ID"] = commit
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "-f", "docker-compose.us-east-uat.yml",
         "--profile", "tools", *command], cwd=source, env=variables, capture_output=True, check=False,
    )
    if result.returncode:
        raise ValueError("B one-shot migration failed; private output suppressed")


def run_migrations(args: argparse.Namespace) -> None:
    _compose(args.backend_source, args.backend_env, args.backend_local_image, "backend", args.backend_commit,
             ["run", "--rm", "--no-deps", "--pull", "never", "migrate"])
    _compose(args.agent_source, args.agent_env, args.agent_local_image, "agent", args.agent_commit,
             ["run", "--rm", "--no-deps", "--pull", "never", "migrate"])


def check_databases_unmigrated() -> None:
    result = subprocess.run([
        "docker", "ps", "--filter", "label=com.docker.compose.project=momcozy-lab-backend-us-east-uat",
        "--filter", "label=com.docker.compose.service=postgres", "--format", "{{.ID}}",
    ], capture_output=True, text=True, check=True)
    ids = result.stdout.splitlines()
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
        raise ValueError("expected one B PostgreSQL")
    for var in ("MOMCOZY_PRODUCT_POSTGRES_DB", "MOMCOZY_AGENT_POSTGRES_DB"):
        query = (
            'db=$(printenv "$1"); test -n "$db"; '
            'test "$(psql -X -U "$POSTGRES_USER" -d "$db" -Atqc '
            "\"SELECT to_regclass('public.alembic_version')\")\" = \"\""
        )
        result = subprocess.run(["docker", "exec", ids[0], "sh", "-ec", query, "sh", var],
                                capture_output=True, check=False)
        if result.returncode:
            raise ValueError("B database is already migrated or unavailable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    for key in ("backend-source", "agent-source", "backend-env", "agent-env", "backend-target", "agent-target"):
        parser.add_argument("--" + key, required=True, type=Path)
    for key in ("backend-commit", "agent-commit", "backend-image", "agent-image",
                "backend-local-image", "agent-local-image", "backend-image-id", "agent-image-id"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args(argv)
    if not args.apply:
        print("No B schema changed; --apply is required.")
        return 0
    try:
        preflight(argparse.Namespace(target=args.backend_target, source=args.backend_source, commit=args.backend_commit,
                                     image=args.backend_image, backend_env=args.backend_env, agent_env=args.agent_env))
        agent_script = args.agent_source / "scripts/b_release.py"
        result = subprocess.run(["python3", str(agent_script), "--target", str(args.agent_target),
                                 "--source", str(args.agent_source), "--commit", args.agent_commit,
                                 "--image", args.agent_image, "--agent-env", str(args.agent_env)],
                                capture_output=True, check=False)
        if result.returncode:
            raise ValueError("B Agent static admission failed")
        verify_image(args.backend_local_image, args.backend_commit, "backend", args.backend_image_id)
        verify_image(args.agent_local_image, args.agent_commit, "agent", args.agent_image_id)
        lock = B_ROOT / "shared/north-america-staging-release.lock"
        if lock.is_symlink() or not lock.is_file() or stat.S_IMODE(lock.stat().st_mode) != 0o600:
            raise ValueError("B release lock is not private")
        with lock.open("r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check_first_release()
            check_databases_unmigrated()
            run_migrations(args)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print("FAIL B first migration; inspect schema, no API or worker started", file=sys.stderr)
        return 1
    print("B Backend and Agent first migrations succeeded; real PostgreSQL recovery still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
