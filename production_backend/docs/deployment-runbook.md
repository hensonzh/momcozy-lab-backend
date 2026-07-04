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
2. Inspect queued and locked `outbox_jobs`.
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
6. If the run failed, create an eval or replay regression case when safe.

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
