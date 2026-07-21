from __future__ import annotations

import argparse
import asyncio
import json

from app.workers.agent_process import run_agent_worker
from scripts.worker_runtime import install_stop_signal_handlers


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the production agent runtime worker.")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
    parser.add_argument("--max-cycles", type=int, default=None, help="Process at most this many cycles before exiting.")
    args = parser.parse_args()

    asyncio.run(_run_from_cli(once=args.once, max_cycles=args.max_cycles))


async def _run_from_cli(*, once: bool, max_cycles: int | None) -> None:
    stop_event = asyncio.Event()
    install_stop_signal_handlers(stop_event)
    result = await run_agent_worker(once=once, max_cycles=max_cycles, stop_event=stop_event)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
