# Flutter Product API Compatibility

Flutter Product repositories treat `docs/openapi.generated.json` as the Product
Backend source of truth. Agent conversation and streaming APIs are generated
from the independently deployed Agent Runtime contract, not this repository.

## Product Sources

- Schema: `docs/openapi.generated.json`
- Handoff: `docs/api-contract-handoff.md`
- Smoke flows: `docs/flutter-smoke-flows.json`

Flutter must consume only operations marked `public_app_api`. It must not call
`internal_service_api`, `admin_ops_api`, or `infra_probe_api` operations.

## Regeneration Rule

Regenerate or validate the Product typed client whenever a change affects:

- request or response schemas
- path or method names
- authentication or session tokens
- the stable error envelope
- `Idempotency-Key` behavior
- file-upload multipart fields
- voice or vision contracts

CI must fail when the committed Product OpenAPI snapshot drifts from the
application schema.

## Compatibility Policy

Backward-compatible changes include adding optional fields with safe defaults
and adding new endpoints. Adding enum values is compatible only when deployed
clients already handle unknown values.

Removing or renaming fields, changing types or requiredness, changing
paths/methods, putting tokens in URLs, or changing the error envelope is
breaking. Breaking changes require a coordinated app release, compatibility
window, or API version.

## Mobile Client Requirements

- Send user auth through `Authorization: Bearer`.
- Never put access tokens, refresh tokens, or service keys in URLs.
- Send refresh tokens only to `POST /v1/auth/refresh`.
- Use `Idempotency-Key` for retryable Product writes and preserve it across
  retries.
- Treat `request_id` from the error envelope as the Product support/debug ID.
- Never send `X-Service-Key` or call `/v1/internal/agent/*` from Flutter.
- Keep the Product and Runtime base URLs and generated clients separate.
- Keep Schedule task state authoritative through Product plan/task APIs and
  refetch after writes.
- A pumping or feeding record created for a task must send the stable
  `plan_task_id` and reuse its idempotency key on retry.
- Update pregnancy-card todo completion only through
  `/v1/plans/{plan_id}/todos/{item_id}/completion`, sending the last observed
  plan `version` as `expected_version`; reload on `version_conflict`.
- Treat `voice_provider_disabled` and `vision_provider_disabled` as stable
  unavailable states. Do not fall back to retired endpoints.
- Treat Product `file_id` as attachment identity. Signed object URLs are
  temporary transport values and must not become cache keys or durable IDs.

## Agent UI Boundary

The Flutter Agent base URL points to the independently deployed Agent Runtime,
not the Product API. The Agent UI authenticates there with the same
Product-issued access token. Product publishes the verification key through
`GET /.well-known/jwks.json`, and the token includes the Runtime audience.

Conversation threads, runs, stream replay, confirmation UI, reducer event names,
and reconnect cursors are Runtime contracts. Their schemas, smoke fixtures, and
compatibility policy must be sourced from the Runtime repository. Product
OpenAPI intentionally exposes none of those routes.

## Release Checklist

1. Export Product OpenAPI.
2. Regenerate or validate the Product typed client.
3. Run `docs/flutter-smoke-flows.json` against staging.
4. Verify no credential appears in URLs or crash logs.
5. Verify retryable Product writes preserve idempotency keys.
6. Verify Flutter has no dependency on `/v1/internal/agent/*`.
7. Validate the separately generated Runtime client against the deployed
   Runtime contract.
8. Record Product schema, Runtime schema, and Flutter build versions in the
   release note.
