import subprocess
from datetime import timezone
from pathlib import Path

import pytest

from production_backend.scripts.inspect_worker_backlog import _status_counts_from_rows
from production_backend.scripts.recover_stuck_agent_runs import _stuck_run_cutoff


ROOT = Path(__file__).resolve().parents[2]


def test_worker_backlog_status_count_helper_normalizes_rows() -> None:
    counts = _status_counts_from_rows([("queued", 2), ("locked", 1), ("dead_lettered", 3)])

    assert counts == {"queued": 2, "locked": 1, "dead_lettered": 3}


def test_stuck_run_cutoff_is_timezone_aware_and_validates_threshold() -> None:
    cutoff = _stuck_run_cutoff(older_than_seconds=900)

    assert cutoff.tzinfo == timezone.utc
    with pytest.raises(ValueError):
        _stuck_run_cutoff(older_than_seconds=0)


def test_worker_ops_scripts_expose_safe_cli_help() -> None:
    for script in [
        "production_backend/scripts/inspect_worker_backlog.py",
        "production_backend/scripts/recover_stuck_agent_runs.py",
    ]:
        completed = subprocess.run(
            ["production_backend/.venv/bin/python", script, "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "usage:" in completed.stdout
