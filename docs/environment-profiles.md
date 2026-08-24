# Product Backend Environment Profiles

Keep real secrets outside the repository. Copy example files into local private
files or the deployment secret manager.

Environment names are fixed: `local` is developer-only, `test` is reserved for
automated tests/CI, and `staging` is the shared internal server. A deployable
profile must use the same token
in its Compose filename, env filename, Compose project, and convenience targets.
Release images use the environment-neutral repository name plus an immutable
commit tag or digest (for example `momcozy-lab-backend:<git-sha>`); legacy names
such as `momcozy-production-backend` must not be reused.

## Profiles

| Profile | File | Purpose |
|---|---|---|
| Local compose | `env/compose.local.env.example` | Product API with Compose Postgres, Redis, and MinIO. |
| Staging compose | `env/compose.staging.env.example` | Product API plus the one shared staging PostgreSQL, Redis, MinIO, and network. |

The Product compose profiles build and run only `migrate`, `api`, and the
profile-appropriate infrastructure. Agent Runtime has a separate application
configuration and repository, but joins Backend's staging network and consumes
the shared infrastructure through isolated databases, logical Redis DBs, and
buckets.

## Common Commands

```bash
make backend-local-up
make backend-check-infra
```

`backend-local-up` builds the Product image, starts local infrastructure, runs
migrations, and recreates the API. `backend-check-infra` loads
`BACKEND_ENV_FILE` and checks:

- PostgreSQL connectivity.
- Redis connectivity with a generic `PING`.
- Object-storage write/read/delete behavior.
- Product asset manifest objects.

Redis run locks, stream cursors, model configuration, Agent workers, memory, and
eval settings are Runtime-owned and must not be added to Product profiles.

## Product Configuration

The shared shape across environments is:

```env
APP_ENV=local|test|staging
DATABASE_URL=...
REDIS_URL=...

OBJECT_STORAGE_PROVIDER=minio|s3|oss|cos
OBJECT_STORAGE_BUCKET=...
OBJECT_STORAGE_ENDPOINT_URL=...
OBJECT_STORAGE_ACCESS_KEY_ID=...
OBJECT_STORAGE_SECRET_ACCESS_KEY=...

AUTH_JWT_PRIVATE_KEY_B64=...
AUTH_JWT_ISSUER=...
AUTH_JWT_PRODUCT_AUDIENCE=momcozy-product-api
AUTH_JWT_RUNTIME_AUDIENCE=momcozy-agent-runtime

SERVICE_API_KEY=...
AGENT_RUNTIME_SERVICE_API_KEY=...
AGENT_MODEL_ASSET_PUBLIC_BASE_URL=https://api.example.com
AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS=1800

OPENAI_API_KEY=...
VISION_PROVIDER=disabled|openai
VISION_OPENAI_MODEL=gpt-5.4-mini
VISION_REQUEST_TIMEOUT_SECONDS=20
```

`OPENAI_API_KEY` in this repository is used only by Product-owned provider
adapters such as vision or speech. Agent model/provider settings belong to the
Runtime configuration.

## Product And Runtime Trust

- The Product Backend owns `AUTH_JWT_PRIVATE_KEY_B64`.
- Product publishes its public key through `GET /.well-known/jwks.json`.
- Product and Runtime use the same `AUTH_JWT_ISSUER` and Runtime audience.
- Runtime receives no JWT private key.
- `AGENT_RUNTIME_SERVICE_API_KEY` authenticates only Runtime calls to
  `/v1/internal/agent/*`.
- Every hostname used by Runtime in `PRODUCT_BACKEND_BASE_URL` and
  `AUTH_JWKS_URL` must also appear in Product `TRUSTED_HOSTS`; staging uses the
  private network alias `product-backend`.
- Authenticated `/v1/internal/agent/*` traffic uses the independent
  `AGENT_RUNTIME_RATE_LIMIT_REQUESTS` /
  `AGENT_RUNTIME_RATE_LIMIT_WINDOW_SECONDS` bucket. Size it from Agent
  concurrency and tool-call load tests instead of sharing the public API
  credential limit.
- `SERVICE_API_KEY` remains the operator/admin credential and must not be reused
  as the Runtime key.
- The Product Backend validates `actor_user_id`, owner scope, action payload,
  action-bound idempotency, and audit fields for every Runtime-originated
  business operation.
- Agent file resolution revalidates file ownership, lifecycle state, and
  purpose before issuing or renewing a reconstructable Redis bearer
  capability. Keys include actor, file, purpose, and the immutable object-key
  version. Redis loss fails model attachment resolution closed.
- The public model-asset fetch route revalidates the authoritative file row,
  never extends capability lifetime, remains rate limited, and redacts bearer
  tokens from application request and exception logs.

## Staging Shared Infrastructure

- Backend Compose owns network `momcozy-lab-staging` and must start first.
- PostgreSQL uses separate roles/databases: `momcozy_staging` and
  `agent_runtime_staging`.
- Product uses Redis logical DB 0; Agent uses DB 1 and its existing
  `agent-runtime:*`/`momcozy-agent-runtime:*` key namespaces.
- Product uses bucket `momcozy-staging`; Agent uses
  `agent-runtime-staging`.
- PostgreSQL, Redis, and MinIO have no host port mappings.

No production Compose or env template is currently maintained. The code-level
production startup validation remains available for a future reviewed
production design.
