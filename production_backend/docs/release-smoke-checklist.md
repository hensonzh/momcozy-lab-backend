# Release Smoke Checklist

Run this checklist after migrations and before a release is declared healthy.

## Infrastructure

- `GET /v1/health/live` returns `200`.
- `GET /v1/health/ready` returns `200` and checks DB/Redis in production.
- `GET /v1/health/metrics` returns request, worker, agent tool, and SDK metric
  buckets.
- Alembic head matches the expected release revision.

## Auth

- `POST /v1/auth/signup` returns an access token and refresh token.
- `POST /v1/auth/login` returns the same token contract.
- `POST /v1/auth/refresh` rotates the refresh token.
- `POST /v1/auth/logout` revokes the current device session.
- Missing or invalid bearer token returns the stable error envelope.

## Core APIs

- Upload a small file through `POST /v1/files/upload`.
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

## Observability

- Confirm logs include `request_id`, route, status code, and latency.
- Confirm `/v1/health/metrics` changes after smoke traffic.
- Confirm no raw password, refresh token, service key, prompt, tool args, or file
  body appears in logs.

## Exit Criteria

- No 5xx spike.
- No readiness failure.
- No new dead-letter worker jobs.
- No cross-user data access finding.
- OpenAPI snapshot matches the deployed schema.
