import asyncio

from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.core.settings import Settings
from production_backend.scripts.run_outbox_worker import _with_metrics, run_outbox_worker


def test_outbox_worker_process_exits_safely_when_disabled() -> None:
    result = asyncio.run(run_outbox_worker(settings=Settings(outbox_worker_enabled=False), once=True))

    assert result == {"status": "disabled", "cycles": 0, "processed": 0}


def test_outbox_worker_process_can_return_metrics_snapshot_for_finite_runs() -> None:
    metrics = RequestMetrics()
    metrics.record_worker_job(job_type="files.cleanup", outcome="completed")

    result = _with_metrics({"status": "ok", "cycles": 1, "processed": 1}, metrics)

    assert result["metrics"]["workers"][0]["job_type"] == "files.cleanup"
    assert result["metrics"]["workers"][0]["outcome_counts"]["completed"] == 1
