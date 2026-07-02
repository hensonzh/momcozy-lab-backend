from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "postgres-integration-profile.md"


def test_postgres_integration_profile_documents_ci_and_local_paths() -> None:
    text = DOC.read_text()

    for phrase in [
        "postgres-migration",
        "DATABASE_URL=postgresql+asyncpg://momcozy:momcozy@localhost:5432/momcozy_test",
        "alembic",
        "docker compose -f production_backend/docker-compose.yml up -d postgres redis",
        "does not require local infrastructure",
    ]:
        assert phrase in text
