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
2. Confirm production does not use local object storage or localhost DB/Redis.
3. Build the container image.
4. Run tests and migration checks.
5. Export OpenAPI and compare it with the committed snapshot.

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

## Rollback

1. Stop routing traffic to the new API version.
2. Roll back the API/worker image.
3. Do not downgrade schema unless a tested downgrade exists.
4. If an expand-contract migration was used, keep backward-compatible columns
   until both versions are safely drained.
5. Create a regression test from the incident before the next release.

## Worker Backlog

1. Check `/v1/health/metrics` for worker outcomes and error codes.
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
3. Search audit logs by `actor_user_id`, `actor_service`, and `request_id`.
4. Preserve evidence with redaction; do not copy raw secrets into tickets.
5. Add a contract or security regression test.
