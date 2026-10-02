"""Disposable Redis ACL RDB restore drill; no B volumes, host ports or network."""

import os
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from scripts.b_redis_recovery import _drill, _snapshot, _source_key_count

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.getenv("RUN_B_REDIS_RECOVERY") != "1", reason="explicit Docker integration opt-in")
def test_synthetic_b_redis_rdb_is_restorable() -> None:
    name = f"momcozy-b-redis-ci-{os.getpid()}"
    subprocess.run([
        "docker", "run", "-d", "--rm", "--name", name, "--network", "none",
        "--tmpfs", "/run/redis:mode=0700", "--tmpfs", "/data",
        "-e", "MOMCOZY_REDIS_ADMIN_PASSWORD=synthetic_admin_password",
        "-e", "MOMCOZY_PRODUCT_REDIS_PASSWORD=synthetic_product_password",
        "-e", "MOMCOZY_AGENT_REDIS_PASSWORD=synthetic_agent_password",
        "-v", f"{ROOT / 'deploy/us-east-uat/start-redis.sh'}:/usr/local/bin/start-momcozy-redis:ro",
        "--entrypoint", "/bin/sh", "redis:7.4-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499", "/usr/local/bin/start-momcozy-redis",
    ], capture_output=True, check=True)
    try:
        for _ in range(30):
            response = subprocess.run([
                "docker", "exec", name, "redis-cli", "--user", "deployment-admin",
                "--pass", "synthetic_admin_password", "--no-auth-warning", "ping",
            ], capture_output=True, text=True, check=False)
            if response.returncode == 0 and response.stdout.strip() == "PONG":
                break
            time.sleep(1)
        else:
            pytest.fail("synthetic B Redis did not start")
        for user, password, key in (
            ("product-backend", "synthetic_product_password", "rate-limit:recovery-canary"),
            ("agent-runtime", "synthetic_agent_password", "agent-runtime:recovery-canary"),
        ):
            response = subprocess.run([
                "docker", "exec", name, "redis-cli", "--user", user,
                "--pass", password, "--no-auth-warning", "set", key, "42",
            ], capture_output=True, text=True, check=True)
            assert response.stdout.strip() == "OK"
        with TemporaryDirectory(prefix="momcozy-b-redis-recovery-") as folder:
            dump = Path(folder) / "dump.rdb"
            expected = _source_key_count(name)
            assert expected == 2
            _snapshot(name, dump)
            assert dump.stat().st_mode & 0o777 == 0o600
            assert _drill(dump, expected) == expected
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True, check=False)
