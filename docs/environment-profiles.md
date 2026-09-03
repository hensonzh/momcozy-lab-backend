# Product Backend Environment Profiles

Keep real secrets outside the repository. Copy example files into local private
files or the deployment secret manager.

Environment names are fixed: `local` is developer-only and `test` is the shared
internal server. CI is an ephemeral verification lane with a distinct Compose
project, not another deployable environment. A deployable profile must use the same token
in its Compose filename, env filename, Compose project, and convenience targets.
Release images use the environment-neutral repository name plus an immutable
commit tag or digest (for example `momcozy-lab-backend:<git-sha>`); legacy names
such as `momcozy-production-backend` must not be reused.

## Profiles

| Profile | File | Purpose |
|---|---|---|
| Local compose | `env/compose.local.env.example` | Product Backend with Compose Postgres, Redis, and MinIO. |
| CI override | `docker-compose.ci.yml` | CI-only override for project `momcozy-lab-backend-ci` and image `momcozy-lab-backend:ci`. |
| Test compose | `env/compose.test.env.example` | Product Backend plus the one shared test PostgreSQL, Redis, MinIO, and network. |

`docker-compose.ci.yml` is combined with `docker-compose.local.yml`; it is
not a standalone Compose file or a deployable server profile. CI injects an
ephemeral RSA private key generated on the runner, executes the explicit
migration job, then verifies the built API image through
`/v1/health/ready`. The key is never stored in the repository or promoted to
test.

The Product Backend Compose profiles build and run only `migrate`, `api`, and the
profile-appropriate infrastructure. Agent Runtime has a separate application
configuration and repository, but joins Product Backend's test network and consumes
the shared infrastructure through isolated databases, logical Redis DBs, and
buckets.

## Common Commands

```bash
make backend-local-up
make backend-check-infra
```

`backend-local-up` builds the Product Backend image, starts local infrastructure, runs
migrations, and recreates the API. `backend-check-infra` loads
`BACKEND_ENV_FILE` and checks:

- PostgreSQL connectivity.
- Redis connectivity with a generic `PING`.
- Object-storage write/read/delete behavior.
- product asset manifest objects.

Redis run locks, stream cursors, model configuration, Agent Runtime workers, memory, and
eval settings are Agent Runtime-owned and must not be added to Product Backend profiles.

## Product Backend Configuration

The shared shape across environments is:

```env
APP_ENV=local|test
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

`OPENAI_API_KEY` in this repository is used only by Product Backend-owned provider
adapters such as vision or speech. Agent model/provider settings belong to the
Agent Runtime configuration.

## Product Backend And Agent Runtime Trust

- The Product Backend owns `AUTH_JWT_PRIVATE_KEY_B64`.
- Product Backend publishes its public key through `GET /.well-known/jwks.json`.
- Product Backend and Agent Runtime use the same `AUTH_JWT_ISSUER` and Agent Runtime audience.
- Agent Runtime receives no JWT private key.
- `AGENT_RUNTIME_SERVICE_API_KEY` authenticates only Agent Runtime calls to
  `/v1/internal/agent/*`.
- Every hostname used by Agent Runtime in `PRODUCT_BACKEND_BASE_URL` and
  `AUTH_JWKS_URL` must also appear in Product Backend `TRUSTED_HOSTS`; test uses the
  private network alias `product-backend`.
- Authenticated `/v1/internal/agent/*` traffic uses the independent
  `AGENT_RUNTIME_RATE_LIMIT_REQUESTS` /
  `AGENT_RUNTIME_RATE_LIMIT_WINDOW_SECONDS` bucket. Size it from Agent
  concurrency and tool-call load tests instead of sharing the public API
  credential limit.
- `SERVICE_API_KEY` remains the operator/admin credential and must not be reused
  as the Agent Runtime key.
- The Product Backend validates `actor_user_id`, owner scope, action payload,
  action-bound idempotency, and audit fields for every Agent Runtime-originated
  business operation.
- Agent file resolution revalidates file ownership, lifecycle state, and
  purpose before issuing or renewing a reconstructable Redis bearer
  capability. Keys include actor, file, purpose, and the immutable object-key
  version. Redis loss fails model attachment resolution closed.
- The public model-asset fetch route revalidates the authoritative file row,
  never extends capability lifetime, remains rate limited, and redacts bearer
  tokens from application request and exception logs.

## Test Shared Infrastructure

- Product Backend Compose owns network `momcozy-lab-test` and must start first.
- PostgreSQL uses separate roles/databases: `momcozy_test` and
  `agent_runtime_test`.
- Product Backend uses Redis logical DB 0; Agent Runtime uses DB 1 and its existing
  `agent-runtime:*`/`momcozy-agent-runtime:*` key namespaces.
- Product Backend uses bucket `momcozy-test`; Agent Runtime uses
  `agent-runtime-test`.
- PostgreSQL, Redis, and MinIO have no host port mappings.

No production Compose or env template is currently maintained. The code-level
production startup validation remains available for a future reviewed
production design.
