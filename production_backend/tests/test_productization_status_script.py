import subprocess
from pathlib import Path

from production_backend.scripts.check_productization_status import run_checks


ROOT = Path(__file__).resolve().parents[2]


def test_productization_status_checks_pass_for_current_backend() -> None:
    results = run_checks(ROOT)

    assert results
    assert all(result.status == "pass" for result in results)


def test_productization_status_script_prints_json_summary() -> None:
    completed = subprocess.run(
        [
            "production_backend/.venv/bin/python",
            "production_backend/scripts/check_productization_status.py",
            "--json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert '"status": "pass"' in completed.stdout
    assert '"failed": 0' in completed.stdout


def test_productization_status_checks_worker_operations_scripts() -> None:
    results = run_checks(ROOT)
    names = {result.name for result in results}

    assert "production_backend/scripts/inspect_worker_backlog.py" in names
    assert "production_backend/scripts/recover_stuck_agent_runs.py" in names
    assert "Makefile:backend-worker-backlog:" in names
    assert "Makefile:backend-agent-recover-stuck-runs:" in names
