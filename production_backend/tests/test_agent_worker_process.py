import asyncio

from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.core.settings import Settings
from production_backend.scripts.run_agent_worker import _with_metrics, run_agent_worker
from production_backend.scripts.worker_runtime import sleep_until_stop


def test_agent_worker_process_exits_safely_when_disabled() -> None:
    result = asyncio.run(run_agent_worker(settings=Settings(agent_runtime_worker_enabled=False), once=True))

    assert result == {"status": "disabled", "cycles": 0, "scanned": 0, "processed": 0, "terminal": 0}


def test_agent_worker_process_can_return_metrics_snapshot_for_finite_runs() -> None:
    metrics = RequestMetrics()
    metrics.record_agent_sdk(node_name="openai_agents_sdk", outcome="failed", error_code="dependency_not_configured")

    result = _with_metrics({"status": "ok", "cycles": 1, "scanned": 0, "processed": 0, "terminal": 0}, metrics)

    assert result["metrics"]["agent_sdk"][0]["node_name"] == "openai_agents_sdk"
    assert result["metrics"]["agent_sdk"][0]["error_code_counts"]["dependency_not_configured"] == 1


def test_worker_runtime_sleep_returns_when_stop_event_is_set() -> None:
    async def run() -> None:
        stop_event = asyncio.Event()
        stop_event.set()
        await sleep_until_stop(seconds=30, stop_event=stop_event)

    asyncio.run(run())
