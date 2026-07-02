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
- `AUTH_JWT_SECRET`
- `AUTH_JWT_ISSUER`
- `AUTH_JWT_AUDIENCE`
- `POSTGRES_BACKUP_HOOK`
- `POSTGRES_RESTORE_HOOK`
- `OBJECT_STORAGE_BACKUP_HOOK`
- `OBJECT_STORAGE_RESTORE_HOOK`

Use `production_backend/.env.example` as the local template. Secrets stay out of
git, and production startup rejects local object storage. Backup/restore hook
values should be references to external automation, not raw credentials.

## Local Docker Compose

The isolated backend can run with local Postgres, Redis, and filesystem-backed
object storage:

```bash
cd production_backend
docker compose --profile tools run --rm migrate
docker compose up api
```

`docker-compose.yml` reads `compose.env.example`, which intentionally points to
compose service hostnames such as `postgres` and `redis`. Production deployments
should provide managed `DATABASE_URL`, `REDIS_URL`, and managed
`OBJECT_STORAGE_*` values through environment variables; no code change is
required to switch providers.

Agent runs are processed by a separate worker process, not by the API lifespan.
The compose `agent-worker` service is behind the `workers` profile and remains
safe by default because `AGENT_RUNTIME_WORKER_ENABLED=false` in
`compose.env.example`. Enable it only in an environment where the LangGraph /
OpenAI Agents SDK runtime handler is configured:

```bash
AGENT_RUNTIME_WORKER_ENABLED=true docker compose --profile workers up agent-worker
```

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
