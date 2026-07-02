# Flutter Generated Client Compatibility

Flutter app code should treat the production backend OpenAPI snapshot as the
source of truth. The legacy raw response contracts are not compatibility
targets.

## Source Of Truth

- Schema: `production_backend/docs/openapi.generated.json`
- Handoff: `production_backend/docs/api-contract-handoff.md`
- Smoke flows: `production_backend/docs/flutter-smoke-flows.json`

## Regeneration Rule

Regenerate or validate Flutter API clients whenever a PR changes:

- request or response schemas
- path or method names
- auth/session token contracts
- error envelope shape
- `Idempotency-Key` usage
- agent event/action schemas
- file upload multipart fields

CI must fail if the OpenAPI snapshot drifts from the current backend schema.

## Compatibility Policy

Backward-compatible changes:

- adding optional response fields
- adding optional request fields with safe defaults
- adding new endpoints
- adding enum values only when clients already handle unknown values

Breaking changes:

- removing or renaming fields
- changing field types
- changing required fields
- changing path/method names
- moving tokens into URLs
- changing the error envelope
- changing agent event names or reducer keys

Breaking changes need a coordinated app release, compatibility window, or API
versioning plan.

## Mobile Client Requirements

- Send user auth through `Authorization` headers.
- Never put access tokens, refresh tokens, or service keys in URLs.
- Send refresh tokens only to `POST /v1/auth/refresh`.
- Use `Idempotency-Key` for retryable writes.
- Treat `request_id` from error envelopes as the support/debug ID.
- Consume agent stream events as application events, not provider raw events.
- Reconnect agent streams with `after_sequence`; do not replay by parsing text.

## Release Checklist

1. Export OpenAPI.
2. Regenerate or validate the Flutter typed client.
3. Run Flutter smoke flows against staging.
4. Verify no token appears in URLs or crash logs.
5. Verify retryable writes preserve idempotency keys across app retries.
6. Verify agent event reducers use stable IDs such as `run_id`, `event_id`,
   `message_id`, `tool_call_id`, and `action_id`.
7. Record backend schema version and Flutter build version in the release note.
