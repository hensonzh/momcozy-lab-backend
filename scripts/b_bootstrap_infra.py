#!/usr/bin/env python3
"""One-time B-only stateful bootstrap; never remove containers, networks or volumes.

This is not application deployment. A failure leaves B state intact for inspection;
subsequent attempts require an explicit recovery procedure rather than --force.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import stat
import subprocess
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.b_release import B_ROOT, preflight  # noqa: E402
from scripts.check_b_fresh_bootstrap import validate_fresh  # noqa: E402


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    # Compose output can include rendered private environment values on error.
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, check=False)
    if result.returncode:
        raise ValueError("B infrastructure step failed; private command output suppressed")


def start_fresh(source: Path, env_path: Path, image: str) -> None:
    validate_fresh()
    env = {**os.environ, "MOMCOZY_BACKEND_ENV_FILE": str(env_path), "MOMCOZY_BACKEND_IMAGE": image}
    command = ["docker", "compose", "--env-file", str(env_path), "-f", "docker-compose.us-east-uat.yml"]
    _run([*command, "up", "-d", "--wait", "postgres", "redis", "minio"], cwd=source, env=env)
    _run([*command, "--profile", "tools", "run", "--rm", "minio-init"], cwd=source, env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--backend-env", required=True, type=Path)
    parser.add_argument("--agent-env", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.apply:
        print("No B infrastructure changed; --apply is required.")
        return 0
    try:
        preflight(args)
        lock = B_ROOT / "shared/north-america-staging-release.lock"
        if lock.is_symlink() or not lock.is_file() or stat.S_IMODE(lock.stat().st_mode) != 0o600:
            raise ValueError("B release lock is not private")
        with lock.open("r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            start_fresh(args.source, args.backend_env, args.image)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("FAIL B infrastructure bootstrap; state was not deleted; inspect before recovery", file=sys.stderr)
        return 1
    print("B PostgreSQL, Redis, MinIO and scoped buckets initialized; no application deployed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
