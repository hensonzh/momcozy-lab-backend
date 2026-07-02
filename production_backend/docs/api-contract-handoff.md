# Production Backend API Contract Handoff

This handoff is the human-readable companion to
`production_backend/docs/openapi.generated.json`.

## Contract Sources

- OpenAPI snapshot: `production_backend/docs/openapi.generated.json`
- Export command: `python production_backend/scripts/export_openapi.py`
- Runtime base path: `/v1`
- Error model: stable `{ "error": { "code", "message", "request_id", "details?" } }`

## Auth

- `POST /v1/auth/signup`
- `POST /v1/auth/login`
- `POST /v1/auth/refresh`
- `POST /v1/auth/logout`

Clients use `Authorization: Bearer <access_token>` for user-facing APIs.
Refresh tokens are opaque and only sent in request bodies to `/auth/refresh`.
Service-to-service callers use `X-Service-Key`; this is not a user token and
must not be used by mobile clients.

## Idempotency

Retryable writes accept `Idempotency-Key` as a header. The backend normalizes
blank values to `null`, trims whitespace, and rejects values over 255
characters.

Current retryable write surfaces include files, profile infants, records,
plans/tasks, device telemetry, notifications, support tickets, agent run
creation, and agent action confirmation.

## Owner Scope

User-owned APIs derive owner scope from the access token. Mobile clients should
not send `user_id` as an authority. Service-created notifications are the only
current public route that accepts a target `owner_user_id`, and it requires
`X-Service-Key`.

## Agent Streaming

- Replay page: `GET /v1/agent/runs/{run_id}/events`
- SSE replay stream: `GET /v1/agent/runs/{run_id}/stream`

Stream URLs do not accept access tokens as query parameters. Clients must send
the bearer token in headers. Events are application-level runtime events, not
provider raw events.

The SSE stream replays persisted events by default. Clients that need live
consumption can pass `follow=true` with bounded `poll_interval_seconds` and
`max_wait_seconds`; the backend still emits only persisted application events
and exits when a terminal run event is observed or the wait budget expires.

## Agent Action Events

Action events use `action_id` as the reducer key. `action.confirmation_required`
includes user-visible `preview_payload` plus action metadata:
`action_type`, `action_status`, `target_type`, `target_id`, and
`side_effect_level`.

`action.queued` and `action.rejected` include `action_status`, `action_type`,
`target_type`, and `target_id` so clients can merge replayed events into the
same action card. `apply_payload` is never streamed; it is only persisted inside
the server-side action/outbox apply path.

## Files

File upload uses multipart form data at `POST /v1/files/upload`. File metadata
is owner-scoped and object bytes are stored through the configured object
storage provider.

## Flutter Integration Rule

Flutter repositories should be generated from or validated against the OpenAPI
snapshot. Do not build new client code against legacy raw response shapes.

Use `production_backend/docs/flutter-smoke-flows.json` as the initial integration
smoke fixture for auth, core records/plans/files, and agent replay.
Use `production_backend/docs/flutter-client-compatibility.md` for generated
client regeneration and breaking-change rules.
