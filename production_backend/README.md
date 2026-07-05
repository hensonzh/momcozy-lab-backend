# MomCozy Production Backend

This directory is the isolated workspace for the production backend refactor.

The legacy backend has been moved under the repository-level `legacy_backend/`
directory. New production code should be built here first so the target
architecture can evolve without inheriting the legacy agent loop, in-memory
session, SQLite data store, or static API-key user boundary.

## Boundary Rules

- New production FastAPI code lives under `production_backend/app/`.
- New Alembic migrations live under `production_backend/migrations/`.
- New backend tests live under `production_backend/tests/`.
- Refactor inventories, ADRs, and runbooks live under `production_backend/docs/`.
- Development and migration scripts live under `production_backend/scripts/`.
- The old demo backend, old tests, old scripts, old skill prompts, and local
  legacy runtime artifacts live under repository-level `legacy_backend/`.
- Do not import legacy runtime modules from
  `legacy_backend/src/momcozy_agent/` in production code. The production backend
  has no legacy bridge fallback; migrate behavior into explicit modules,
  services, repositories, tools, and tests instead.
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
- `VOICE_PROVIDER`
- `VISION_PROVIDER`

Use `production_backend/env/local.env.example` as the host-local template and
copy it to ignored `production_backend/env/local.env` for secrets. Secrets stay
out of git, and production startup rejects local object storage. Backup/restore
hook values should be references to external automation, not raw credentials.

## Local Docker Compose

The isolated backend can run with local Postgres, Redis, and filesystem-backed
object storage:

```bash
cd production_backend
docker compose --profile tools run --rm migrate
docker compose up api
```

`docker-compose.yml` reads `env/compose.local.env.example` by default, which
intentionally points to compose service hostnames such as `postgres` and
`redis`. Production deployments should provide managed `DATABASE_URL`,
`REDIS_URL`, and managed `OBJECT_STORAGE_*` values through environment
variables; no code change is required to switch providers.

Agent runs are processed by a separate worker process, not by the API lifespan.
The compose `agent-worker` service is behind the `workers` profile and remains
safe by default because `AGENT_RUNTIME_WORKER_ENABLED=false` in
`env/compose.local.env.example`. Enable it only in an environment where the
LangGraph / OpenAI Agents SDK runtime handler is configured. `OPENAI_API_KEY`
must be set when this worker is enabled, and `OPENAI_MODEL` controls the SDK
agent model:

```bash
AGENT_RUNTIME_WORKER_ENABLED=true docker compose --profile workers up agent-worker
```

Voice endpoints are exposed in the production contract, but speech provider
integration is disabled by default. Keep `VOICE_PROVIDER=disabled` until a
managed provider adapter is configured; `VOICE_PROVIDER=local_stub` is only for
local/test contract checks and is rejected in production.

Vision event endpoints are exposed in the production contract, but image
analysis provider integration is disabled by default. Keep
`VISION_PROVIDER=disabled` until a managed provider adapter is configured;
`VISION_PROVIDER=local_stub` is only for local/test contract checks and is
rejected in production.

Durable side effects are processed by a separate outbox worker. It handles file
cleanup jobs and confirmed agent actions, and is also disabled by default:

```bash
OUTBOX_WORKER_ENABLED=true docker compose --profile workers up outbox-worker
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

legacy_backend/
  src/
  skills/
  tests/
  scripts/
  web_data/
```

Agent runtime is a domain module with explicit internal subdomains:

```text
app/modules/agent_runtime/
  router.py
  service.py
  repository.py
  models.py
  schemas.py
  actions/
  event_stream/
  run_lifecycle/
  memory/
  evals/
  safety/
  graphs/
  prompts/
  routing/
  sdk/
  tools/
```

## First Milestone

The first milestone is not feature migration. It is the production shell:

1. Create the app factory and health endpoints.
2. Add typed settings and request/error primitives.
3. Inventory legacy API, DB, tools, events, and side effects.
4. Add baseline contract tests before moving behavior.
