from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "backend-ci.yml"
)


def test_production_backend_ci_runs_core_gates() -> None:
    text = WORKFLOW.read_text()

    for phrase in [
        "python -m pytest tests",
        "python -m ruff check app tests scripts",
        "python -m mypy app scripts",
        "python -m alembic -c alembic.ini heads",
        "python -m alembic -c alembic.ini upgrade head --sql",
        "scripts/check_backup_restore_hooks.py",
        "scripts/export_openapi.py",
        "previous_response" + "_id|Chat" + "Session|ENTRY" + "_API_KEY",
        "docker compose -f docker-compose.local.yml config",
        "docker compose -f docker-compose.test.yml config",
        "docker compose -f docker-compose.prod.yml config",
        "docker build -f Dockerfile .",
        "postgres-migration",
        "python -m alembic -c alembic.ini upgrade 20260723_0047",
        "Seed Product data before Runtime extraction",
        "python -m alembic -c alembic.ini upgrade head",
        '"idempotency_keys"',
        '"feeding_records"',
        '"legacy_agent_context_checkpoints"',
        "product data was not preserved",
        "retired runtime tables",
        "redis-product-profile",
        "scripts/check_redis_profile.py",
        "object-storage-integration",
        "scripts/check_object_storage_profile.py",
    ]:
        assert phrase in text

    for retired in [
        "test_agent_task8_observed_eval",
        "run_agent_fact_eval.py",
        "agent-service-observed-eval",
        "redis-runtime-controls",
        "check_redis_runtime_controls.py",
    ]:
        assert retired not in text


def test_production_backend_ci_is_scoped_to_isolated_backend() -> None:
    text = WORKFLOW.read_text()

    assert "**" in text
    assert "src/momcozy" + "_agent" not in text
