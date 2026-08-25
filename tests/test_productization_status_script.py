import subprocess
import sys
from pathlib import Path

from scripts.check_productization_status import run_checks


ROOT = Path(__file__).resolve().parents[1]


def test_productization_status_checks_pass_for_current_backend() -> None:
    results = run_checks(ROOT)

    assert results
    assert all(result.status == "pass" for result in results)


def test_productization_status_script_prints_json_summary() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/check_productization_status.py",
            "--json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert '"status": "pass"' in completed.stdout
    assert '"failed": 0' in completed.stdout


def test_productization_status_checks_product_operations_and_runtime_boundary() -> None:
    results = run_checks(ROOT)
    names = {result.name for result in results}

    assert "scripts/check_redis_profile.py" in names
    assert "scripts/check_product_asset_storage.py" in names
    assert "docker-compose.ci.yml" in names
    assert "docker-compose.ci.yml:name: momcozy-lab-backend-ci" in names
    assert "app/modules/auth/jwks_router.py" in names
    assert "app/modules/profiles/agent_router.py" in names
    assert "retired:app/agent_runtime" in names
    assert "retired:scripts/run_agent_worker.py" in names
    assert "Makefile:scripts/check_product_asset_storage.py" in names
