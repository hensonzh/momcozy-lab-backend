# MomCozy Production Backend

This directory contains the MomCozy production backend.

The legacy backend has been moved under the repository-level `legacy_backend/`
directory. New production code should be built here first so the target
architecture can evolve without inheriting the legacy agent loop, in-memory
session, SQLite data store, or static API-key user boundary.

## Boundary Rules

- New production FastAPI code lives under `production_backend/app/`.
- New Alembic migrations live under `production_backend/migrations/`.
- New backend tests live under `production_backend/tests/`.
- Current contracts, architecture conventions, and runbooks live under
  `production_backend/docs/`.
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

Local development uses Docker Compose Postgres, Redis, and MinIO-backed object
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
- `VOICE_API_KEY`
- `VOICE_BASE_URL`
- `VOICE_TRANSCRIBE_MODEL`
- `VOICE_TTS_RESOURCE_ID`
- `VOICE_TTS_VOICE_TYPE`
- `VOICE_TTS_AUDIO_FORMAT`
- `VOICE_TTS_SAMPLE_RATE`
- `VOICE_TTS_SPEED_RATIO`
- `VOICE_TTS_FIRST_CHUNK_TIMEOUT_SECONDS`
- `VOICE_REALTIME_MODEL`
- `VOICE_REQUEST_TIMEOUT_SECONDS`
- `VISION_PROVIDER`
- `VISION_OPENAI_MODEL`
- `VISION_REQUEST_TIMEOUT_SECONDS`

Use the `production_backend/env/compose.*.env.example` files as the only
committed environment templates. Copy them to ignored private files such as
`env/compose.local.env`, `env/compose.test.env`, or `env/compose.prod.env` for
real secrets. Secrets stay out of git, and production startup rejects local
object storage. Backup/restore hook values should be references to external
automation, not raw credentials.

## Local Docker Compose

The isolated backend can run with Docker Compose Postgres, Redis, and MinIO:

```bash
cd /path/to/MomCozyAgent
cp production_backend/env/compose.local.env.example production_backend/env/compose.local.env
make backend-local-up
```

`docker-compose.local.yml` reads `env/compose.local.env` by default. The local
env should keep compose service hostnames such as `postgres` and `redis`, plus
`http://minio:9000` for S3-compatible object storage. Production deployments
should provide managed `DATABASE_URL`, `REDIS_URL`, and managed
`OBJECT_STORAGE_*` values through environment variables; no code change is
required to switch providers.

Agent runs are processed by a separate worker process, not by the API lifespan.
`make backend-local-up` first builds the local `migrate`, `api`,
`agent-worker`, and `outbox-worker` images from the current source tree, then
starts infrastructure, runs Alembic migrations, and starts the four runtime
services with recreated containers. The worker processes still run as separate
Compose services, so they can be restarted or scaled independently. For a fully
uncached rebuild, run `BACKEND_BUILD_FLAGS=--no-cache make backend-local-up`.

`agent-worker` remains safe in the committed template because
`AGENT_RUNTIME_WORKER_ENABLED=false` in `env/compose.local.env.example`. The
local `agent-worker` Compose service enables the worker when the `workers`
profile is started, so your private `env/compose.local.env` must include the
configured model provider credentials. `AGENT_MODEL_PROVIDER=openai` is the
default. When the worker is enabled with the default provider, `OPENAI_API_KEY`
must be set. The default main runtime uses `gpt-5.6-terra` with low reasoning,
does not retain provider-side Responses state, and uses the native Responses
runner. Namespace/deferred tool loading uses hosted `tool_search` while keeping
the declared tool surface stable between model turns. The quick reply finalizer
uses `gpt-5.4-nano` separately with reasoning disabled. Long-term memory writes
are not part of the live agent loop: `memory-worker` uses the same lightweight
model to consolidate the previous local day's completed conversations into a
small runtime snapshot.

```env
AGENT_MODEL_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING_EFFORT=low
OPENAI_RESPONSES_STORE=false
OPENAI_AGENT_USE_RESPONSES=true
AGENT_QUICK_REPLY_MODEL=gpt-5.4-nano
AGENT_QUICK_REPLY_TIMEOUT_SECONDS=3.0
AGENT_MEMORY_CONSOLIDATION_MODEL=gpt-5.4-nano
AGENT_MEMORY_CONSOLIDATION_TIMEZONE=Asia/Shanghai
AGENT_MEMORY_CONSOLIDATION_HOUR=3
```

