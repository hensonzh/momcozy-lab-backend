# Product Backend Environment Profiles

Keep real secrets outside the repository. Copy example files into local private
files or the deployment secret manager.

## Profiles

| Profile | File | Purpose |
|---|---|---|
| Local compose | `env/compose.local.env.example` | Product API with Compose Postgres, Redis, and MinIO. |
| Server test compose | `env/compose.test.env.example` | Product API and isolated infrastructure on a test server. |
| Production compose | `env/compose.prod.env.example` | Product API with managed Postgres, Redis, and object storage. |

The Product compose profiles build and run only `migrate`, `api`, and the
profile-appropriate infrastructure. Agent Runtime has a separate configuration,
deployment, and repository.

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
APP_ENV=local|test|production
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
AGENT_IMAGE_SIGNED_URL_TTL_SECONDS=...

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
  `AUTH_JWKS_URL` must also appear in Product `TRUSTED_HOSTS`; the production
  templates use `product-api.internal`.
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

## Production Rules

Production startup rejects implicit localhost Postgres/Redis, filesystem object
storage, missing managed storage credentials, missing service keys, missing RSA
signing material, and wildcard trust settings.

The production compose starts only application processes. Infrastructure URLs
must point to managed services. `OBJECT_STORAGE_PUBLIC_ENDPOINT_URL`, when
configured for Runtime image access, must be HTTPS and reachable from the
Runtime/model provider path.

`VISION_PROVIDER=openai` reads owner-scoped Product file bytes and sends a
bounded request with `store=false` and a hard timeout. Keep it disabled until
`vision-provider-integration.md` passes with deployment-owned credentials.

Local and server-test profiles use Compose-managed Postgres, Redis, and MinIO.
Production changes only environment values; Product business code and internal
Agent API contracts remain the same.
