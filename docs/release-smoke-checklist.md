# Product Backend Release Smoke Checklist

Run this checklist after migrations and before declaring the Product Backend
healthy. Runtime conversation, model, orchestration, and eval checks belong to
the Agent Runtime release.

## Infrastructure

- `make backend-productization-status` passes.
- `make backend-test-smoke` passes in the isolated server-test profile.
- `make backend-prod-readiness` passes with production-managed dependencies.
- `make backend-smoke` passes.
- `GET /v1/health/live` returns `200`.
- `GET /v1/health/ready` returns `200` and checks Postgres/Redis in production.
- `GET /v1/health/metrics` returns Product request/dependency metrics;
  production calls include the operator `X-Service-Key`.
- Alembic head matches the expected release revision.
- CORS, trusted-host, security-header, rate-limit, and upload-limit checks pass.
- Object-storage write/read/delete and Product asset checks pass.

## Authentication

- `GET /.well-known/jwks.json` returns the active RS256 public key and no private
  RSA fields.
- `POST /v1/auth/signup`, `POST /v1/auth/login`,
  `POST /v1/auth/invite-login`, refresh rotation, and logout pass.
- Create an invite through `POST /v1/admin/invite-codes` with the operator
  service key.
- Disable it through `POST /v1/admin/invite-codes/{code}/disable` and verify a
  later invite-login is rejected.
- Access tokens contain the configured Product and Runtime audiences.
- Product rejects a token without the Product audience.
- Runtime independently validates the same token against Product JWKS and its
  configured audience.
- Missing or invalid bearer credentials return the stable error envelope.
- Operator `SERVICE_API_KEY` and `AGENT_RUNTIME_SERVICE_API_KEY` are distinct
  and cannot be substituted for each other.

## Core Product APIs

- Upload a small file through `POST /v1/files/upload`.
- Oversized upload returns `payload_too_large` without object metadata or bytes.
- Create and list feeding and pumping records.
- Create and list a plan and task.
- Create and read a diary entry.
- Upsert and list a pump device.
- Create a support ticket.
- Retry one write with the same `Idempotency-Key` and verify replay or the
  documented stable conflict.

## Internal Agent API Boundary

- Product Compose does not start an Agent worker; Agent Runtime is deployed
  independently.
- `AGENT_RUNTIME_SERVICE_API_KEY` is installed only in Product and Runtime
  service secrets.
- Every `/v1/internal/agent/*` route rejects a missing, invalid, or operator
  service key.
- A valid Runtime service key can read profile, lactation, plan, and diary
  context only for the supplied `actor_user_id`.
- Cross-user file resolve is rejected; owner-scoped file resolve returns a
  bounded signed URL and stable Product `file_id`.
- Each action apply endpoint rejects a missing idempotency key.
- Each action apply endpoint requires
  `Idempotency-Key: agent-action:<action_id>`.
- Repeating an applied action with the same key returns the same Product result
  without duplicating the business record.
- Audit records identify `actor_user_id`, `actor_service=agent-runtime`,
  `action_id`, and `request_id` without recording secrets or file bodies.

## Observability

- Product logs include `request_id`, route, status, and latency.
- Product metrics change after smoke traffic.
- Postgres, Redis, and object-storage errors are visible without leaking
  credentials or health content.
- No password, refresh token, service key, JWT private key, signed URL, request
  body, or uploaded file body appears in logs.

## Exit Criteria

- No 5xx spike or readiness failure.
- No cross-user data access finding.
- No duplicate Product write after idempotent replay.
- JWKS and Runtime service authentication pass.
- OpenAPI snapshot matches the deployed Product schema.
- Runtime-owned smoke checks pass separately before end-to-end Agent traffic is
  enabled.
