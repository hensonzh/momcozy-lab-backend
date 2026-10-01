#!/usr/bin/env python3
"""Read-only B rollback admission; never switches services or downgrades schemas."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path("/opt/momcozy-lab-us-east-uat")
REVISION = re.compile(r"^[0-9a-zA-Z_.-]+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _manifest(path: Path, root: Path, service: str, slot: str) -> dict[str, Any]:
    link = root / slot / service
    if path != link / "release-manifest.json" or not link.is_symlink() or root.is_symlink():
        raise ValueError("B rollback pointer is not a B-only release symlink")
    target = link.resolve(strict=True)
    release_dir = root / "releases" / service
    if target.parent != release_dir or not COMMIT.fullmatch(target.name) or path.is_symlink() or not path.is_file():
        raise ValueError("B rollback pointer leaves the isolated B release root")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("deployment_target") != "north-america-staging" or data.get("service") != service:
        raise ValueError("B rollback manifest identity is invalid")
    image, revision = data.get("image_ref", ""), data.get("migration_revision", "")
    if (data.get("commit") != target.name or not isinstance(image, str)
            or not re.fullmatch(rf"ghcr\.io/hensonzh/momcozy-lab-{service}@sha256:[0-9a-f]{{64}}", image)
            or not isinstance(revision, str) or not REVISION.fullmatch(revision)):
        raise ValueError("B rollback manifest commit, digest or schema is invalid")
    return data


def validate_rollback_pair(
    root: Path, current: Path, previous: Path, service: str, confirmed: bool, *, database_revision: str = "",
) -> None:
    if service not in {"backend", "agent"}:
        raise ValueError("unknown B service")
    if not confirmed:
        raise ValueError("B rollback requires explicit client compatibility confirmation")
    current_data = _manifest(current, root, service, "current")
    previous_data = _manifest(previous, root, service, "previous")
    if current_data["commit"] == previous_data["commit"]:
        raise ValueError("B rollback requires a distinct previous release")
    if not REVISION.fullmatch(database_revision) or database_revision != current_data["migration_revision"]:
        raise ValueError("B rollback must verify the live database revision")
    if current_data["migration_revision"] != previous_data["migration_revision"]:
        raise ValueError("B rollback schema revisions differ; plan a forward-compatible recovery")


def read_live_revision(service: str) -> str:
    """Query only the B-owned Postgres container, never take a caller-supplied ID."""
    listing = subprocess.run(
        ["docker", "ps", "--filter", "label=com.docker.compose.project=momcozy-lab-backend-us-east-uat",
         "--filter", "label=com.docker.compose.service=postgres", "--format", "{{.ID}}"],
        capture_output=True, text=True, check=True,
    )
    ids = listing.stdout.splitlines()
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
        raise ValueError("expected exactly one B-owned PostgreSQL container")
    database = "MOMCOZY_PRODUCT_POSTGRES_DB" if service == "backend" else "MOMCOZY_AGENT_POSTGRES_DB"
    query = f'psql -X --username "$POSTGRES_USER" --dbname "${database}" --tuples-only --no-align --set ON_ERROR_STOP=1 --command "SELECT version_num FROM public.alembic_version"'
    result = subprocess.run(["docker", "exec", ids[0], "sh", "-ec", query], capture_output=True, text=True, check=True)
    revision = result.stdout.strip()
    if not REVISION.fullmatch(revision):
        raise ValueError("B PostgreSQL has no single safe Alembic revision")
    return revision


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--service", required=True, choices=["backend", "agent"])
    parser.add_argument("--confirm-client-compatible", action="store_true")
    args = parser.parse_args(argv)
    root = args.release_root
    try:
        if root != ROOT or root.is_symlink():
            raise ValueError("B rollback root is not approved")
        current = root / "current" / args.service / "release-manifest.json"
        previous = root / "previous" / args.service / "release-manifest.json"
        # Inspect the pointers before any Docker query. Recheck under a B-only
        # release lock in the eventual mutation runner; this CLI never mutates.
        _manifest(current, root, args.service, "current")
        _manifest(previous, root, args.service, "previous")
        revision = read_live_revision(args.service)
        validate_rollback_pair(root, current, previous, args.service, args.confirm_client_compatible, database_revision=revision)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print("FAIL B rollback admission: missing or incompatible B-only live evidence", file=sys.stderr)
        return 1
    print("B rollback passes live DB and static manifest checks; no service or schema changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
