from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_BACKEND = ROOT / "production_backend"


def test_dockerfile_runs_isolated_production_backend() -> None:
    dockerfile = (PRODUCTION_BACKEND / "Dockerfile").read_text()

    assert "python:3.13-slim" in dockerfile
    assert "production_backend/requirements.txt" in dockerfile
    assert "production_backend.app.main:app" in dockerfile
    assert "momcozy" + "_agent" not in dockerfile


def test_compose_uses_local_infra_service_names_not_localhost() -> None:
    compose = (PRODUCTION_BACKEND / "docker-compose.yml").read_text()
    env = (PRODUCTION_BACKEND / "compose.env.example").read_text()

    assert "postgres:16" in compose
    assert "redis:7" in compose
    assert "minio/minio:latest" in compose
    assert "${MOMCOZY_BACKEND_ENV_FILE:-compose.env.example}" in compose
    assert "postgresql+asyncpg://momcozy:momcozy@postgres:5432/momcozy" in env
    assert "redis://redis:6379/0" in env
    assert "localhost" not in env
    assert "127.0.0.1" not in env


def test_compose_env_keeps_object_storage_switchable_by_environment() -> None:
    env = (PRODUCTION_BACKEND / "compose.env.example").read_text()

    assert "OBJECT_STORAGE_PROVIDER=local" in env
    assert "OBJECT_STORAGE_LOCAL_ROOT=/workspace/production_backend/.local/object_storage" in env
    assert "OBJECT_STORAGE_ENDPOINT_URL=" in env
    assert "OBJECT_STORAGE_ACCESS_KEY_ID=" in env
    assert "OBJECT_STORAGE_SECRET_ACCESS_KEY=" in env
    assert "PRODUCT_ASSET_MANIFEST_PATH=/workspace/production_backend/assets/product-assets.manifest.json" in env
    assert "PRODUCT_ASSET_LOCAL_ROOT=" in env


def test_environment_profile_examples_exist_for_local_staging_and_production() -> None:
    env_dir = PRODUCTION_BACKEND / "env"

    assert (env_dir / "local.env.example").exists()
    assert (env_dir / "compose.local.env.example").exists()
    assert (env_dir / "staging.env.example").exists()
    assert (env_dir / "production.env.example").exists()
    assert "APP_ENV=production" in (env_dir / "production.env.example").read_text()
    assert "OBJECT_STORAGE_PROVIDER=oss" in (env_dir / "production.env.example").read_text()


def test_makefile_infra_checks_use_project_python_environment() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "PYTHON ?= .venv/bin/python" in makefile
    assert "$(PYTHON) production_backend/scripts/check_database_profile.py" in makefile
    assert "$(PYTHON) production_backend/scripts/check_redis_runtime_controls.py" in makefile
    assert "$(PYTHON) production_backend/scripts/check_object_storage_profile.py" in makefile
    assert "backend-productization-status:" in makefile
    assert "$(PYTHON) production_backend/scripts/check_productization_status.py" in makefile
    assert "backend-smoke:" in makefile
    assert "backend-staging-smoke:" in makefile
    assert "$(MAKE) backend-check-infra BACKEND_ENV=staging" in makefile
    assert "backend-production-readiness:" in makefile
    assert "$(MAKE) backend-check-infra BACKEND_ENV=production" in makefile


def test_docker_context_excludes_product_asset_blobs_from_worker_images() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text()

    assert "production_backend/fixtures/product_assets" in dockerignore


def test_compose_env_declares_disabled_agent_worker_controls() -> None:
    env = (PRODUCTION_BACKEND / "compose.env.example").read_text()

    assert "AGENT_RUNTIME_WORKER_ENABLED=false" in env
    assert "AGENT_RUNTIME_WORKER_BATCH_LIMIT=10" in env
    assert "AGENT_RUNTIME_WORKER_IDLE_SECONDS=2" in env
    assert "AGENT_RUNTIME_RECOVER_RUNNING_OLDER_THAN_SECONDS=900" in env
    assert "OPENAI_API_KEY=" in env
    assert "OPENAI_MODEL=gpt-5.5" in env
    assert "VOICE_PROVIDER=disabled" in env
    assert "VISION_PROVIDER=disabled" in env


def test_compose_env_declares_disabled_outbox_worker_controls() -> None:
    env = (PRODUCTION_BACKEND / "compose.env.example").read_text()

    assert "OUTBOX_WORKER_ENABLED=false" in env
    assert "OUTBOX_WORKER_IDLE_SECONDS=2" in env
    assert "OUTBOX_WORKER_LEASE_SECONDS=60" in env


def test_compose_env_declares_active_session_auth_gate() -> None:
    env = (PRODUCTION_BACKEND / "compose.env.example").read_text()

    assert "AUTH_REQUIRE_ACTIVE_SESSION=false" in env


def test_compose_exposes_minio_as_optional_tools_profile() -> None:
    compose = (PRODUCTION_BACKEND / "docker-compose.yml").read_text()

    assert "minio:" in compose
    assert "profiles:" in compose
    assert "server /data --address" in compose
    assert "9000:9000" in compose


def test_compose_exposes_agent_worker_as_optional_worker_profile() -> None:
    compose = (PRODUCTION_BACKEND / "docker-compose.yml").read_text()

    assert "agent-worker:" in compose
    assert "python -m production_backend.scripts.run_agent_worker" in compose
    assert "workers" in compose


def test_compose_exposes_outbox_worker_as_optional_worker_profile() -> None:
    compose = (PRODUCTION_BACKEND / "docker-compose.yml").read_text()

    assert "outbox-worker:" in compose
    assert "python -m production_backend.scripts.run_outbox_worker" in compose
    assert "workers" in compose


def test_outbox_worker_waits_for_redis_because_agent_events_use_stream_cursor() -> None:
    compose = (PRODUCTION_BACKEND / "docker-compose.yml").read_text()
    outbox_worker_section = compose.split("outbox-worker:", maxsplit=1)[1].split("\n  postgres:", maxsplit=1)[0]

    assert "redis:" in outbox_worker_section
    assert "condition: service_healthy" in outbox_worker_section
