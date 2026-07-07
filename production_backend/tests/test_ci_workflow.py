from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "production-backend-ci.yml"
PROVIDER_EVAL_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "agent-provider-eval.yml"


def test_production_backend_ci_runs_core_gates() -> None:
    text = WORKFLOW.read_text()

    for phrase in [
        "python -m pytest production_backend/tests",
        "production_backend/scripts/run_agent_seed_eval.py",
        "--junit-output /tmp/agent-seed-eval.junit.xml",
        "actions/upload-artifact@v4",
        "agent-seed-eval",
        "python -m ruff check app tests scripts",
        "python -m mypy app",
        "python -m alembic -c production_backend/alembic.ini heads",
        "python -m alembic -c production_backend/alembic.ini upgrade head --sql",
        "production_backend/scripts/check_backup_restore_hooks.py",
        "production_backend/scripts/export_openapi.py",
        "previous_response" + "_id|Chat" + "Session|ENTRY" + "_API_KEY",
        "docker compose -f production_backend/docker-compose.local.yml config",
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


def test_agent_provider_eval_workflow_is_manual_or_scheduled() -> None:
    text = PROVIDER_EVAL_WORKFLOW.read_text()

    for phrase in [
        "workflow_dispatch",
        "schedule:",
        "production_backend/scripts/run_agent_provider_eval.py",
        "--allow-skip-without-credentials",
        "AGENT_MODEL_PROVIDER: ${{ vars.AGENT_MODEL_PROVIDER || 'openai' }}",
        "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}",
        "MINIMAX_API_KEY: ${{ secrets.MINIMAX_API_KEY }}",
        "MINIMAX_MODEL: ${{ vars.MINIMAX_MODEL || 'MiniMax-M3' }}",
        "AGENT_PROVIDER_EVAL_MAX_CASES",
        "AGENT_PROVIDER_EVAL_COST_BUDGET_USD",
        "--cost-budget-usd",
        "actions/upload-artifact@v4",
        "agent-provider-eval",
    ]:
        assert phrase in text
