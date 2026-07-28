# Product Backend Deployment Runbook

This runbook covers only the Product Backend. Agent Runtime is deployed from a
separate repository and has its own database, workers, model configuration,
evals, and run-recovery procedures.

## Required IDs

Start release and incident debugging from these IDs when available:

- `request_id`
- `actor_user_id`
- `resource_id`
- `action_id`
- `idempotency_key`

Runtime-owned `thread_id` and `run_id` may be supplied as cross-service
correlation values, but they are not Product Backend execution state.

## Preflight

1. Confirm the production configuration includes:
   - `APP_ENV=production`
   - managed `DATABASE_URL` and `REDIS_URL`
   - managed `OBJECT_STORAGE_*`
   - `AUTH_JWT_PRIVATE_KEY_B64`
   - `AUTH_JWT_ISSUER`
   - `AUTH_JWT_PRODUCT_AUDIENCE`
   - `AUTH_JWT_RUNTIME_AUDIENCE`
   - `AGENT_RUNTIME_SERVICE_API_KEY`
   - `AGENT_IMAGE_SIGNED_URL_TTL_SECONDS`
   - `AGENT_FILE_URL_REUSE_TTL_SECONDS`
   - explicit `CORS_ALLOWED_ORIGINS` and `TRUSTED_HOSTS`
   - environment-appropriate rate limits and upload limits
2. Confirm the Product Backend is the only holder of the JWT private key.
3. Confirm production does not use localhost infrastructure or filesystem
   object storage.
4. Confirm `GET /.well-known/jwks.json` returns only public RSA fields and the
   expected active key id.
5. Confirm the Runtime service key differs from the general operator service
   key and is available only to the two services.
6. Build the `migrate` and `api` images.
7. Run tests, migration checks, backup-hook validation, and OpenAPI drift
   checks.
8. Confirm the signed URL TTL exceeds the Redis reuse TTL by at least five
   minutes. The production template uses 3600/1800 seconds.
9. Run:

   ```bash
   make backend-productization-status
   make backend-test-smoke
   make backend-prod-readiness
   ```

## Release

1. Run the migration job:

   ```bash
   python -m alembic -c alembic.ini upgrade head
   ```

   The current baseline targets an empty database and does not support
   upgrading an older Product schema. This is intentional while the product is
   limited to resettable internal testing. Drop and recreate the test database
   before deploying this baseline; do not point it at a database whose data
   must be preserved.

2. Deploy the Product API.
3. Wait for `GET /v1/health/live`.
4. Wait for `GET /v1/health/ready`; readiness must verify configured Postgres
   and Redis.
5. Run `docs/release-smoke-checklist.md`.
6. Verify the JWKS and internal Agent API boundary before allowing a separately
   deployed Runtime to use the new Product contract.
7. Watch 5xx rate, latency, auth failures, database/Redis latency, and object
   storage failures for at least one SLO window.

Product deployment does not start or manage Agent Runtime processes.
Product Compose does not start an Agent worker; Runtime is independently
deployed.

## Production Docker Compose

`docker-compose.prod.yml` is the server deployment template. It contains the
Product `migrate` job and `api` service only. Managed Postgres, Redis, and object
storage are supplied through environment variables.

Prepare a private environment file:

```bash
cp env/compose.prod.env.example env/compose.prod.env
```

Build and release:

```bash
make backend-prod-build
make backend-prod-up
```

`backend-prod-up` builds the current Product image, runs migrations, and
recreates the API. Use `BACKEND_BUILD_FLAGS=--no-cache` only when a clean image
build is required.

Restart the API without another migration:

```bash
make backend-prod-services
```

Inspect the deployment:

```bash
make backend-prod-ps
make backend-prod-logs
```

The production compose binds the API to `127.0.0.1:8000` by default. Put Nginx,
Caddy, or a cloud load balancer in front of it. External health probes should
use `/v1/health/ready`, not container liveness alone.

To deploy a registry image:

```bash
MOMCOZY_BACKEND_IMAGE=registry.example.com/momcozy/backend:2026-07-26 \
make backend-prod-up
```

## Server Test Docker Compose

`docker-compose.test.yml` runs the Product API with isolated Postgres, Redis,
and MinIO. It does not start Agent Runtime.

```bash
cp env/compose.test.env.example env/compose.test.env
make backend-test-up
make backend-test-ps
make backend-test-logs
```

Stop it while preserving data volumes:

```bash
make backend-test-down
```

The test API binds to `127.0.0.1:8001` by default. Keep Postgres, Redis, and
MinIO private to the Compose network.

## Agent Runtime Integration Boundary

The independent Runtime calls only the Product internal API:

- `GET /v1/internal/agent/profile`
- `POST /v1/internal/agent/actions/profile.update/apply`
- `GET /v1/internal/agent/lactation/milk-analysis-snapshot`
- `POST /v1/internal/agent/actions/lactation.record/apply`
- `GET /v1/internal/agent/plans/current`
- `GET /v1/internal/agent/plans/calendar`
- `GET /v1/internal/agent/plans/{plan_id}`
- `GET /v1/internal/agent/schedule-timeline`
- `POST /v1/internal/agent/actions/plans/apply`
- `GET /v1/internal/agent/diary`
- `POST /v1/internal/agent/actions/diary.entry/apply`
- `POST /v1/internal/agent/files/resolve`

Every call requires the Runtime-only `X-Service-Key`. Read calls carry
`actor_user_id` as an explicit query parameter. Action calls carry
`actor_user_id`, `action_id`, and runtime correlation metadata in the request
body. The Product Backend derives owner scope from `actor_user_id`, validates
the action payload, writes business state, and records audit data.

Every action apply call also requires:

```text
Idempotency-Key: agent-action:<action_id>
```

The same action and idempotency key must replay the stored Product result rather
than duplicate a business write. The Runtime owns proposal, confirmation,
conversation, orchestration, and final-response state; the Product Backend owns
only the resulting business transaction.

`POST /v1/internal/agent/files/resolve` validates both `actor_user_id` and
`file_id` ownership before returning a bounded signed URL. Runtime must retain
the stable Product `file_id`, not persist the signed URL as identity.

## Nginx Edge Proxy

`deploy/nginx/momcozy-api.conf` is the versioned Product API edge template. Keep
the application bound to loopback and expose it through controlled HTTPS
ingress. The template rejects unknown hosts, forwards request IDs, preserves
WebSocket upgrade headers for Product voice endpoints, and applies an upload
limit above the application limit.

Validate before switching traffic:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Never publish the Product API container port, Postgres, Redis, or object storage
directly. Agent Runtime reaches the internal API through an approved private
network or service gateway and must not share a public mobile route.

## Rollback

1. Stop routing traffic to the new Product API version.
2. Roll back the Product API image.
3. Do not downgrade schema unless a tested downgrade exists.
4. For expand-contract changes, keep backward-compatible schema and response
   fields until both Product and Runtime callers have moved.
5. If the internal Agent API contract caused the incident, disable Runtime
   traffic at the service gateway or rotate/revoke its service key while Product
   APIs recover.
6. Add a Product contract regression test before the next release.

## Product Dependency Incident

1. Check `/v1/health/ready`.
2. Check `/v1/health/metrics` with the operator `X-Service-Key`.
3. Verify Postgres, Redis, and object storage independently.
4. Search logs by `request_id`; for Runtime-originated writes, also correlate
   `actor_user_id` and `action_id`.
5. Retry only idempotent operations with the original `Idempotency-Key`.
6. Do not operate on Runtime queues, runs, memory, or model-provider state from
   the Product Backend.

## Security Incident

1. Revoke affected Product device sessions or refresh-token families.
2. Rotate the Runtime service key, operator service key, or object-storage
   credentials when implicated.
3. Search audit logs by `actor_user_id`, `actor_service`, `action_id`, and
   `request_id`.
4. Preserve evidence with redaction; never copy secrets, health content, or
   signed URLs into tickets.
5. Add a contract or security regression test.

## Backup And Restore Drill

1. Confirm Product Postgres backup completion and retention.
2. Confirm object-storage retention for uploads and product assets.
3. Run:

   ```bash
   python scripts/check_backup_restore_hooks.py --strict
   ```

4. Restore into an isolated environment.
5. Run migrations to the current head.
6. Run `/v1/health/ready` and the Product release smoke checklist.
7. Record actual RPO/RTO.

Runtime persistence is backed up and restored by the Runtime team; do not assume
the Product database contains conversation or run state.

## Credential Rotation Drill

1. Create new Product infrastructure, JWT signing, or service credentials in
   the secret manager.
2. For JWT signing changes, coordinate with Runtime because it validates Product
   access tokens through JWKS.
3. For Runtime service-key changes, deploy the new shared value to both services
   in a coordinated maintenance window.
4. Run readiness, JWKS, internal API authentication, owner-scope, and
   idempotency smoke checks.
5. Check auth, object storage, dependency, and 5xx metrics.

The current single-key JWKS contract does not provide an overlapping signing-key
window. Add current/previous key publication before relying on zero-downtime JWT
rotation. Never put old or new credentials in logs, tickets, commits, or
OpenAPI examples.
