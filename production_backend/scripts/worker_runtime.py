from __future__ import annotations

import asyncio
import signal
from contextlib import suppress


def install_stop_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(signum, stop_event.set)


async def sleep_until_stop(*, seconds: int, stop_event: asyncio.Event | None) -> None:
    if seconds <= 0:
        return
    if stop_event is None:
        await asyncio.sleep(seconds)
        return
    if stop_event.is_set():
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        return
