# MomCozy Production Backend

This directory contains the MomCozy production backend. It is the only backend
runtime maintained in this repository.

## Boundary Rules

- New production FastAPI code lives under `app/`.
- New Alembic migrations live under `migrations/`.
- New backend tests live under `tests/`.
- Current contracts, architecture conventions, and runbooks live under
  `docs/`.
- Development and migration scripts live under `scripts/`.
- Do not use `previous_response_id`, provider session, in-memory `ChatSession`,
  or a provider-managed conversation loop as a production fallback.

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
- `OBJECT_STORAGE_PUBLIC_ENDPOINT_URL`
- `OBJECT_STORAGE_ACCESS_KEY_ID`
- `OBJECT_STORAGE_SECRET_ACCESS_KEY`
- `AGENT_IMAGE_SIGNED_URL_TTL_SECONDS`
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
- `AGENT_FACT_EXTRACTION_ENABLED`
- `AGENT_FACT_EXTRACTION_MODEL`
- `AGENT_FACT_EXTRACTION_TIMEOUT_SECONDS`
- `AGENT_FACT_EXTRACTION_VERSION`
- `AGENT_FACT_WORKER_CONCURRENCY`
- `AGENT_FACT_WORKER_BATCH_LIMIT`
- `AGENT_FACT_WORKER_IDLE_SECONDS`
- `AGENT_FACT_WORKER_LEASE_SECONDS`
- `AGENT_FACT_WORKER_MAX_ATTEMPTS`

Use the `env/compose.*.env.example` files as the only
committed environment templates. Copy them to ignored private files such as
`env/compose.local.env`, `env/compose.test.env`, or `env/compose.prod.env` for
real secrets. Secrets stay out of git, and production startup rejects local
object storage. Backup/restore hook values should be references to external
automation, not raw credentials.

## Local Docker Compose

The isolated backend can run with Docker Compose Postgres, Redis, and MinIO:

```bash
cd /path/to/MomCozyAgent
cp env/compose.local.env.example env/compose.local.env
make backend-local-up
```

`docker-compose.local.yml` reads `env/compose.local.env` by default. The local
env should keep compose service hostnames such as `postgres` and `redis`, plus
`http://minio:9000` for S3-compatible object storage. Production deployments
should provide managed `DATABASE_URL`, `REDIS_URL`, and managed
`OBJECT_STORAGE_*` values through environment variables; no code change is
required to switch providers.

Agent image messages use a stable internal `asset_id`; Base64 image bytes and
signed URLs are never stored in the model conversation. Before the first run
for an image, the backend creates one HTTPS signed-URL binding for the
`thread_id + asset_id` pair and reuses the exact URL until it expires. Configure
`OBJECT_STORAGE_PUBLIC_ENDPOINT_URL` with an OpenAI-reachable HTTPS object
storage host. `AGENT_IMAGE_SIGNED_URL_TTL_SECONDS` defaults to `604800` (seven
days, the S3-compatible presign ceiling); after expiry, the next run rotates the
binding once and then reuses the new URL.

Agent runs are processed by a separate worker process, not by the API lifespan.
`make backend-local-up` first builds the local `migrate`, `api`,
`agent-worker`, and `memory-worker` images from the current source tree, then
starts infrastructure, runs Alembic migrations, and starts the three runtime
services with recreated containers. The worker processes still run as separate
Compose services, so they can be restarted or scaled independently. For a fully
uncached rebuild, run `BACKEND_BUILD_FLAGS=--no-cache make backend-local-up`.

