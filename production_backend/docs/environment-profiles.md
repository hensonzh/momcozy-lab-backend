# Environment Profiles

The backend is configured by environment variables. Keep real secrets outside
the repository and copy the example files into your deployment secret manager or
local `.env` files.

## Profiles

| Profile | File | Purpose |
|---|---|---|
| Local compose | `production_backend/env/compose.local.env` | Run API and workers with `docker-compose.local.yml`; service hosts are `postgres`, `redis`, and `minio`. Copy it from `production_backend/env/compose.local.env.example`. |
| Server test compose | `production_backend/env/compose.test.env.example` | Run API, workers, Postgres, Redis, and MinIO with `docker-compose.test.yml` on a test server. |
| Production compose | `production_backend/env/compose.prod.env.example` | Run API and workers with `docker-compose.prod.yml`; managed Postgres, Redis, and OSS/S3 are supplied through env. |

## Common Commands

```bash
make backend-local-up
make backend-check-infra
```

`backend-local-up` first builds `migrate`, `api`, `agent-worker`, and
`outbox-worker` from the current source tree, then starts local infrastructure,
runs migrations, and starts the API plus agent/outbox workers with recreated
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
AGENT_MODEL_PROVIDER=openai|minimax
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING_EFFORT=low
OPENAI_RESPONSES_STORE=false
OPENAI_AGENT_USE_RESPONSES=true
AGENT_QUICK_REPLY_MODEL=gpt-5.4-nano
AGENT_QUICK_REPLY_TIMEOUT_SECONDS=3.0
MINIMAX_API_KEY=...
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M3
```

The OpenAI production path uses the native Responses runner. Keep
`OPENAI_AGENT_USE_RESPONSES=true` for normal traffic; setting it to `false`
selects the temporary Agents SDK rollback path. `OPENAI_RESPONSES_STORE=false`
keeps conversation authority in the application database, while the runtime
round-trips required response and reasoning items within the active run.

The main agent uses `OPENAI_MODEL` and `OPENAI_REASONING_EFFORT`. Quick replies
use `AGENT_QUICK_REPLY_MODEL` as a separate lightweight finalizer and do not
persist to the message database.

Local and server-test development use Docker Compose managed Postgres, Redis,
and MinIO. Production should point the same variables at managed Postgres,
managed Redis, and OSS-compatible object storage without code changes.
