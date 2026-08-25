import asyncio
import subprocess
import sys
from pathlib import Path

from scripts.check_redis_profile import check_client


ROOT = Path(__file__).resolve().parents[1]


class FakeRedis:
    def __init__(self, response: bool) -> None:
        self.response = response
        self.calls = 0

    async def ping(self) -> bool:
        self.calls += 1
        return self.response


def test_check_client_runs_generic_product_redis_ping() -> None:
    client = FakeRedis(True)

    result = asyncio.run(check_client(client))

    assert result == {"checked": ["connect", "ping"]}
    assert client.calls == 1


def test_redis_profile_check_rejects_failed_ping() -> None:
    client = FakeRedis(False)

    try:
        asyncio.run(check_client(client))
    except RuntimeError as exc:
        assert str(exc) == "Redis ping returned an unexpected value"
    else:
        raise AssertionError("expected failed Redis ping to raise")


def test_redis_profile_check_cli_has_help() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/check_redis_profile.py", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Product Backend profile" in completed.stdout
