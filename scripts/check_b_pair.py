#!/usr/bin/env python3
"""Read-only cross-service B private env contract, without revealing values."""

from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path

SHARED = (
    "MOMCOZY_B_ENV_MARKER", "MOMCOZY_BACKEND_COMPOSE_PROJECT", "MOMCOZY_AGENT_COMPOSE_PROJECT",
    "MOMCOZY_NETWORK_NAME", "MOMCOZY_BACKEND_API_BIND", "MOMCOZY_AGENT_API_BIND",
    "MOMCOZY_BACKEND_PUBLIC_URL", "MOMCOZY_AGENT_PUBLIC_URL", "MOMCOZY_POSTGRES_ADMIN_USER",
    "MOMCOZY_AGENT_POSTGRES_DB", "MOMCOZY_AGENT_POSTGRES_USER", "MOMCOZY_AGENT_POSTGRES_PASSWORD",
    "MOMCOZY_AGENT_MINIO_BUCKET", "MOMCOZY_AGENT_MINIO_ACCESS_KEY", "MOMCOZY_AGENT_MINIO_SECRET_KEY",
    "MOMCOZY_AGENT_REDIS_PASSWORD", "AUTH_JWT_ISSUER",
)


def _read(path: Path) -> dict[str, str]:
    if not path.is_absolute() or path.parent.is_symlink() or path.parent.parent.is_symlink():
        raise ValueError("B env path must be absolute without symlinked parents")
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("B env must be a private mode-0600 regular file")
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("B env contains malformed data")
        key, value = line.split("=", 1)
        if key in result:
            raise ValueError("B env contains duplicate keys")
        result[key] = value.strip()
    return result


def validate_pair(backend: Path, agent: Path) -> None:
    product, runtime = _read(backend), _read(agent)
    for key in SHARED:
        if not product.get(key) or product[key] != runtime.get(key):
            raise ValueError(f"B service configuration differs: {key}")
        if "REPLACE_WITH" in product[key] or "example.invalid" in product[key] or "${" in product[key]:
            raise ValueError(f"B service configuration contains a placeholder: {key}")
    if product.get("AGENT_RUNTIME_SERVICE_API_KEY", "").startswith("REPLACE_WITH") or not product.get("AGENT_RUNTIME_SERVICE_API_KEY"):
        raise ValueError("B Product-to-Agent service identity is a placeholder")
    if product.get("AGENT_RUNTIME_SERVICE_API_KEY") != runtime.get("PRODUCT_BACKEND_SERVICE_KEY"):
        raise ValueError("B Product-to-Agent service identity differs")
    if product.get("MOMCOZY_PRODUCT_REDIS_PASSWORD") == runtime.get("MOMCOZY_AGENT_REDIS_PASSWORD"):
        raise ValueError("B Redis service identities must be distinct")
    if product.get("MOMCOZY_PRODUCT_MINIO_ACCESS_KEY") == runtime.get("MOMCOZY_AGENT_MINIO_ACCESS_KEY"):
        raise ValueError("B MinIO service identities must be distinct")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-env", type=Path, required=True)
    parser.add_argument("--agent-env", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        validate_pair(args.backend_env, args.agent_env)
    except (OSError, ValueError):
        print("FAIL B pair: private cross-service configuration differs or is unreadable", file=sys.stderr)
        return 1
    print("B private cross-service contract matches; no network or provider check performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
