# Product Backend Release Smoke Checklist

Run this checklist after migrations and before declaring the Product Backend
healthy. Runtime conversation, model, orchestration, and eval checks belong to
the Agent Runtime release.

## Infrastructure

- `make backend-productization-status` passes.
- `make backend-staging-config` passes against the shared test infrastructure.
- `make backend-smoke` passes.
- `GET /v1/health/live` returns `200`.
- `GET /v1/health/ready` returns `200` and checks the target PostgreSQL/Redis.
- `GET /v1/health/metrics` returns Product Backend request/dependency metrics;
  deployment checks include the operator `X-Service-Key`.
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

## Core Product Backend APIs

- Confirm authenticated `GET /v1/onboarding/me` returns owner-scoped state and
  `Cache-Control: private, no-store`; unauthenticated calls return `401`.
- For an isolated first-delivery account, `PUT /v1/onboarding/me/profile` with
  a required delivery date and current cesarean persists *prior* cesarean
  history as false. A second delivery with current cesarean and no prior
  cesarean must also persist false.
- Repeat the same onboarding `PUT`: return the original primary infant ID
  without adding baby records. A different payload after confirmation returns
  `409`. Existing current-delivery babies cannot be silently duplicated.
- Verify `GET` returns completed state only for that account and the maternal
  context reports the same prior-history meaning. Migration
  `20260926_0022` must set legacy ambiguous values to unknown, not false.
- Do not enable `MOMCOZY_ENABLE_ONBOARDING` until the public OpenAPI, App
  runtime, and actual auth-backed flow are verified. Keep release-reset disabled:
  `/v1/onboarding/me/release-reset` is not implemented by this Backend.
- Upload a small file through `POST /v1/files/upload`.
- Oversized upload returns `payload_too_large` without object metadata or bytes.
- Create and list feeding and pumping records.
- Create and list a plan and task.
- Verify removed diary and Runtime business-action routes return 404.
- Upsert and list a pump device.
- Create a support ticket.
- Retry one write with the same `Idempotency-Key` and verify replay or the
  documented stable conflict.

## Internal Agent Runtime API Boundary

- Product Backend Compose does not start an Agent Runtime worker; Agent Runtime is deployed
  independently.
- `AGENT_RUNTIME_SERVICE_API_KEY` is installed only in Product and Runtime
  service secrets.
- Every `/v1/internal/agent/*` route rejects a missing, invalid, or operator
  service key.
- A valid Runtime service key can read basic profile context only for the supplied `actor_user_id`.
- Cross-user file resolve is rejected; owner-scoped file resolve returns a
  stable opaque Product capability URL and stable Product `file_id`.
- Resolve the same active file twice with a time gap: `model_url` remains
  identical while `expires_at` slides forward by the authorized activity.
  Fetching the capability directly must not extend Redis TTL.
- Delete the file and verify both a later internal resolve and the old public
  capability fetch are rejected; application logs must not contain its token.
## Observability

- Product logs include `request_id`, route, status, and latency.
- Product metrics change after smoke traffic.
- Postgres, Redis, and object-storage errors are visible without leaking
  credentials or health content.
- No password, refresh token, service key, JWT private key, signed URL,
  model-asset capability token, request body, or uploaded file body appears in
  logs.

## Exit Criteria

- No 5xx spike or readiness failure.
- No cross-user data access finding.
- No duplicate Product write after idempotent replay.
- JWKS and Runtime service authentication pass.
- OpenAPI snapshot matches the deployed Product Backend schema.
- Runtime-owned smoke checks pass separately before end-to-end Agent traffic is
  enabled.