`OPENAI_AGENT_USE_RESPONSES=false` selects the temporary OpenAI Agents SDK
rollback path and should not be used for normal local or production traffic.

Minimax is available as an experimental OpenAI-compatible provider. To test it,
set these values in a private env file and run the provider eval before using it
for production traffic:

```env
AGENT_MODEL_PROVIDER=minimax
MINIMAX_API_KEY=...
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M3
```

The Minimax path uses the OpenAI Agents SDK with an OpenAI-compatible provider
base URL and currently pins the SDK model path to Chat Completions compatibility
for flat tool calling and streaming validation; it does not advertise deferred
tool loading in runtime metadata.

Voice playback follows the legacy Doubao/Volcengine realtime TTS path. Use
`VOICE_PROVIDER=doubao`, set `VOICE_API_KEY`, and keep the default bidirectional
TTS endpoint/resource/voice values unless the provider account changes. The App
requests `/v1/realtime-voice-stream` and plays PCM chunks locally. `local_stub`
is only for local/test contract checks and is rejected in production.

Vision event endpoints are exposed in the production contract and remain
disabled by default. Set `VISION_PROVIDER=openai` to use the existing
`OPENAI_API_KEY` with the Responses image-input/structured-output adapter;
`VISION_OPENAI_MODEL` and `VISION_REQUEST_TIMEOUT_SECONDS` are independent from
the live Agent model controls. Requests always use `store=false`. Keep
`VISION_PROVIDER=disabled` until production credentials and a curated screenshot
quality smoke pass are ready. `VISION_PROVIDER=local_stub` is deterministic,
local/test-only, and rejected in production. See
`production_backend/docs/vision-provider-integration.md`.

Durable file-object cleanup jobs are processed by the separate outbox worker.
Agent action writes execute synchronously inside the Agent worker run and never
use that outbox. Both workers are disabled by default in the example env; enable
the Agent worker for Agent runs and the outbox worker when local file cleanup is
also required:

```env
AGENT_RUNTIME_WORKER_ENABLED=true
OUTBOX_WORKER_ENABLED=true
AGENT_MODEL_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING_EFFORT=low
OPENAI_RESPONSES_STORE=false
OPENAI_AGENT_USE_RESPONSES=true
AGENT_QUICK_REPLY_MODEL=gpt-5.4-nano
AGENT_QUICK_REPLY_TIMEOUT_SECONDS=3.0
AGENT_MEMORY_CONSOLIDATION_MODEL=gpt-5.4-nano
AGENT_MEMORY_CONSOLIDATION_TIMEZONE=Asia/Shanghai
AGENT_MEMORY_CONSOLIDATION_HOUR=3
```

The independent memory worker runs once at startup (idempotently) and then at
the configured local hour. To inspect or backfill one local date without
starting the scheduler:

```bash
python -m production_backend.scripts.run_memory_consolidation --once --date 2026-07-10
```

## Production Docker Compose

Use `production_backend/docker-compose.prod.yml` on a server when Postgres,
Redis, and object storage are managed outside the compose project. The production
compose starts `api`, `agent-worker`, `outbox-worker`, and `memory-worker`; the one-time
`migrate` service is available through the `tools` profile.

```bash
cp production_backend/env/compose.prod.env.example production_backend/env/compose.prod.env
make backend-prod-up
```

`backend-prod-up` builds local runtime images, runs Alembic migrations, and then
starts the four runtime services with recreated containers. Use
`backend-prod-services` for restarts that should not run migrations again; it
still builds runtime images before restart. See
`production_backend/docs/deployment-runbook.md` for reverse proxy, scaling, and
release details.

## Server Test Docker Compose

Use `production_backend/docker-compose.test.yml` when deploying a test
environment on a server and you want DB, Redis, and MinIO to run as Docker
containers. This is separate from production because it starts containerized
infrastructure and stores data in compose volumes.

```bash
cp production_backend/env/compose.test.env.example production_backend/env/compose.test.env
make backend-test-up
```

`backend-test-up` builds local runtime images, starts `postgres`, `redis`,
`minio`, initializes the test bucket, runs Alembic migrations, and then starts
`api`, `agent-worker`, and `outbox-worker` with recreated containers. The API
binds to `127.0.0.1:8001` by default so a reverse proxy can expose a test domain
without exposing DB/Redis/MinIO ports.

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
