#!/usr/bin/env python3
"""One-time B-host credential generation; keep provider secrets as placeholders.

Run as the owner of the isolated US-East B root. Never pass or print secrets.
No service is started, no Docker state is changed, and reruns never rotate keys.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HOST_ROOT = Path("/opt/momcozy-lab-us-east-uat")
TARGET_HOSTNAME = "ip-172-31-29-24"
GENERATED_BACKEND = (
    "MOMCOZY_POSTGRES_ADMIN_PASSWORD", "MOMCOZY_PRODUCT_POSTGRES_PASSWORD",
    "MOMCOZY_AGENT_POSTGRES_PASSWORD", "MOMCOZY_REDIS_ADMIN_PASSWORD",
    "MOMCOZY_PRODUCT_REDIS_PASSWORD", "MOMCOZY_AGENT_REDIS_PASSWORD",
    "MOMCOZY_MINIO_ROOT_USER", "MOMCOZY_MINIO_ROOT_PASSWORD",
    "MOMCOZY_PRODUCT_MINIO_ACCESS_KEY", "MOMCOZY_PRODUCT_MINIO_SECRET_KEY",
    "MOMCOZY_AGENT_MINIO_ACCESS_KEY", "MOMCOZY_AGENT_MINIO_SECRET_KEY",
    "AUTH_JWT_PRIVATE_KEY_B64", "AUTH_EMAIL_TOKEN_KEY", "SERVICE_API_KEY",
    "AGENT_RUNTIME_SERVICE_API_KEY",
)
SHARED_AGENT = (
    "MOMCOZY_AGENT_POSTGRES_PASSWORD", "MOMCOZY_AGENT_REDIS_PASSWORD",
    "MOMCOZY_AGENT_MINIO_ACCESS_KEY", "MOMCOZY_AGENT_MINIO_SECRET_KEY",
)
AGENT_SERVICE_KEY = "PRODUCT_BACKEND_SERVICE_KEY"
AGENT_ADMIN_KEY = "RUNTIME_ADMIN_SERVICE_KEY"
EXPECTED_IDS = {
    "MOMCOZY_B_ENV_MARKER": "us-east-uat",
    "MOMCOZY_BACKEND_COMPOSE_PROJECT": "momcozy-lab-backend-us-east-uat",
    "MOMCOZY_AGENT_COMPOSE_PROJECT": "momcozy-lab-agent-us-east-uat",
    "MOMCOZY_BACKEND_PUBLIC_URL": "https://backend-us-dev.lute-momcozylab.luteos.cloud",
    "MOMCOZY_AGENT_PUBLIC_URL": "https://agent-us-dev.lute-momcozylab.luteos.cloud",
}


def _private(path: Path, mode: int) -> None:
    if (path.is_symlink() or not path.exists()
            or (mode == 0o600 and not path.is_file())
            or (mode == 0o700 and not path.is_dir())):
        raise ValueError("B private path is missing or unsafe")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) != mode or info.st_uid != os.geteuid():
        raise ValueError("B private path mode or owner is unsafe")


def _parse(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("B private env is malformed")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in result:
            raise ValueError("B private env has an invalid or duplicate key")
        result[key] = value
    for key, expected in EXPECTED_IDS.items():
        if result.get(key) != expected:
            raise ValueError("B private env identity does not match the approved host")
    return result


def _placeholder(key: str) -> str:
    return f"REPLACE_WITH_US_EAST_UAT_{key}"


def _replace(text: str, generated: dict[str, str]) -> str:
    lines = text.splitlines(keepends=True)
    for key, value in generated.items():
        matches = [index for index, line in enumerate(lines) if line.startswith(key + "=")]
        if len(matches) != 1 or lines[matches[0]].split("=", 1)[1].strip() != _placeholder(key):
            raise ValueError("B credential was altered; refusing to overwrite")
        lines[matches[0]] = f"{key}={value}\n"
    return "".join(lines)


def _jwt() -> str:
    result = subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-outform", "PEM"],
        capture_output=True, check=True,
    )
    if not result.stdout.startswith(b"-----BEGIN PRIVATE KEY-----\n"):
        raise ValueError("OpenSSL did not produce a PKCS#8 RSA key")
    return base64.b64encode(result.stdout).decode("ascii")


def _write_staged(path: Path, text: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".b-credentials-", dir=path.parent)
    staged = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def provision(root: Path) -> bool:
    if root != HOST_ROOT or socket.gethostname() != TARGET_HOSTNAME:
        raise ValueError("B credential provisioning requires the approved US-East host")
    for directory in (root, root / "shared", root / "shared/backend", root / "shared/agent"):
        _private(directory, 0o700)
    lock = root / "shared/north-america-staging-release.lock"
    _private(lock, 0o600)
    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            raise ValueError("B release lock is not private")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backend = root / "shared/backend/north-america-staging.env"
        agent = root / "shared/agent/north-america-staging.env"
        for path in (backend, agent):
            _private(path, 0o600)
        product_text, runtime_text = backend.read_text(), agent.read_text()
        product, runtime = _parse(product_text), _parse(runtime_text)
        backend_pending = [key for key in GENERATED_BACKEND if product.get(key) == _placeholder(key)]
        agent_keys = (*SHARED_AGENT, AGENT_SERVICE_KEY, AGENT_ADMIN_KEY)
        agent_pending = [key for key in agent_keys if runtime.get(key) == _placeholder(key)]
        if not backend_pending and not agent_pending:
            if (any(not product.get(key) or "REPLACE_WITH" in product[key] for key in GENERATED_BACKEND)
                    or any(not runtime.get(key) or "REPLACE_WITH" in runtime[key] for key in agent_keys)):
                raise ValueError("B private env is partially provisioned; refusing rotation")
            for key in SHARED_AGENT:
                if runtime.get(key) != product.get(key):
                    raise ValueError("B already-provisioned cross-service identity differs")
            if runtime.get(AGENT_SERVICE_KEY) != product.get("AGENT_RUNTIME_SERVICE_API_KEY"):
                raise ValueError("B already-provisioned service identity differs")
            return False
        if len(backend_pending) != len(GENERATED_BACKEND) or len(agent_pending) != len(agent_keys):
            raise ValueError("B private env is partially provisioned; refusing rotation")
        generated = {key: secrets.token_urlsafe(48) for key in GENERATED_BACKEND}
        for key in ("MOMCOZY_MINIO_ROOT_USER", "MOMCOZY_PRODUCT_MINIO_ACCESS_KEY", "MOMCOZY_AGENT_MINIO_ACCESS_KEY"):
            generated[key] = "b" + secrets.token_hex(12)
        generated["AUTH_JWT_PRIVATE_KEY_B64"] = _jwt()
        shared = {key: generated[key] for key in SHARED_AGENT}
        shared[AGENT_SERVICE_KEY] = generated["AGENT_RUNTIME_SERVICE_API_KEY"]
        shared[AGENT_ADMIN_KEY] = secrets.token_urlsafe(48)
        new_product = _replace(product_text, generated)
        new_runtime = _replace(runtime_text, shared)
        product_staged, agent_staged = None, None
        try:
            product_staged = _write_staged(backend, new_product)
            agent_staged = _write_staged(agent, new_runtime)
            # In a power loss between replaces, rerun refuses partial state.
            os.replace(product_staged, backend)
            os.replace(agent_staged, agent)
            product_staged = agent_staged = None
            for path in (backend, agent):
                _private(path, 0o600)
        finally:
            for staged in (product_staged, agent_staged):
                if staged is not None:
                    staged.unlink(missing_ok=True)
        return True
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Generate B-only credentials in existing private host files")
    args = parser.parse_args(argv)
    if not args.apply:
        print("No changes made. --apply generates missing B-owned credentials on the approved US-East host.")
        return 0
    try:
        changed = provision(HOST_ROOT)
    except (OSError, ValueError, subprocess.SubprocessError, BlockingIOError):
        print("FAIL B credential provisioning: private state unsafe or incomplete; no secrets shown", file=sys.stderr)
        return 1
    print("B-owned credentials generated in two mode-0600 private env files." if changed else
          "B-owned credentials already present; no rotation or file change.")
    print("External provider keys and invite policy remain separate release gates; no services started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
