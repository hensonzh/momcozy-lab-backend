#!/usr/bin/env python3
"""Read-only preflight for the private B Compose env; never print secret values."""

from __future__ import annotations

import argparse
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED = {
    "MOMCOZY_B_ENV_MARKER": "us-east-uat",
    "APP_ENV": "staging",
    "MOMCOZY_BACKEND_COMPOSE_PROJECT": "momcozy-lab-backend-us-east-uat",
    "MOMCOZY_AGENT_COMPOSE_PROJECT": "momcozy-lab-agent-us-east-uat",
    "MOMCOZY_NETWORK_NAME": "momcozy-lab-us-east-uat",
    "MOMCOZY_BACKEND_API_BIND": "127.0.0.1:8101",
    "MOMCOZY_AGENT_API_BIND": "127.0.0.1:8102",
    "MOMCOZY_PRODUCT_POSTGRES_DB": "momcozy_lab_backend_uat",
    "MOMCOZY_PRODUCT_POSTGRES_USER": "momcozy_lab_backend_uat",
    "MOMCOZY_AGENT_POSTGRES_DB": "momcozy_lab_agent_uat",
    "MOMCOZY_AGENT_POSTGRES_USER": "momcozy_lab_agent_uat",
    "MOMCOZY_PRODUCT_MINIO_BUCKET": "momcozy-product-us-east-uat",
    "MOMCOZY_AGENT_MINIO_BUCKET": "momcozy-agent-us-east-uat",
    "OBJECT_STORAGE_ENDPOINT_URL": "http://minio:9000",
}
REQUIRED_SECRETS = (
    "MOMCOZY_POSTGRES_ADMIN_PASSWORD", "MOMCOZY_PRODUCT_POSTGRES_PASSWORD",
    "MOMCOZY_AGENT_POSTGRES_PASSWORD", "MOMCOZY_REDIS_ADMIN_PASSWORD",
    "MOMCOZY_PRODUCT_REDIS_PASSWORD", "MOMCOZY_AGENT_REDIS_PASSWORD",
    "MOMCOZY_MINIO_ROOT_USER", "MOMCOZY_MINIO_ROOT_PASSWORD",
    "MOMCOZY_PRODUCT_MINIO_ACCESS_KEY", "MOMCOZY_PRODUCT_MINIO_SECRET_KEY",
    "MOMCOZY_AGENT_MINIO_ACCESS_KEY", "MOMCOZY_AGENT_MINIO_SECRET_KEY",
    "AUTH_JWT_PRIVATE_KEY_B64", "AUTH_EMAIL_TOKEN_KEY", "AUTH_SMTP_PASSWORD",
    "SERVICE_API_KEY", "AGENT_RUNTIME_SERVICE_API_KEY",
)


def validate(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("B env must be a regular, non-symlink mode-0600 file")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("B env contains a malformed line")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("B env contains an invalid key")
        if key in values:
            raise ValueError(f"duplicate B env key: {key}")
        values[key] = value.strip()
    for key, expected in EXPECTED.items():
        if values.get(key) != expected:
            raise ValueError(f"B env {key} does not match the isolated topology")
    if values.get("DATABASE_URL", "").split("@")[-1] != "postgres:5432/momcozy_lab_backend_uat":
        raise ValueError("B env DATABASE_URL must use the B Product database")
    if not values.get("DATABASE_URL", "").startswith("postgresql+asyncpg://momcozy_lab_backend_uat:"):
        raise ValueError("B env DATABASE_URL must use the B Product role")
    if values.get("OBJECT_STORAGE_BUCKET") != EXPECTED["MOMCOZY_PRODUCT_MINIO_BUCKET"]:
        raise ValueError("B env OBJECT_STORAGE_BUCKET must use the B Product bucket")
    if values.get("AGENT_RUNTIME_URL") != values.get("MOMCOZY_AGENT_PUBLIC_URL"):
        raise ValueError("B env Agent origin must match its B public URL")
    if values.get("AGENT_MODEL_ASSET_PUBLIC_BASE_URL") != values.get("MOMCOZY_BACKEND_PUBLIC_URL"):
        raise ValueError("B env asset origin must match the B Product public URL")
    if values.get("AUTH_JWT_ISSUER") != "momcozy-product-us-east-uat":
        raise ValueError("B env AUTH_JWT_ISSUER must use the B issuer")
    if not values.get("REDIS_URL", "").startswith("redis://product-backend:") or values.get("REDIS_URL", "").split("@")[-1] != "redis:6379/0":
        raise ValueError("B env REDIS_URL must use B Redis DB 0")
    a_template = (Path(__file__).resolve().parents[1] / "env/staging.env.example").read_text()
    a_hosts = {
        urlsplit(line.split("=", 1)[1].strip()).hostname
        for line in a_template.splitlines()
        if line.startswith(("MOMCOZY_BACKEND_PUBLIC_URL=", "MOMCOZY_AGENT_PUBLIC_URL="))
    }
    for key in ("MOMCOZY_BACKEND_PUBLIC_URL", "MOMCOZY_AGENT_PUBLIC_URL"):
        url = urlsplit(values.get(key, ""))
        if (url.scheme != "https" or not url.hostname or url.hostname in a_hosts
                or url.hostname.endswith((".invalid", ".test", ".localhost"))
                or url.username or url.password or url.path not in ("", "/")
                or url.query or url.fragment):
            raise ValueError(f"B env {key} must be a non-A HTTPS origin")
    for key in REQUIRED_SECRETS:
        value = values.get(key, "")
        if not value or "REPLACE_WITH" in value or "${" in value:
            raise ValueError(f"B env {key} is missing or a placeholder")
    for key, value in values.items():
        if "REPLACE_WITH" in value or "example.invalid" in value:
            raise ValueError(f"B env {key} still contains a placeholder")
    if values.get("SERVICE_API_KEY") == values.get("AGENT_RUNTIME_SERVICE_API_KEY"):
        raise ValueError("B env service identities must be distinct")
    if values.get("AUTH_REQUIRE_ACTIVE_SESSION") != "true":
        raise ValueError("B env must require active sessions")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate(args.env_file)
    except (OSError, UnicodeError, ValueError) as error:
        # Do not surface parser or filesystem error details containing env values.
        print(f"FAIL B env preflight: {error if isinstance(error, ValueError) else 'cannot read private env'}", file=sys.stderr)
        return 1
    print("B Backend private env passes static isolation checks; no deployment or provider checks performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
