from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_BACKEND = ROOT / "production_backend"
COMPOSE_LOCAL_ENV = PRODUCTION_BACKEND / "env" / "compose.local.env.example"
COMPOSE_TEST_ENV = PRODUCTION_BACKEND / "env" / "compose.test.env.example"
COMPOSE_PROD_ENV = PRODUCTION_BACKEND / "env" / "compose.prod.env.example"
LOCAL_COMPOSE = PRODUCTION_BACKEND / "docker-compose.local.yml"
TEST_COMPOSE = PRODUCTION_BACKEND / "docker-compose.test.yml"
PROD_COMPOSE = PRODUCTION_BACKEND / "docker-compose.prod.yml"


def test_dockerfile_runs_isolated_production_backend() -> None:
    dockerfile = (PRODUCTION_BACKEND / "Dockerfile").read_text()

    assert "python:3.13-slim" in dockerfile
    assert "production_backend/requirements.txt" in dockerfile
    assert "production_backend.app.main:app" in dockerfile
    assert "momcozy" + "_agent" not in dockerfile


def test_compose_uses_local_infra_service_names_not_localhost() -> None:
    compose = LOCAL_COMPOSE.read_text()
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "postgres:16" in compose
    assert "redis:7" in compose
    assert "minio/minio:latest" in compose
    assert "${MOMCOZY_BACKEND_ENV_FILE:-env/compose.local.env.example}" in compose
    assert "postgresql+asyncpg://momcozy:momcozy@postgres:5432/momcozy" in env
    assert "redis://redis:6379/0" in env
    assert "localhost" not in env
    assert "127.0.0.1" not in env


def test_compose_env_keeps_object_storage_switchable_by_environment() -> None:
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "OBJECT_STORAGE_PROVIDER=minio" in env
    assert "OBJECT_STORAGE_LOCAL_ROOT=" in env
    assert "OBJECT_STORAGE_BUCKET=momcozy-local" in env
    assert "OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000" in env
    assert "OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin" in env
    assert "OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin" in env
    assert "PRODUCT_ASSET_MANIFEST_PATH=/workspace/production_backend/assets/product-assets.manifest.json" in env
    assert "PRODUCT_ASSET_LOCAL_ROOT=" in env


def test_environment_profile_examples_exist_for_local_staging_and_production() -> None:
    env_dir = PRODUCTION_BACKEND / "env"

    assert (env_dir / "local.env.example").exists()
    assert (env_dir / "compose.local.env.example").exists()
    assert (env_dir / "compose.test.env.example").exists()
    assert (env_dir / "compose.prod.env.example").exists()
    assert (env_dir / "staging.env.example").exists()
    assert (env_dir / "production.env.example").exists()
    assert "APP_ENV=production" in (env_dir / "compose.prod.env.example").read_text()
    assert "APP_ENV=production" in (env_dir / "production.env.example").read_text()
    assert "OBJECT_STORAGE_PROVIDER=oss" in (env_dir / "production.env.example").read_text()


def test_makefile_infra_checks_use_project_python_environment() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "COMPOSE_ENV_FILE ?= production_backend/env/compose.local.env.example" in makefile
    assert "COMPOSE_ENV_FILE_FOR_COMPOSE = $(patsubst production_backend/%,%,$(COMPOSE_ENV_FILE))" in makefile
    assert "docker-compose.local.yml" in makefile
    assert "PYTHON ?= production_backend/.venv/bin/python" in makefile
    assert "$(COMPOSE) up -d postgres redis minio minio-init" in makefile
    assert "$(COMPOSE) --profile tools run --rm migrate" in makefile
    assert "$(COMPOSE) --profile workers up -d api agent-worker outbox-worker" in makefile
    assert "$(PYTHON) production_backend/scripts/check_database_profile.py" in makefile
    assert "$(PYTHON) production_backend/scripts/check_redis_runtime_controls.py" in makefile
    assert "$(PYTHON) production_backend/scripts/check_object_storage_profile.py" in makefile
    assert "$(PYTHON) production_backend/scripts/check_product_asset_storage.py" in makefile
    assert "backend-productization-status:" in makefile
    assert "$(PYTHON) production_backend/scripts/check_productization_status.py" in makefile
    assert "backend-smoke:" in makefile
    assert "backend-staging-smoke:" in makefile
    assert "$(MAKE) backend-check-infra BACKEND_ENV=staging" in makefile
    assert "backend-production-readiness:" in makefile
    assert "$(MAKE) backend-check-infra BACKEND_ENV=production" in makefile
    assert "backend-worker-backlog:" in makefile
    assert "$(PYTHON) production_backend/scripts/inspect_worker_backlog.py" in makefile
    assert "backend-agent-recover-stuck-runs:" in makefile
    assert "$(PYTHON) production_backend/scripts/recover_stuck_agent_runs.py" in makefile


