from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from collections.abc import Awaitable
from pathlib import Path
from typing import Protocol


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class RedisPingClient(Protocol):
    def ping(self) -> Awaitable[bool] | bool: ...


async def check_client(client: RedisPingClient) -> dict[str, object]:
    response = client.ping()
    if inspect.isawaitable(response):
        response = await response
    if not response:
        raise RuntimeError("Redis ping returned an unexpected value")
    return {"checked": ["connect", "ping"]}


async def run_check() -> dict[str, object]:
    from app.core.settings import Settings
    from app.infrastructure.redis import close_redis_client, create_redis_client

    settings = Settings.from_env()
    client = create_redis_client(settings)
    try:
        result = await check_client(client)
        return {
            "redis_url_configured": bool(settings.redis_url),
            **result,
        }
    finally:
        await close_redis_client(client)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Redis connectivity for the active Product Backend profile.")
    parser.parse_args()
    print(json.dumps(asyncio.run(run_check()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
