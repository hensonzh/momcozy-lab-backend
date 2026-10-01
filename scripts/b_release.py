#!/usr/bin/env python3
"""Read-only B release admission; intentionally contains no deploy operation.

A backup manifest or self-reported drill is NOT proof of recovery.
This command performs only static checks; no release is authorized.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_b_env import validate as validate_backend_env  # noqa: E402
from scripts.check_b_pair import validate_pair  # noqa: E402
from scripts.check_release_target import validate_target  # noqa: E402

IMAGE = re.compile(r"^ghcr\.io/hensonzh/momcozy-lab-backend@sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
B_ROOT = Path("/opt/momcozy-lab-us-east-uat")


def _private_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("B target declaration must be a private mode-0600 regular file")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("B target declaration must be an object")
    return data


def validate_inputs(source: Path, commit: str, image: str) -> None:
    if not COMMIT.fullmatch(commit) or not IMAGE.fullmatch(image):
        raise ValueError("B source must have a full SHA and immutable Backend image digest")
    if source.is_symlink() or not source.is_dir():
        raise ValueError("B source directory is missing or a symlink")
    metadata = json.loads((source / "deploy/us-east-uat/release-source.json").read_text())
    if not isinstance(metadata, dict):
        raise ValueError("B source manifest must be an object")
    if (metadata.get("source_branch") != "dev" or metadata.get("deployment_target") != "north-america-staging"
            or metadata.get("dockerfile") != "deploy/Dockerfile"
            or metadata.get("compose_file") != "docker-compose.us-east-uat.yml"):
        raise ValueError("B source manifest does not match the B-only contract")
    if not (source / "deploy/Dockerfile").is_file() or not (source / "docker-compose.us-east-uat.yml").is_file():
        raise ValueError("B build and Compose files are missing")
    # This entry accepts a checked-out clean dev source, not arbitrary extracted
    # files. The future deployment entrypoint must independently stage/verify a
    # committed archive before invoking Docker on the target host.
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    branch = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    if head != commit or branch != "dev" or dirty:
        raise ValueError("B source must be the requested clean dev commit")


def read_public_origin(path: Path, key: str) -> str:
    """Read one non-secret origin without printing the private env."""
    for line in path.read_text().splitlines():
        if line.startswith(key + "="):
            value = line.split("=", 1)[1].strip()
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
                raise ValueError("B public origin is invalid")
            return value.rstrip("/")
    raise ValueError("B public origin is missing")


def preflight(args: argparse.Namespace) -> None:
    target = _private_json(args.target)
    validate_target(target)
    root = Path(target["release_root"])
    if root != B_ROOT:
        raise ValueError("B release root does not match the isolated host layout")
    if args.target != root / "shared/backend/north-america-staging.json":
        raise ValueError("B private target must be in the isolated B root")
    if args.backend_env != Path(target["service_env_file"]) or not args.backend_env.is_relative_to(root / "shared/backend"):
        raise ValueError("B private env must match the isolated target")
    if not args.source.is_absolute() or not args.backend_env.is_absolute() or not args.target.is_absolute():
        raise ValueError("B source and private configuration paths must be absolute")
    if args.target.resolve().is_relative_to(args.source.resolve()) or args.backend_env.resolve().is_relative_to(args.source.resolve()):
        raise ValueError("B private files must live outside the source repository")
    validate_backend_env(args.backend_env)
    if not args.agent_env.is_absolute() or args.agent_env != root / "shared/agent/north-america-staging.env":
        raise ValueError("B Agent env must be in the isolated B root")
    if args.agent_env.resolve().is_relative_to(args.source.resolve()):
        raise ValueError("B Agent env must live outside the source repository")
    validate_pair(args.backend_env, args.agent_env)
    if read_public_origin(args.backend_env, "MOMCOZY_BACKEND_PUBLIC_URL") != target["public_url"]:
        raise ValueError("B private env origin differs from the target declaration")
    validate_inputs(args.source, args.commit, args.image)
    env = {**os.environ, "MOMCOZY_BACKEND_IMAGE": args.image, "MOMCOZY_BACKEND_ENV_FILE": str(args.backend_env)}
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(args.backend_env), "-f", "docker-compose.us-east-uat.yml", "config", "--quiet"],
        cwd=args.source, env=env, capture_output=True, check=False,
    )
    if result.returncode:
        raise ValueError("B Compose rendering failed; output suppressed to protect private values")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--backend-env", type=Path, required=True)
    parser.add_argument("--agent-env", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        preflight(args)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        # Never interpolate external error messages: command stderr and paths
        # may include private credentials or values.
        category = "invalid private B configuration" if isinstance(error, ValueError) else "unavailable B source or configuration"
        print(f"FAIL B admission: {category}", file=sys.stderr)
        return 1
    print("B static admission passed; deploy remains blocked pending proven backups and isolated restore. No mutation performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
