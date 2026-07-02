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


def test_compose_env_declares_disabled_agent_worker_controls() -> None:
    env = (PRODUCTION_BACKEND / "compose.env.example").read_text()

    assert "AGENT_RUNTIME_WORKER_ENABLED=false" in env
    assert "AGENT_RUNTIME_WORKER_BATCH_LIMIT=10" in env
    assert "AGENT_RUNTIME_WORKER_IDLE_SECONDS=2" in env
    assert "AGENT_RUNTIME_RECOVER_RUNNING_OLDER_THAN_SECONDS=900" in env
    assert "OPENAI_API_KEY=" in env
    assert "OPENAI_MODEL=gpt-5.5" in env


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
