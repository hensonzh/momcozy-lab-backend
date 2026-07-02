import asyncio

from production_backend.app.core.settings import Settings
from production_backend.scripts.run_outbox_worker import run_outbox_worker


def test_outbox_worker_process_exits_safely_when_disabled() -> None:
    result = asyncio.run(run_outbox_worker(settings=Settings(outbox_worker_enabled=False), once=True))

    assert result == {"status": "disabled", "cycles": 0, "processed": 0}
