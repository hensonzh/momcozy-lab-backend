from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "production-backend-ci.yml"


def test_production_backend_ci_runs_core_gates() -> None:
    text = WORKFLOW.read_text()

    for phrase in [
        "python -m pytest production_backend/tests",
        "production_backend/scripts/run_agent_seed_eval.py",
        "--junit-output /tmp/agent-seed-eval.junit.xml",
        "python -m ruff check app tests scripts",
        "python -m mypy app",
        "python -m alembic -c production_backend/alembic.ini heads",
        "python -m alembic -c production_backend/alembic.ini upgrade head --sql",
        "production_backend/scripts/check_backup_restore_hooks.py",
        "production_backend/scripts/export_openapi.py",
        "previous_response" + "_id|Chat" + "Session|ENTRY" + "_API_KEY",
        "docker compose -f production_backend/docker-compose.yml config",
        "docker build -f production_backend/Dockerfile .",
        "postgres-migration",
        "python -m alembic -c production_backend/alembic.ini upgrade head",
        "agent_eval_cases",
        "redis-runtime-controls",
        "production_backend/scripts/check_redis_runtime_controls.py",
        "object-storage-integration",
        "production_backend/scripts/check_object_storage_profile.py",
    ]:
        assert phrase in text


def test_production_backend_ci_is_scoped_to_isolated_backend() -> None:
    text = WORKFLOW.read_text()

    assert "production_backend/**" in text
    assert "src/momcozy" + "_agent" not in text
