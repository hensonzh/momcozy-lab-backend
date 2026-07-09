# Production Backend Deployment Runbook

This runbook is for the isolated `production_backend/` service. It assumes
Postgres, Redis, and object storage are provided through environment variables.

## Required IDs

Start incident and release debugging from these IDs when available:

- `request_id`
- `actor_user_id`
- `resource_id`
- `run_id`
- `thread_id`
- `action_id`
- `job_id`

## Preflight

1. Confirm production environment variables are set:
   - `APP_ENV=production`
   - `DATABASE_URL`
   - `REDIS_URL`
   - managed `OBJECT_STORAGE_*`
   - `AUTH_JWT_SECRET`
   - `AUTH_JWT_ISSUER`
   - `AUTH_JWT_AUDIENCE`
   - `CORS_ALLOWED_ORIGINS` for any browser/admin client origins
   - `TRUSTED_HOSTS` for public API, admin, and probe hosts
   - `RATE_LIMIT_ENABLED=true` with environment-appropriate
     `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW_SECONDS`
   - `FILE_UPLOAD_MAX_BYTES` tuned for the largest supported user upload
2. Confirm production does not use local object storage or localhost DB/Redis.
3. Confirm production CORS origins and trusted hosts are explicit and do not
   use `*`.
4. Confirm unexpected `Host` headers are rejected before business handlers.
5. Confirm health and OpenAPI/docs paths are exempt but user/API paths return a
   stable `rate_limited` envelope when the configured limit is exceeded.
6. Confirm oversized uploads return `payload_too_large` before object storage
   writes.
7. Build the container image.
8. Run tests and migration checks.
9. Check backup/restore automation hooks:
   `python production_backend/scripts/check_backup_restore_hooks.py --strict`
10. Export OpenAPI and compare it with the committed snapshot.
11. Run backend-local productization guardrails:
    `make backend-productization-status`.
12. In the server test environment, run the compose-backed smoke bundle:
    `make backend-test-smoke`.

## Release

1. Deploy migration job:
   `python -m alembic -c production_backend/alembic.ini upgrade head`
2. Deploy API.
3. Deploy worker processes after API image is healthy.
4. Wait for `/v1/health/live`.
5. Wait for `/v1/health/ready`.
6. Run the release smoke checklist.
7. Watch 5xx rate, latency, auth failures, worker failures, and agent run
   failures for at least one SLO window.
8. During rolling restarts, let agent and outbox workers receive SIGTERM/SIGINT
   and stop at the next idle point before force killing the process.

## Production Docker Compose

`production_backend/docker-compose.prod.yml` is the server deployment template.
It starts only application processes and assumes Postgres, Redis, and object
storage are provided through environment variables. It intentionally does not
start local `postgres`, `redis`, `minio`, or `minio-init` services.

Prepare a private env file on the server:

```bash
cp production_backend/env/compose.prod.env.example production_backend/env/compose.prod.env
```

Fill the real managed infrastructure URLs, object-storage credentials, JWT
secret, service key, and OpenAI settings. Then build or pull the image:

```bash
make backend-prod-build
```

Run the release path with an explicit migration followed by the API and worker
services:

```bash
make backend-prod-up
```

`backend-prod-up` builds the local `migrate`, `api`, `agent-worker`, and
`outbox-worker` images before running migrations and recreating runtime
containers. Add `BACKEND_BUILD_FLAGS=--no-cache` when the server should ignore
Docker cache completely.

For restarts that should not run migrations again:

```bash
make backend-prod-services
```

Inspect the deployment:

```bash
make backend-prod-ps
make backend-prod-logs
```

The production compose binds the API to `127.0.0.1:8000` by default. Put Nginx,
Caddy, or a cloud load balancer in front of it and route HTTPS traffic to that
local port. Override the bind only when the server network boundary is already
protected:

```bash
MOMCOZY_API_BIND=0.0.0.0:8000 make backend-prod-services
```

To deploy a registry image instead of building locally:

```bash
MOMCOZY_BACKEND_IMAGE=registry.example.com/momcozy/backend:2026-07-07 make backend-prod-up
```

Scale agent run capacity by increasing worker replicas and, separately,
`AGENT_RUNTIME_WORKER_CONCURRENCY`:

```bash
MOMCOZY_BACKEND_ENV_FILE=env/compose.prod.env \
docker compose -f production_backend/docker-compose.prod.yml up -d --scale agent-worker=3 agent-worker
```

External traffic routers should probe `/v1/health/ready`, not only container
liveness, because readiness verifies configured infrastructure.

## Server Test Docker Compose

`production_backend/docker-compose.test.yml` is for a test server where the app
and its infrastructure run in Docker. Unlike the production compose, it starts
containerized `postgres`, `redis`, `minio`, and `minio-init`. Unlike local
compose, it does not publish DB/Redis/MinIO host ports by default.

Prepare a private test env file:

```bash
cp production_backend/env/compose.test.env.example production_backend/env/compose.test.env
```

Enable the agent worker only after adding a test provider key:

```env
AGENT_RUNTIME_WORKER_ENABLED=true
AGENT_MODEL_PROVIDER=openai
OPENAI_API_KEY=...
```

With `AGENT_MODEL_PROVIDER=openai`, runtime requests that include namespace /
deferred tool loading use the Responses API adapter and hosted `tool_search`.

For an experimental Minimax smoke test, switch only the provider block in the
private env file:

```env
AGENT_RUNTIME_WORKER_ENABLED=true
AGENT_MODEL_PROVIDER=minimax
MINIMAX_API_KEY=...
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M3
```