def test_docker_context_excludes_product_asset_blobs_from_worker_images() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text()

    assert "production_backend/fixtures/product_assets" in dockerignore


def test_compose_env_declares_disabled_agent_worker_controls() -> None:
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "AGENT_RUNTIME_WORKER_ENABLED=false" in env
    assert "AGENT_RUNTIME_WORKER_BATCH_LIMIT=10" in env
    assert "AGENT_RUNTIME_WORKER_IDLE_SECONDS=2" in env
    assert "AGENT_RUNTIME_RECOVER_RUNNING_OLDER_THAN_SECONDS=900" in env
    assert "OPENAI_API_KEY=" in env
    assert "OPENAI_MODEL=gpt-5.5" in env
    assert "VOICE_PROVIDER=disabled" in env
    assert "VISION_PROVIDER=disabled" in env


def test_compose_env_declares_disabled_outbox_worker_controls() -> None:
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "OUTBOX_WORKER_ENABLED=false" in env
    assert "OUTBOX_WORKER_IDLE_SECONDS=2" in env
    assert "OUTBOX_WORKER_LEASE_SECONDS=60" in env


def test_compose_env_declares_active_session_auth_gate() -> None:
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "AUTH_REQUIRE_ACTIVE_SESSION=false" in env


def test_compose_exposes_minio_as_default_local_object_storage() -> None:
    compose = LOCAL_COMPOSE.read_text()

    assert "minio:" in compose
    minio_section = compose.split("  minio:", maxsplit=1)[1].split("\n  minio-init:", maxsplit=1)[0]
    assert "profiles:" not in minio_section
    assert "server /data --address" in compose
    assert "9000:9000" in compose
    assert "minio-init:" in compose
    assert "mc mb --ignore-existing local/momcozy-local" in compose


def test_compose_exposes_agent_worker_as_optional_worker_profile() -> None:
    compose = LOCAL_COMPOSE.read_text()

    assert "agent-worker:" in compose
    assert "python -m production_backend.scripts.run_agent_worker" in compose
    assert "workers" in compose


def test_compose_exposes_outbox_worker_as_optional_worker_profile() -> None:
    compose = LOCAL_COMPOSE.read_text()

    assert "outbox-worker:" in compose
    assert "python -m production_backend.scripts.run_outbox_worker" in compose
    assert "workers" in compose


def test_outbox_worker_waits_for_redis_because_agent_events_use_stream_cursor() -> None:
    compose = LOCAL_COMPOSE.read_text()
    outbox_worker_section = compose.split("outbox-worker:", maxsplit=1)[1].split("\n  postgres:", maxsplit=1)[0]

    assert "redis:" in outbox_worker_section
    assert "condition: service_healthy" in outbox_worker_section


def test_production_compose_only_starts_application_processes() -> None:
    compose = PROD_COMPOSE.read_text()

    assert "api:" in compose
    assert "migrate:" in compose
    assert "agent-worker:" in compose
    assert "outbox-worker:" in compose
    assert "\n  postgres:" not in compose
    assert "\n  redis:" not in compose
    assert "\n  minio:" not in compose
    assert "\n  minio-init:" not in compose
    assert "postgres:16" not in compose
    assert "redis:7" not in compose
    assert "minio/minio" not in compose


