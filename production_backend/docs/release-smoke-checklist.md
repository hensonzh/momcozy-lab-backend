# Release Smoke Checklist

Run this checklist after migrations and before a release is declared healthy.

## Infrastructure

- `make backend-productization-status` returns all pass.
- `make backend-smoke` passes in the release candidate checkout.
- `GET /v1/health/live` returns `200`.
- `GET /v1/health/ready` returns `200` and checks DB/Redis in production.
- `GET /v1/health/metrics` returns request, worker, agent tool, and SDK metric
  buckets; production calls include `X-Service-Key`.
- Alembic head matches the expected release revision.
- Browser/admin origins configured through `CORS_ALLOWED_ORIGINS` receive CORS
  headers; unexpected origins do not.
- Hosts configured through `TRUSTED_HOSTS` are accepted; unexpected `Host`
  headers are rejected.
- Responses include `X-Content-Type-Options`, `Referrer-Policy`, and
  `X-Frame-Options`; production responses include HSTS.
- When `RATE_LIMIT_ENABLED=true`, repeated non-exempt requests return a stable
  `rate_limited` error envelope and health checks remain exempt.

## Auth

- `POST /v1/auth/signup` returns an access token and refresh token.
- `POST /v1/auth/login` returns the same token contract.
- `POST /v1/auth/invite-login` accepts a configured invite code and returns the
  same token contract without requiring email registration.
- Reusing the same invite code from a different `device_id` returns
  `permission_denied`.
- Create an invite code through `POST /v1/admin/invite-codes` using
  `X-Service-Key`.
- Disable the invite code through
  `POST /v1/admin/invite-codes/{code}/disable`; subsequent invite-login attempts
  with that code return `permission_denied`.
- `POST /v1/auth/refresh` rotates the refresh token.
- `POST /v1/auth/logout` revokes the current device session.
- Missing or invalid bearer token returns the stable error envelope.

## Core APIs

- Upload a small file through `POST /v1/files/upload`.
- Oversized file upload returns `payload_too_large` and does not create file
  metadata.
- Create and list a feeding record.
- Create and list a plan.
- Create and read a diary entry.
- Upsert and list a pump device.
- Create a support ticket.
- Retry one create request with the same `Idempotency-Key` and confirm replay or
  stable conflict behavior.

## Agent Runtime

- Create an agent thread.
- Create an agent run.
- Replay run events through `GET /v1/agent/runs/{run_id}/events`.
- Open SSE replay through `GET /v1/agent/runs/{run_id}/stream` using bearer
  auth headers, not query tokens.
- Confirm/reject a test action if one is available.
- Run deterministic agent seed eval:
  `python production_backend/scripts/run_agent_seed_eval.py`.
- Run replay eval for any incident-derived bundle that is part of the release:
  `python production_backend/scripts/run_agent_replay_eval.py --replay <bundle.json> --suite <suite> --name <case-name>`.

## Observability

- Confirm logs include `request_id`, route, status code, and latency.
- Confirm `/v1/health/metrics` changes after smoke traffic using `X-Service-Key`
  in production.
- Run `make backend-worker-backlog BACKEND_ENV_FILE=<env file>` and confirm no
  unexpected queued, locked, stale running, or dead-letter growth.
- Confirm no raw password, refresh token, service key, prompt, tool args, or file
  body appears in logs.

## Exit Criteria

- No 5xx spike.
- No readiness failure.
- No new dead-letter worker jobs.
- No unrecovered stale agent runs.
- No cross-user data access finding.
- OpenAPI snapshot matches the deployed schema.
