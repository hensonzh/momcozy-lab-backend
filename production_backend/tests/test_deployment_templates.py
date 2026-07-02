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
