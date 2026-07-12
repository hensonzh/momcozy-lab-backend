# Production Backend API Contract Handoff

This handoff is the human-readable companion to
`production_backend/docs/openapi.generated.json`.

## Contract Sources

- OpenAPI snapshot: `production_backend/docs/openapi.generated.json`
- API surface catalog: `production_backend/docs/api-surface-catalog.md`
- Export command: `make backend-export-contracts`
- Runtime base path: `/v1`
- Error model: stable `{ "error": { "code", "message", "request_id", "details?" } }`

## API Surface Rules

Every OpenAPI operation carries MomCozy extension metadata:

- `x-momcozy-api-surface`: whether the route is app-facing, streaming,
  internal-service, admin/ops, infra-probe, or deprecated.
- `x-momcozy-owner`: owning backend module or team boundary.
- `x-momcozy-client`: intended callers.
- `x-momcozy-stability`: stability level for client coordination.

Flutter should only integrate routes marked `public_app_api` and the specific
`runtime_stream_api` routes needed for streaming UX. It must not depend on
`internal_service_api`, `admin_ops_api`, or `infra_probe_api` routes.

Update flow for API changes:

1. Add or update route metadata in the FastAPI router.
2. Export OpenAPI.
3. Regenerate `api-surface-catalog.md`.
4. Run contract tests.

## Auth

- `POST /v1/auth/invite-login`
- `POST /v1/auth/signup`
- `POST /v1/auth/login`
- `POST /v1/auth/refresh`
- `POST /v1/auth/logout`

Clients use `Authorization: Bearer <access_token>` for user-facing APIs.
Refresh tokens are opaque and only sent in request bodies to `/auth/refresh`.
Invite-login is a beta-access path: the mobile app sends a configured invite
code plus its stable device id. The first successful login binds that invite
code to the device id; subsequent logins must use the same device id and receive
the same access/refresh token pair contract as signup/login. A different device
using an already-bound invite code is rejected with `permission_denied`.
Service-to-service callers use `X-Service-Key`; this is not a user token and
must not be used by mobile clients.

## Admin Invite Codes

- Admin page: `GET /v1/admin/invite-codes/ui`
- Create: `POST /v1/admin/invite-codes`
- List: `GET /v1/admin/invite-codes`
- Disable: `POST /v1/admin/invite-codes/{code}/disable`

The lightweight admin page is for operators who need to create and disable beta
invite codes quickly. The page itself is a static HTML shell; all state-changing
and listing calls require `X-Service-Key` and are marked `admin_ops_api`.

Managed invite codes live in Postgres and take precedence over legacy
environment-configured invite codes. The first successful mobile invite login
binds the code to the app's stable `device_id` and backend user. Disabling the
code prevents future invite-login attempts, including attempts from the
previously bound device.

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

The confirmed `pregnancy.plan.create` apply path also emits the durable
application event `pregnancy_plan.changed` after the authoritative Plan and
action result are committed. Its payload is intentionally limited to
`operation=created`, opaque `plan_id`, `plan_type=pregnancy`,
`source=agent_action`, and the generic outbox-added `action_id`. Preview,
confirmation, rejection, and failed apply states do not emit this business
event, and it never contains the personalized card, plan context, or health
facts. Clients use it only as an invalidation/notification signal and reload the
owner-scoped resource through
`GET /v1/plans?plan_type=pregnancy&status=active`.

## Files

File upload uses multipart form data at `POST /v1/files/upload`. File metadata
is owner-scoped and object bytes are stored through the configured object
storage provider.

## Schedule And Plan Progress

Schedule resources are owner-scoped and never accept a mobile-provided
`user_id` as authority:

- `GET /v1/plans?plan_type=milk_management&status=active` loads plan context.
- `GET /v1/plans/tasks/list?task_date=YYYY-MM-DD` loads the selected day.
- `POST /v1/plans/tasks`, `PATCH/DELETE /v1/plans/tasks/{task_id}` implement
  task creation and editing.
- `PATCH /v1/plans/tasks/{task_id}/state` accepts the typed states `pending`,
  `completed`, and `skipped`.
- Pumping and feeding creates accept optional `plan_task_id`. When present,
  record creation and task completion happen in the same database transaction;
  the record create remains retry-safe through `Idempotency-Key`.

Pregnancy-card todos are not `PlanTask` rows. New pregnancy plan payloads
persist a stable `item_id` on every structured todo. Clients update one item via
`PATCH /v1/plans/{plan_id}/todos/{item_id}/completion` with
`{completed, expected_version}` and an `Idempotency-Key`. The response is the
complete authoritative `PlanRead` with an incremented `version`. A stale write
returns `version_conflict`; an old item without `item_id` remains read-only and
returns `todo_item_not_found`. Clients must never match todo items by title.

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

Voice playback follows the legacy Doubao/Volcengine realtime TTS provider.
Set `VOICE_PROVIDER=doubao`, `VOICE_API_KEY`, and the TTS resource/voice
settings from `env/compose.*.env.example`. The Flutter client calls
`GET /v1/realtime-voice-stream?text=...` with an Authorization header and plays
the returned PCM chunks through the native PCM player. `VOICE_PROVIDER=disabled`
keeps the stable `code=voice_provider_disabled` error contract, and
`VOICE_PROVIDER=local_stub` remains local/test-only.

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
