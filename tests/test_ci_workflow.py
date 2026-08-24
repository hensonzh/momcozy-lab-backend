from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "backend-ci.yml"
)


def test_backend_ci_runs_core_gates() -> None:
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
        "-f docker-compose.staging.yml",
        "--env-file env/compose.staging.env.example",
        "docker build -f Dockerfile .",
        "postgres-migration",
        "python -m alembic -c alembic.ini upgrade head",
        "python -m alembic -c alembic.ini check",
        '"idempotency_keys"',
        '"feeding_records"',
        '"diary_entries"',
        '"maternal_current_delivery_infants"',
        "redis-product-profile",
        "scripts/check_redis_profile.py",
        "object-storage-integration",
        "scripts/check_object_storage_profile.py",
    ]:
        assert phrase in text

    assert "docker-compose.production.yml" not in text
    assert "compose.production.env" not in text

    for retired in [
        "test_agent_task8_observed_eval",
        "run_agent_fact_eval.py",
        "agent-service-observed-eval",
        "redis-runtime-controls",
        "check_redis_runtime_controls.py",
    ]:
        assert retired not in text


def test_backend_ci_is_scoped_to_product_backend() -> None:
    text = WORKFLOW.read_text()

    assert "**" in text
    assert "src/momcozy" + "_agent" not in text
