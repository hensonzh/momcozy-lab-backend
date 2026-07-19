from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import suppress
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def run_check() -> dict[str, object]:
    from app.core.settings import Settings
    from app.infrastructure.redis.client import close_redis_client, create_redis_client
    from app.modules.agent_runtime.run_lifecycle.controls import AgentRunControls
    from app.modules.agent_runtime.event_stream.transient import AgentTransientStream

    settings = Settings.from_env()
    client = create_redis_client(settings)
    controls = AgentRunControls(client)
    transient_stream = AgentTransientStream(client)
    thread_id = uuid4()
    run_id = uuid4()
    owner_token = uuid4().hex
    connected = False

    try:
        await client.ping()
        connected = True

        await controls.set_active_run(thread_id=thread_id, run_id=run_id, ttl_seconds=60)
        await controls.request_cancel(run_id=run_id, ttl_seconds=60)
        await controls.set_stream_cursor(run_id=run_id, sequence=42, ttl_seconds=60)
        await transient_stream.publish_message_delta(thread_id=thread_id, run_id=run_id, delta="hello", ttl_seconds=60)

        first_lock = await controls.acquire_run_lock(run_id=run_id, owner_token=owner_token, ttl_seconds=60)
        second_lock = await controls.acquire_run_lock(run_id=run_id, owner_token="other", ttl_seconds=60)

        _require(await controls.get_active_run(thread_id=thread_id) == str(run_id), "active run key mismatch")
        _require(await controls.is_cancel_requested(run_id=run_id), "cancel flag missing")
        _require(await controls.get_stream_cursor(run_id=run_id) == 42, "stream cursor mismatch")
        transient_events = await transient_stream.read(run_id=run_id, after_cursor="0-0")
        _require(bool(transient_events), "transient stream delta missing")
        _require(transient_events[-1].payload.get("delta") == "hello", "transient stream delta mismatch")
        _require(first_lock is True, "first run lock was not acquired")
        _require(second_lock is False, "run lock was not exclusive")

        await controls.release_run_lock(run_id=run_id, owner_token="other")
        third_lock = await controls.acquire_run_lock(run_id=run_id, owner_token="third", ttl_seconds=60)
        _require(third_lock is False, "foreign owner released the run lock")

        return {
            "redis_url_configured": bool(settings.redis_url),
            "checked": [
                "ping",
                "thread_active_run",
                "run_cancel_flag",
                "run_stream_cursor",
                "run_transient_stream",
                "exclusive_run_lock",
                "owner_checked_lock_release",
            ],
        }
    finally:
        if connected:
            with suppress(Exception):
                await controls.clear_active_run(thread_id=thread_id, run_id=run_id)
                await controls.clear_cancel(run_id=run_id)
                await controls.clear_stream_cursor(run_id=run_id)
                await client.delete(f"agent:run:{run_id}:transient_stream")
                await controls.release_run_lock(run_id=run_id, owner_token=owner_token)
        await close_redis_client(client)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check live Redis agent runtime controls.")
    parser.parse_args()
    print(json.dumps(asyncio.run(run_check()), indent=2, sort_keys=True))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


if __name__ == "__main__":
    main()
