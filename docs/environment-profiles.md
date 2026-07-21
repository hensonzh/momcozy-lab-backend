# Environment Profiles

The backend is configured by environment variables. Keep real secrets outside
the repository and copy the example files into your deployment secret manager or
local `.env` files.

## Profiles

| Profile | File | Purpose |
|---|---|---|
| Local compose | `env/compose.local.env` | Run API and workers with `docker-compose.local.yml`; service hosts are `postgres`, `redis`, and `minio`. Copy it from `env/compose.local.env.example`. |
| Server test compose | `env/compose.test.env.example` | Run API, workers, Postgres, Redis, and MinIO with `docker-compose.test.yml` on a test server. |
| Production compose | `env/compose.prod.env.example` | Run API and workers with `docker-compose.prod.yml`; managed Postgres, Redis, and OSS/S3 are supplied through env. |

## Common Commands

```bash
make backend-local-up
make backend-check-infra
```

`backend-local-up` first builds `migrate`, `api`, `agent-worker`, and
`memory-worker` from the current source tree, then starts local infrastructure,
runs migrations, and starts the API plus both workers with recreated
containers. `backend-local-migrate` and `backend-local-workers` remain available
for explicit maintenance, retries, and debugging; they also build the relevant
runtime image before running. Use `BACKEND_BUILD_FLAGS=--no-cache` when you want
to bypass the Docker build cache completely.

`backend-check-infra` loads `BACKEND_ENV_FILE`, then runs database, Redis, and
object storage diagnostics against the configured services.

It checks:

- PostgreSQL connectivity with `select 1`.
- Redis agent runtime controls, stream cursor, cancel flag, and lock semantics.
- Object storage `put_bytes`, `get_bytes`, and `delete` semantics.

## Production Rules

When `APP_ENV=production`, startup validation rejects implicit localhost
Postgres or Redis URLs, rejects local filesystem object storage, and requires
managed object storage bucket, credentials, service key, JWT secret, and trusted
hosts.

The production compose file starts only application processes. Postgres, Redis,
and object storage are selected through `compose.prod.env` and should point at
managed infrastructure.

## Environment Switching

All environment-specific infrastructure is selected through variables:

```env
DATABASE_URL=...
REDIS_URL=...
OBJECT_STORAGE_PROVIDER=minio|s3|oss|cos
OBJECT_STORAGE_BUCKET=...
OBJECT_STORAGE_ENDPOINT_URL=...
OBJECT_STORAGE_ACCESS_KEY_ID=...
OBJECT_STORAGE_SECRET_ACCESS_KEY=...
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
VISION_PROVIDER=disabled|openai
VISION_OPENAI_MODEL=gpt-5.4-mini
VISION_REQUEST_TIMEOUT_SECONDS=20
```

The Agent runtime uses the native OpenAI Responses runner exclusively. `OPENAI_RESPONSES_STORE=false`
keeps conversation authority in the application database, while the runtime
round-trips required response and reasoning items within the active run.

The main agent uses `OPENAI_MODEL` and `OPENAI_REASONING_EFFORT`. Quick replies
use `AGENT_QUICK_REPLY_MODEL` as a separate lightweight finalizer and do not
persist to the message database. Turn-level form-prefill fact extraction is a
separate OpenAI Responses lane inside `agent-worker` and requires
`OPENAI_API_KEY`. Its lease must remain greater than its model request timeout.
Long-term memory extraction is also outside
the live run path: the independent `memory-worker` reads only completed
conversations for the previous local day, uses
`AGENT_MEMORY_CONSOLIDATION_MODEL`, and publishes a bounded database snapshot
that the live worker reads with one primary-key query.

The vision adapter is independent from the Agent worker. `VISION_PROVIDER=openai`
reuses `OPENAI_API_KEY`, sends the owner-scoped object bytes to the Responses API
with `store=false`, and uses `VISION_OPENAI_MODEL` plus a hard
`VISION_REQUEST_TIMEOUT_SECONDS` bound. Keep it disabled until the release smoke
in `vision-provider-integration.md` passes with deployment-owned credentials.

Local and server-test development use Docker Compose managed Postgres, Redis,
and MinIO. Production should point the same variables at managed Postgres,
managed Redis, and OSS-compatible object storage without code changes.
