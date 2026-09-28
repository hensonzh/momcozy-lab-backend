# Product Backend (backend/)

This repository contains Product Backend (backend/) and owns business data,
authentication, files, notifications, plans, profiles, records, diary, support,
voice and vision workflows. Agent Runtime orchestration, model providers, conversation state, tools, skills,
workers, memory, and evals live in the independent Agent Runtime repository.

Human-facing documentation uses the canonical service names `Product Backend
(backend/)` and `Agent Runtime (agent/)`. Existing identifiers such as
`PRODUCT_BACKEND_BASE_URL`, `AUTH_JWT_PRODUCT_AUDIENCE`, the `product-backend`
Compose alias, and the `/v1` API contract remain stable compatibility names.

## Service Boundary

- Flutter calls Product Backend and calls Agent Runtime through its
  separate Agent Runtime base URL.
- Agent Runtime calls only `/v1/internal/agent/**` with
  `X-Service-Key: <AGENT_RUNTIME_SERVICE_API_KEY>`.
- Agent Runtime supplies the user actor explicitly; Product Backend
  re-authorizes ownership and policy and records audit/idempotency data before
  applying a write.
- Product Backend publishes user-token verification keys at
  `GET /.well-known/jwks.json`.
- Product Backend does not expose Agent Runtime run, thread, event,
  confirmation, memory, or eval endpoints and does not start Agent Runtime
  workers.

The internal Agent Runtime API covers file resolution, profile and lactation
records, plus legacy diary/plan adapters retained only for migration
compatibility. The source of truth is the generated [API surface catalog](docs/api-surface-catalog.md);
integration rules are in [API contract handoff](docs/api-contract-handoff.md).


## Repository Shape

```text
app/
  api/                 # public Product Backend API composition
  core/                # settings, logging, metrics, rate limiting
  infrastructure/      # database, Redis, object storage
  modules/             # product domain modules and internal Agent Runtime adapters
migrations/            # Product Backend-owned database schema
scripts/               # Product Backend deployment and infrastructure checks
tests/                 # Product Backend API and boundary tests
docs/                  # Product Backend contracts and runbooks
```

Each product domain that is callable by Agent Runtime owns an `agent_router.py`,
`agent_service.py`, and `agent_contracts.py`. These are Product Backend internal
adapters, not an embedded Agent Runtime.

## Environment Contract

Copy one committed template to an ignored private file:

```bash
cp env/local.env.example env/local.env
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

`OPENAI_API_KEY` in this repository is only for Product Backend voice transcription or
vision when those providers are enabled. Agent model credentials and model
settings belong to Agent Runtime.

Images uploaded for Agent Runtime remain Product Backend-owned assets. Agent Runtime resolves an
owner-scoped `asset_id` through the internal file endpoint and receives a
stable opaque Product Backend capability URL. Product Backend revalidates owner/status/type on
every authorized resolve and slides the Redis capability TTL by 30 minutes.
The unauthenticated model fetch endpoint revalidates the file again, never
renews TTL, returns `no-store` bytes, and is revoked by deletion. Configure
`AGENT_MODEL_ASSET_PUBLIC_BASE_URL` to the public HTTPS Product Backend origin
reachable by the model provider. In the local-only Compose environment, Agent
Runtime instead fetches an owner-scoped image through its authenticated internal
endpoint and inlines the bytes for the provider call; this avoids exposing a
local developer machine through a public tunnel. The local inline image is
never written to the transcript or context ledger. Staging/production keep the
HTTPS capability URL flow and require a real provider-reachable origin.

## Local Development

Local Compose starts Product Backend, PostgreSQL, Redis, and MinIO:

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

`backend-check-infra` verifies Product Backend database connectivity, a generic Redis
ping, object-storage round trips, and the product asset manifest. It contains
no Agent Runtime worker or runtime-state checks.

## CI Container Profile

`docker-compose.ci.yml` is a CI-only override and is never deployed to a
server. CI combines it with `docker-compose.local.yml`, changes the Compose
project to `momcozy-lab-backend-ci`, and builds
`momcozy-lab-backend:ci`. The workflow generates an ephemeral RSA signing key,
runs the migration job, starts the real API container, and requires
`/v1/health/ready` to pass before cleaning up the stack. No CI private key is
committed or reused.

## Deployable Environments

`staging` and `production` share one environment-neutral deployment implementation:

```text
env/staging.env.example
env/production.env.example
docker-compose.deploy.yml
scripts/release.py
.github/workflows/backend-delivery.yml
```

Product Backend owns the shared PostgreSQL, Redis, MinIO, and Docker network for
each environment. Agent Runtime joins that network as an external consumer and
uses separate database, Redis ACL/DB, and bucket identities. The actual staging
DNS names still contain `-test`; this is a transitional hostname only.

A successful `backend-ci` run publishes the exact tested image under the full
commit SHA and records its digest. An operator then runs `backend-delivery`,
selects `staging` or `production`, and chooses `bootstrap`, `deploy`, or
`rollback`. Deployment pulls only the CI-proven digest, validates the private
environment file, serializes with the shared release lock, backs up before schema
changes, verifies loopback and public readiness, and writes the release manifest
under `${RELEASE_ROOT}/current/backend/release-manifest.json`.

Direct server mutation is intentionally absent from the Makefile. Use
`make backend-staging-config` or `make backend-production-config` only to render
and validate the committed Compose contract. See
[`docs/environment-profiles.md`](docs/environment-profiles.md) and
[`docs/deployment-runbook.md`](docs/deployment-runbook.md).
