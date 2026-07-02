# MomCozy Production Backend

This directory is the isolated workspace for the production backend refactor.

The legacy backend remains under `src/momcozy_agent/`. New production code should
be built here first so the target architecture can evolve without inheriting the
legacy agent loop, in-memory session, SQLite data store, or static API-key user
boundary.

## Boundary Rules

- New production FastAPI code lives under `production_backend/app/`.
- New Alembic migrations live under `production_backend/migrations/`.
- New backend tests live under `production_backend/tests/`.
- Refactor inventories, ADRs, and runbooks live under `production_backend/docs/`.
- Development and migration scripts live under `production_backend/scripts/`.
- Do not import legacy runtime modules from `src/momcozy_agent/` in production
  code. If a temporary bridge is unavoidable, put it under
  `production_backend/legacy_bridge/` with an owner, removal condition, and
  deletion PR.
- Do not use `previous_response_id`, provider session, in-memory `ChatSession`,
  or the legacy Responses API loop as a production fallback.

## Environment Contract

Local development uses local Postgres, Redis, and filesystem-backed object
storage by default. Staging and production must switch infrastructure through
environment variables, not code changes:

- `DATABASE_URL`
- `REDIS_URL`
- `OBJECT_STORAGE_PROVIDER`
- `OBJECT_STORAGE_BUCKET`
- `OBJECT_STORAGE_REGION`
- `OBJECT_STORAGE_ENDPOINT_URL`
- `OBJECT_STORAGE_ACCESS_KEY_ID`
- `OBJECT_STORAGE_SECRET_ACCESS_KEY`

Use `production_backend/.env.example` as the local template. Secrets stay out of
git, and production startup rejects local object storage.

## Target Shape

```text
production_backend/
  app/
    main.py
    factory.py
    core/
    api/
    modules/
    infrastructure/
    workers/
  migrations/
  tests/
  docs/
  scripts/
  legacy_bridge/
```

## First Milestone

The first milestone is not feature migration. It is the production shell:

1. Create the app factory and health endpoints.
2. Add typed settings and request/error primitives.
3. Inventory legacy API, DB, tools, events, and side effects.
4. Add baseline contract tests before moving behavior.
