#!/usr/bin/env python3
"""Read-only guard for a fresh B bootstrap; never inspect or import managed data."""

from __future__ import annotations

import subprocess
import sys

PROJECTS = ("momcozy-lab-backend-us-east-uat", "momcozy-lab-agent-us-east-uat")
NETWORK = "momcozy-lab-us-east-uat"
VOLUME_PREFIXES = tuple(f"{project}_" for project in PROJECTS)
CONTAINER_PREFIXES = tuple(f"{project}-" for project in PROJECTS)


def _names(*args: str) -> list[str]:
    result = subprocess.run(["docker", *args, "--format", "{{.Name}}" if args[0] != "ps" else "{{.Names}}"],
                            capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


def validate_fresh() -> None:
    """Reject named or label-owned B resources, including stopped containers."""
    for command, prefixes in (
        (("volume", "ls"), VOLUME_PREFIXES),
        (("network", "ls"), (*VOLUME_PREFIXES, NETWORK)),
        (("ps", "-a"), CONTAINER_PREFIXES),
    ):
        if any(name.startswith(prefixes) for name in _names(*command)):
            raise ValueError("B Docker state already exists; manual review required")
        for project in PROJECTS:
            if _names(*command, "--filter", f"label=com.docker.compose.project={project}"):
                raise ValueError("B Docker state already exists; manual review required")


def main() -> int:
    try:
        validate_fresh()
    except (OSError, ValueError, subprocess.SubprocessError):
        print("FAIL B fresh bootstrap: existing state or unavailable Docker; no changes made", file=sys.stderr)
        return 1
    print("B Docker resources are fresh by name and Compose label; no service started or managed data imported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