`agent-worker` remains safe in the committed template because
`AGENT_RUNTIME_WORKER_ENABLED=false` in `env/compose.local.env.example`. The
local `agent-worker` Compose service enables the worker when the `workers`
profile is started, so your private `env/compose.local.env` must include the
configured OpenAI credentials. When the worker is enabled, `OPENAI_API_KEY`
must be set. The main runtime uses `gpt-5.6-terra` with low reasoning,
does not retain provider-side Responses state, and uses the native Responses
runner. Namespace/deferred tool loading uses hosted `tool_search` while keeping
the declared tool surface stable between model turns. The quick reply finalizer
uses `gpt-5.4-nano` separately with reasoning disabled. Long-term memory writes
are not part of the live agent loop: `memory-worker` uses the same lightweight
model to consolidate the previous local day's completed conversations into a
small runtime snapshot.

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING_EFFORT=low
OPENAI_RESPONSES_STORE=false
AGENT_QUICK_REPLY_MODEL=gpt-5.4-nano
AGENT_QUICK_REPLY_TIMEOUT_SECONDS=3.0
AGENT_FACT_EXTRACTION_ENABLED=true
AGENT_FACT_EXTRACTION_MODEL=gpt-5.4-nano
AGENT_FACT_EXTRACTION_TIMEOUT_SECONDS=5.0
AGENT_FACT_EXTRACTION_VERSION=turn-fact-extractor-v2
AGENT_FACT_WORKER_CONCURRENCY=2
AGENT_FACT_WORKER_BATCH_LIMIT=10
AGENT_FACT_WORKER_IDLE_SECONDS=0.5
AGENT_FACT_WORKER_LEASE_SECONDS=30
AGENT_FACT_WORKER_MAX_ATTEMPTS=3
AGENT_MEMORY_CONSOLIDATION_MODEL=gpt-5.4-nano
AGENT_MEMORY_CONSOLIDATION_TIMEZONE=Asia/Shanghai
AGENT_MEMORY_CONSOLIDATION_HOUR=3
```

Voice playback uses the Doubao/Volcengine realtime TTS path. Use
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
`docs/vision-provider-integration.md`.

File deletion removes the owner-scoped object synchronously in the API request.
Agent action writes execute synchronously inside the Agent worker run. Enable
the Agent worker when local Agent runs are required:

```env
AGENT_RUNTIME_WORKER_ENABLED=true
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING_EFFORT=low
OPENAI_RESPONSES_STORE=false
AGENT_QUICK_REPLY_MODEL=gpt-5.4-nano
AGENT_QUICK_REPLY_TIMEOUT_SECONDS=3.0
AGENT_FACT_EXTRACTION_ENABLED=true
AGENT_FACT_EXTRACTION_MODEL=gpt-5.4-nano
AGENT_FACT_EXTRACTION_TIMEOUT_SECONDS=5.0
AGENT_FACT_EXTRACTION_VERSION=turn-fact-extractor-v2
AGENT_FACT_WORKER_CONCURRENCY=2
AGENT_FACT_WORKER_BATCH_LIMIT=10
AGENT_FACT_WORKER_IDLE_SECONDS=0.5
AGENT_FACT_WORKER_LEASE_SECONDS=30
AGENT_FACT_WORKER_MAX_ATTEMPTS=3
AGENT_MEMORY_CONSOLIDATION_MODEL=gpt-5.4-nano
AGENT_MEMORY_CONSOLIDATION_TIMEZONE=Asia/Shanghai
AGENT_MEMORY_CONSOLIDATION_HOUR=3
```

The independent memory worker runs once at startup (idempotently) and then at
the configured local hour. To inspect or backfill one local date without
starting the scheduler:

```bash
python -m scripts.run_memory_consolidation --once --date 2026-07-10
```

## Production Docker Compose

Use `docker-compose.prod.yml` on a server when Postgres,
Redis, and object storage are managed outside the compose project. The production
compose starts `api`, `agent-worker`, and `memory-worker`; the one-time
`migrate` service is available through the `tools` profile.

```bash
cp env/compose.prod.env.example env/compose.prod.env
make backend-prod-up
```

`backend-prod-up` builds local runtime images, runs Alembic migrations, and then
starts the three runtime services with recreated containers. Use
`backend-prod-services` for restarts that should not run migrations again; it
still builds runtime images before restart. See
`docs/deployment-runbook.md` for reverse proxy, scaling, and
release details.

## Server Test Docker Compose

Use `docker-compose.test.yml` when deploying a test
environment on a server and you want DB, Redis, and MinIO to run as Docker
containers. This is separate from production because it starts containerized
infrastructure and stores data in compose volumes.

```bash
cp env/compose.test.env.example env/compose.test.env
make backend-test-up
```

`backend-test-up` builds local runtime images, starts `postgres`, `redis`,
`minio`, initializes the test bucket, runs Alembic migrations, and then starts
`api`, `agent-worker`, and `memory-worker` with recreated containers. The API
binds to `127.0.0.1:8001` by default so a reverse proxy can expose a test domain
without exposing DB/Redis/MinIO ports.

## Target Shape

```text
MomCozyAgent/
  app/
    main.py
    factory.py
    agent_runtime/
    agents/
    core/
    api/
    modules/
    infrastructure/
    workers/
  migrations/
  tests/
  docs/
  scripts/
```

Agent runtime is the shared execution layer. Concrete agent behavior lives under `app/agents/`:

```text
app/agents/
  cozymate/
    actions/
    context/
    prompts/
    skills/
    tools/
    workflows/
    executor.py
    factory.py

app/agent_runtime/
  actions/
  api/
  context/
  evals/
  events/
  providers/
  runs/
  tools/
```

Dependency direction is one-way: concrete agents may depend on runtime primitives and business services; runtime never imports a concrete agent or product domain. Product policies and handler wiring are owned by `app/agents/cozymate/factory.py`. `app/workers/` owns only process execution, while scripts remain thin launchers.
