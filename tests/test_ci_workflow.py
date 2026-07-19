from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "production-backend-ci.yml"


def test_production_backend_ci_runs_core_gates() -> None:
    text = WORKFLOW.read_text()

    for phrase in [
        "python -m pytest tests",
        "tests/test_agent_task8_observed_eval.py",
        "--junitxml=/tmp/agent-service-observed-eval.junit.xml",
        "actions/upload-artifact@v4",
        "agent-service-observed-eval",
        "python -m ruff check app tests scripts",
        "python -m mypy app",
        "python -m alembic -c alembic.ini heads",
        "python -m alembic -c alembic.ini upgrade head --sql",
        "scripts/check_backup_restore_hooks.py",
        "scripts/export_openapi.py",
        "previous_response" + "_id|Chat" + "Session|ENTRY" + "_API_KEY",
        "docker compose -f docker-compose.local.yml config",
        "docker build -f Dockerfile .",
        "postgres-migration",
        "python -m alembic -c alembic.ini upgrade head",
        "agent_eval_cases",
        "redis-runtime-controls",
        "scripts/check_redis_runtime_controls.py",
        "object-storage-integration",
        "scripts/check_object_storage_profile.py",
    ]:
        assert phrase in text


def test_production_backend_ci_is_scoped_to_isolated_backend() -> None:
    text = WORKFLOW.read_text()

    assert "**" in text
    assert "src/momcozy" + "_agent" not in text
