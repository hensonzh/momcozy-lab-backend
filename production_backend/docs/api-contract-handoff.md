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

AG-UI is not a production compatibility target. The legacy `/api/ag-ui`,
`/api/ag-ui-ws`, prewarm, and WebSocket bridge contracts are replaced by the
typed run/event APIs above. Flutter/Web clients should implement a MomCozy
application-event reducer instead of an AG-UI adapter.

The SSE stream replays persisted events by default. Clients that need live
consumption can pass `follow=true` with bounded `poll_interval_seconds` and
`max_wait_seconds`. Persisted events are the source of truth and carry a
monotonic `sequence` for replay.

In live follow mode, the backend may also emit transient `message.delta`
application events from Redis for token-level typing UI. These events are not
provider raw events, are not persisted to Postgres, and do not carry a
`sequence`; their SSE id is `delta:<redis-stream-id>` and their payload includes
`transient: true` plus a Redis `cursor`. Clients must treat them as provisional:
they can be replayed within the short Redis TTL or lost after disconnect.
The final assistant content is authoritative only after the persisted assistant
`message.completed` event is available; that event includes
`payload.message_id`, `payload.role=assistant`, and `payload.text`.

A client reducer is the deterministic function that folds an ordered event
stream into visible UI state:

```text
previous AgentChatState + AgentEvent -> next AgentChatState
```

It deduplicates by `event_id` or `sequence`, merges message updates by
`message_id`, tool updates by `tool_call_id`, artifacts by `artifact_id`, and
action cards by `action_id`. Transient `message.delta` events should update only
the provisional streaming buffer and must be replaced by the persisted assistant
`message.completed` payload. The reducer must not infer state from
natural-language assistant text, provider raw events, or legacy AG-UI event
names.

## Agent Action Events

Action events use `action_id` as the reducer key. `action.confirmation_required`
includes user-visible `preview_payload` plus action metadata:
`action_type`, `action_status`, `target_type`, `target_id`, and
`side_effect_level`.

`action.queued`, `action.applied`, `action.failed`, and `action.rejected`
include `action_status`, `action_type`, `target_type`, and `target_id` so
clients can merge replayed events into the same action card. `apply_payload` is
never streamed; it is only persisted inside the server-side action/outbox apply
path.

Action API responses likewise expose preview/status metadata only. They do not
return server-side `apply_payload` or action idempotency keys.

## Files

File upload uses multipart form data at `POST /v1/files/upload`. File metadata
is owner-scoped and object bytes are stored through the configured object
storage provider.

## Product Assets

Legacy `/skill-assets/...` and `/images/Air_img/...` paths are retired. Product
assets are served through `GET /v1/assets` and `GET /v1/assets/{asset_id}` using
allowlisted manifest asset ids. Asset bytes live in the configured object
storage provider; clients never construct URLs from local skill directory names,
filesystem paths, or object-storage keys.

## Voice

- Transcription: `POST /v1/speech/transcribe-chunk`
- PCM playback: `GET /v1/realtime-voice-stream?text=...`
- Realtime session: `WEBSOCKET /v1/realtime-voice-session`

HTTP voice endpoints require `Authorization: Bearer <access_token>` and never
accept tokens in URLs. The realtime WebSocket also authenticates through the
`Authorization` header.

`VOICE_PROVIDER=disabled` is the default stable production contract until a
managed speech provider is configured. HTTP endpoints return the standard error
envelope with `code=voice_provider_disabled` and status `503`. The WebSocket
accepts authenticated clients, sends an `error` frame with the same code, and
then closes. `VOICE_PROVIDER=local_stub` exists only for local/test contract
checks and is rejected in production startup validation.

`VISION_PROVIDER=disabled` is the default stable production contract until a
managed image analysis provider is configured. The production replacement for
the legacy vision WebSocket is `GET /v1/files/{file_id}/vision/events/stream`;
it requires bearer auth, verifies file ownership through current user scope, and
never accepts tokens in URLs. Disabled provider responses use the standard error
envelope with `code=vision_provider_disabled` and status `503`.

## Flutter Integration Rule

Flutter repositories should be generated from or validated against the OpenAPI
snapshot. Do not build new client code against legacy raw response shapes.

Use `production_backend/docs/flutter-smoke-flows.json` as the initial integration
smoke fixture for auth, core records/plans/files, agent replay, and voice
contract checks.
Use `production_backend/docs/flutter-client-compatibility.md` for generated
client regeneration and breaking-change rules.
