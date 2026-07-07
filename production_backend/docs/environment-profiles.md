# Environment Profiles

The backend is configured by environment variables. Keep real secrets outside
the repository and copy the example files into your deployment secret manager or
local `.env` files.

## Profiles

| Profile | File | Purpose |
|---|---|---|
| Local host | `production_backend/env/local.env.example` | Run the app from the host while Docker Compose exposes Postgres, Redis, and MinIO on localhost ports. |
| Local compose | `production_backend/env/compose.local.env.example` | Run API and workers with `docker-compose.local.yml`; service hosts are `postgres`, `redis`, and `minio`. |
| Server test compose | `production_backend/env/compose.test.env.example` | Run API, workers, Postgres, Redis, and MinIO with `docker-compose.test.yml` on a test server. |
| Production compose | `production_backend/env/compose.prod.env.example` | Run API and workers with `docker-compose.prod.yml`; managed Postgres, Redis, and OSS/S3 are supplied through env. |
| Staging host/platform | `production_backend/env/staging.env.example` | Non-Compose staging deployment or platform-injected settings with managed services. |
| Production host/platform | `production_backend/env/production.env.example` | Non-Compose production deployment or platform-injected settings with production startup validation. |

## Common Commands

```bash
make backend-local-up
make backend-check-infra BACKEND_ENV=local
```

`backend-local-up` starts local infrastructure, runs migrations, and starts the
API plus agent/outbox workers. `backend-local-migrate` and
`backend-local-workers` remain available for explicit maintenance, retries, and
debugging.

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

The compose file is for local development only. Staging and production should
inject the corresponding variables through the deployment platform.

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
```

Local development uses Docker Compose managed Postgres, Redis, and MinIO.
Production should point the same variables at managed Postgres, managed Redis,
and OSS-compatible object storage without code changes.