The Minimax path remains the flat OpenAI-compatible Agents SDK tool path; runtime
metadata keeps `tool_search_enabled=false` there.

Start the full test stack:

```bash
make backend-test-up
```

`backend-test-up` follows the same build-before-run rule, but includes
containerized Postgres, Redis, and MinIO for server-side testing.

Inspect and tail logs:

```bash
make backend-test-ps
make backend-test-logs
```

Stop the test stack while keeping data volumes:

```bash
make backend-test-down
```

The test API binds to `127.0.0.1:8001` by default:

```text
host 127.0.0.1:8001 -> container api:8000
```

Put Nginx or Caddy in front of it for `https://api-test.example.com`. Keep
Postgres, Redis, and MinIO internal to the compose network unless a temporary
operations task explicitly requires a port forward.

## Agent Worker Capacity

Agent run capacity is controlled by both worker replicas and per-process async
slots:

```text
total agent run slots ~= agent-worker replicas * AGENT_RUNTIME_WORKER_CONCURRENCY
```

Each concurrent run uses its own database session and still acquires the Redis
run lock before execution. Increase `AGENT_RUNTIME_WORKER_CONCURRENCY` together
with `AGENT_RUNTIME_WORKER_BATCH_LIMIT`, database pool capacity, OpenAI
RPM/TPM limits, tool latency budgets, and cost controls. For I/O-heavy agent
runs, 100 concurrent run slots do not imply 100 CPU cores; measure CPU,
provider wait time, DB pool saturation, Redis latency, and token/cost usage
before increasing limits.

## Rollback

1. Stop routing traffic to the new API version.
2. Let worker processes drain on SIGTERM/SIGINT, then roll back the API/worker image.
3. Do not downgrade schema unless a tested downgrade exists.
4. If an expand-contract migration was used, keep backward-compatible columns
   until both versions are safely drained.
5. Create a regression test from the incident before the next release.

## Worker Backlog

1. Check `/v1/health/metrics` with `X-Service-Key` in production for worker
   outcomes and error codes.
2. Inspect queued and locked `outbox_jobs` with
   `make backend-worker-backlog BACKEND_ENV_FILE=<env file>`.
3. Confirm Redis and external providers are reachable.
4. If jobs are locked by a dead worker, wait for lease expiry or release them
   with an audited maintenance script.
5. For repeated permanent failures, move affected jobs to dead-letter and open
   a repair ticket with `job_id`, `action_id`, and `request_id`.

## Agent Run Recovery

1. Locate the run by `run_id` and `thread_id`.
2. Inspect persisted run status, messages, events, tool calls, actions, and
   safety events.
3. Check Redis active run lock and cancel flag; Redis state is reconstructable.
4. Replay persisted events before attempting any new model call.
5. If the run is waiting for confirmation, keep it waiting and ask the client to
   resume from persisted action state.
6. Dry-run stuck running recovery with
   `make backend-agent-recover-stuck-runs BACKEND_ENV_FILE=<env file>`.
7. Only after support/incident approval, run
   `python production_backend/scripts/recover_stuck_agent_runs.py --apply`.
8. If the run failed, create an eval or replay regression case when safe.

## Provider Eval Budget

Provider-backed eval is deferred for the current simplified multi-agent phase.
Keep `AGENT_PROVIDER_EVAL_MAX_CASES` and
`AGENT_PROVIDER_EVAL_COST_BUDGET_USD` set in staging secret templates so the
budget switch is explicit when provider evals are reintroduced. Release
readiness uses deterministic seed and replay evals until the provider eval path
is reintroduced with a concrete product need.

When provider-backed eval is re-enabled, cap it with
`AGENT_PROVIDER_EVAL_MAX_CASES` and `AGENT_PROVIDER_EVAL_COST_BUDGET_USD`.

## Security Incident

1. Revoke affected device sessions or refresh-token families.
2. Rotate service keys or object-storage credentials when implicated.
3. Tighten `RATE_LIMIT_REQUESTS` or `RATE_LIMIT_WINDOW_SECONDS` if the incident
   involves abusive request volume.
4. Search audit logs by `actor_user_id`, `actor_service`, and `request_id`.
5. Preserve evidence with redaction; do not copy raw secrets into tickets.
6. Add a contract or security regression test.

## Backup And Restore Drill

Run this drill before production launch and on a regular schedule:

1. Confirm Postgres backup completion and retention window.
2. Confirm object storage retention policy for uploads and artifacts.
3. Confirm automation hook references are configured:
   `python production_backend/scripts/check_backup_restore_hooks.py --strict`
4. Restore the latest backup into an isolated environment.
5. Run migrations to the current head against the restored database.
6. Run `/v1/health/ready` against the restored environment.
7. Run the release smoke checklist with a non-production test account.
8. Record actual RPO/RTO and fix gaps before relying on the backup.

Never treat a backup as valid until a restore has been tested.

## Credential Rotation Drill

Rotate credentials without code changes:

1. Create new managed Postgres, Redis, object-storage, JWT, or service-key
   credentials in the secret manager.
2. Deploy the new environment variables to staging first.
3. Run readiness and smoke tests.
4. Deploy to production with a rolling restart.
5. Revoke old credentials only after all API and worker instances have the new
   values.
6. Check auth failures, object-storage failures, worker failures, and 5xx rate.
7. Record the rotated key IDs and incident/change ticket.

Do not put old or new secrets in logs, tickets, commits, or OpenAPI examples.
