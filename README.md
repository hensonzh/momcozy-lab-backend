# Backend

This repository owns the Product API, business data, authentication, files,
notifications, plans, profiles, records, diary, support, voice, and vision.
Agent orchestration, model providers, conversation state, tools, skills,
workers, memory, and evals live in the independent Agent Runtime repository.

## Service Boundary

- Flutter calls this service for Product APIs and calls Agent Runtime through
  its separate Agent base URL.
- Agent Runtime calls only `/v1/internal/agent/**` with
  `X-Service-Key: <AGENT_RUNTIME_SERVICE_API_KEY>`.
- Runtime supplies the user actor explicitly; Product re-authorizes ownership
  and policy and records audit/idempotency data before applying a write.
- Product publishes user-token verification keys at
  `GET /.well-known/jwks.json`.
- Product does not expose Agent run, thread, event, confirmation, memory, or
  eval endpoints and does not start Agent workers.

The internal Agent API covers pregnancy diary, file resolution, plans, profile,
and lactation records. The source of truth is
the generated [API surface catalog](docs/api-surface-catalog.md); integration
rules are in [API contract handoff](docs/api-contract-handoff.md).

## Repository Shape

```text
app/
  api/                 # public Product API composition
  core/                # settings, logging, metrics, rate limiting
  infrastructure/      # database, Redis, object storage
  modules/             # Product domain modules and internal Agent adapters
migrations/            # Product-owned database schema
scripts/               # Product deployment and infrastructure checks
tests/                 # Product API and boundary tests
docs/                  # Product contracts and runbooks
```

Each Product domain that is callable by Runtime owns an `agent_router.py`,
`agent_service.py`, and `agent_contracts.py`. These are Product-side internal
adapters, not an embedded Agent runtime.

## Environment Contract

Copy one committed template to an ignored private file:

```bash
cp env/compose.local.env.example env/compose.local.env
```

Core infrastructure is configured through `DATABASE_URL`, `REDIS_URL`, and
`OBJECT_STORAGE_*`. Authentication and the cross-service boundary use:

- `AUTH_JWT_PRIVATE_KEY_B64`
- `AUTH_JWT_ISSUER`
- `AUTH_JWT_PRODUCT_AUDIENCE`
- `AUTH_JWT_RUNTIME_AUDIENCE`
- `AGENT_RUNTIME_SERVICE_API_KEY`
- `AGENT_MODEL_ASSET_PUBLIC_BASE_URL`
- `AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS`

`OPENAI_API_KEY` in this repository is only for Product voice transcription or
vision when those providers are enabled. Agent model credentials and model
settings belong to Agent Runtime.

Uploaded Agent images remain Product-owned assets. Runtime resolves an
owner-scoped `asset_id` through the internal file endpoint and receives a
stable opaque Product capability URL. Product revalidates owner/status/type on
every authorized resolve and slides the Redis capability TTL by 30 minutes.
The unauthenticated model fetch endpoint revalidates the file again, never
renews TTL, returns `no-store` bytes, and is revoked by deletion. Configure
`AGENT_MODEL_ASSET_PUBLIC_BASE_URL` to the public HTTPS Product API origin
reachable by the model provider.

## Local Development

Local Compose starts Product API, PostgreSQL, Redis, and MinIO:

```bash
make backend-local-up
```

Useful commands:

```bash
make backend-migrate
make backend-check-infra
make backend-productization-status
make backend-export-contracts
make backend-down
```

`backend-check-infra` verifies Product database connectivity, a generic Redis
ping, object-storage round trips, and the Product asset manifest. It contains
no Agent worker or runtime-state checks.

## Server Test

The test Compose profile owns disposable PostgreSQL, Redis, and MinIO volumes
and binds Product API to `127.0.0.1:8001` by default:

```bash
cp env/compose.test.env.example env/compose.test.env
make backend-test-up
make backend-test-smoke
```

`make backend-test-reset` deletes the test Compose volumes and must only be used
for disposable environments.

## Production

Production Compose starts only `api`; the one-time `migrate` service is in the
`tools` profile. PostgreSQL, Redis, and object storage must be managed services:

```bash
cp env/compose.prod.env.example env/compose.prod.env
make backend-prod-readiness
make backend-prod-up
```

Use `backend-prod-services` to rebuild/restart Product API without rerunning
migrations. Agent Runtime is deployed, scaled, observed, and rolled back from
its own repository and pipeline. See the
[deployment runbook](docs/deployment-runbook.md) for release order and
cross-service smoke checks.
