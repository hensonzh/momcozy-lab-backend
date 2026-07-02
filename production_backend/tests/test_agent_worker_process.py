import asyncio

from production_backend.app.core.settings import Settings
from production_backend.scripts.run_agent_worker import run_agent_worker


def test_agent_worker_process_exits_safely_when_disabled() -> None:
    result = asyncio.run(run_agent_worker(settings=Settings(agent_runtime_worker_enabled=False), once=True))

    assert result == {"status": "disabled", "cycles": 0, "scanned": 0, "processed": 0, "terminal": 0}