def test_production_compose_uses_production_env_and_safe_api_bind() -> None:
    compose = PROD_COMPOSE.read_text()
    env = COMPOSE_PROD_ENV.read_text()

    assert "${MOMCOZY_BACKEND_ENV_FILE:-env/compose.prod.env}" in compose
    assert "${MOMCOZY_BACKEND_IMAGE:-momcozy-production-backend:latest}" in compose
    assert "${MOMCOZY_API_BIND:-127.0.0.1:8000}:8000" in compose
    assert "python -m alembic -c production_backend/alembic.ini upgrade head" in compose
    assert "python -m production_backend.scripts.run_agent_worker" in compose
    assert "python -m production_backend.scripts.run_outbox_worker" in compose
    assert "restart: unless-stopped" in compose
    assert "stop_grace_period: 60s" in compose
    assert "APP_ENV=production" in env
    assert "OBJECT_STORAGE_PROVIDER=oss" in env
    assert "OUTBOX_WORKER_ENABLED=true" in env


def test_server_test_compose_starts_containerized_infrastructure_without_publishing_it() -> None:
    compose = TEST_COMPOSE.read_text()

    assert "postgres:" in compose
    assert "redis:" in compose
    assert "minio:" in compose
    assert "minio-init:" in compose
    assert "postgres:16" in compose
    assert "redis:7" in compose
    assert "minio/minio:latest" in compose
    assert "postgres_test_data:" in compose
    assert "redis_test_data:" in compose
    assert "minio_test_data:" in compose
    assert "5432:5432" not in compose
    assert "6379:6379" not in compose
    assert "9000:9000" not in compose


def test_server_test_compose_uses_test_env_and_safe_api_bind() -> None:
    compose = TEST_COMPOSE.read_text()
    env = COMPOSE_TEST_ENV.read_text()

    assert "${MOMCOZY_BACKEND_ENV_FILE:-env/compose.test.env}" in compose
    assert "${MOMCOZY_TEST_API_BIND:-127.0.0.1:8001}:8000" in compose
    assert "mc mb --ignore-existing test/momcozy-test" in compose
    assert "APP_ENV=test" in env
    assert "postgresql+asyncpg://momcozy_test:momcozy_test@postgres:5432/momcozy_test" in env
    assert "REDIS_URL=redis://redis:6379/0" in env
    assert "OBJECT_STORAGE_PROVIDER=minio" in env
    assert "OBJECT_STORAGE_BUCKET=momcozy-test" in env
    assert "OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000" in env
    assert "OUTBOX_WORKER_ENABLED=true" in env


def test_makefile_exposes_production_compose_release_targets() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "PROD_COMPOSE_ENV_FILE ?= production_backend/env/compose.prod.env" in makefile
    assert "docker-compose.prod.yml" in makefile
    assert "backend-prod-build:" in makefile
    assert "$(PROD_COMPOSE) build api agent-worker outbox-worker" in makefile
    assert "backend-prod-migrate:" in makefile
    assert "$(PROD_COMPOSE) --profile tools run --rm migrate" in makefile
    assert "backend-prod-up:" in makefile
    assert "$(PROD_COMPOSE) up -d api agent-worker outbox-worker" in makefile
    assert "backend-prod-services:" in makefile
    assert "backend-prod-logs:" in makefile


def test_makefile_exposes_server_test_compose_targets() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "TEST_COMPOSE_ENV_FILE ?= production_backend/env/compose.test.env" in makefile
    assert "docker-compose.test.yml" in makefile
    assert "backend-test-build:" in makefile
    assert "$(TEST_COMPOSE) build api agent-worker outbox-worker" in makefile
    assert "backend-test-migrate:" in makefile
    assert "$(TEST_COMPOSE) --profile tools run --rm migrate" in makefile
    assert "backend-test-up:" in makefile
    assert "$(TEST_COMPOSE) up -d postgres redis minio minio-init" in makefile
    assert "$(TEST_COMPOSE) up -d api agent-worker outbox-worker" in makefile
    assert "backend-test-services:" in makefile
    assert "backend-test-logs:" in makefile
