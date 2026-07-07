# Postgres Integration Profile

The default local test suite avoids requiring a running database. Production
readiness still needs a real Postgres migration profile.

## CI Profile

The `postgres-migration` CI job starts a Postgres service, installs backend
dependencies, and runs:

```bash
DATABASE_URL=postgresql+asyncpg://momcozy:momcozy@localhost:5432/momcozy_test \
python -m alembic -c production_backend/alembic.ini upgrade head
```

It then verifies key production tables exist through SQLAlchemy.

## Local Profile

When Docker is available:

```bash
docker compose -f production_backend/docker-compose.local.yml up -d postgres redis
DATABASE_URL=postgresql+asyncpg://momcozy:momcozy@localhost:5432/momcozy \
  python -m alembic -c production_backend/alembic.ini upgrade head
```

This profile is separate from the fast unit/contract suite so normal development
does not require local infrastructure.
