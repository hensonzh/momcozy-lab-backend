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
        "docker-compose.local.yml",
        "docker-compose.ci.yml",
        "-f docker-compose.deploy.yml",
        "--env-file env/staging.env.example",
        "openssl genpkey",
        "momcozy-lab-backend:ci",
        "--profile tools run --rm migrate",
        "http://127.0.0.1:8000/v1/health/ready",
        "down --volumes",
        "postgres-migration",
        "python -m alembic -c alembic.ini upgrade head",
        "python -m alembic -c alembic.ini check",
        '"idempotency_keys"',
        '"feeding_records"',
        '"maternal_current_delivery_infants"',
        "redis-product-profile",
        "scripts/check_redis_profile.py",
        "object-storage-integration",
        "scripts/check_object_storage_profile.py",
    ]:
        assert phrase in text

    assert "docker-compose.production.yml" not in text
    assert "compose.production.env" not in text
    assert "docker build -f Dockerfile ." not in text

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


def test_product_owned_agent_batch_receipts_are_not_rejected_as_runtime_tables() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/backend-ci.yml").read_text()
    assert 'table.startswith("agent_") and table != "agent_batch_receipts"' in workflow


def test_pinned_minio_ci_builds_reuse_main_only_layer_cache_without_skipping_smoke() -> None:
    text = WORKFLOW.read_text()
    container = text.split("  container:\n", 1)[1].split("  postgres-migration:\n", 1)[0]
    object_storage = text.split("  object-storage-integration:\n", 1)[1]
    for section in (container, object_storage):
        build = section.split("      - name: Build the pinned community MinIO image\n", 1)[1].split("      - name:", 1)[0]
        for line in (
            "uses: docker/build-push-action@f2a1d5e99d037542a71f64918e516c093c6f3fc4",
            "context: deploy/shared",
            "file: deploy/shared/Minio.Dockerfile",
            "load: true",
            "push: false",
            "tags: momcozy-staging-minio:9e49d5e7a648f00e",
            "cache-from: type=gha,scope=momcozy-minio-9e49d5e7a648f00e",
        ):
            assert line in build
    assert "cache-to:" not in container
    assert "cache-to:" in object_storage
    assert "github.ref == 'refs/heads/main'" in object_storage
    assert text.count("uses: docker/setup-buildx-action@v3") == 2
    assert "Smoke-test migration-gated readiness" in container
    assert "Check object storage profile" in object_storage
