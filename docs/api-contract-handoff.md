# Production Backend API Contract Handoff

This handoff is the human-readable companion to
`docs/openapi.generated.json`.

## Contract Sources

- OpenAPI snapshot: `docs/openapi.generated.json`
- API surface catalog: `docs/api-surface-catalog.md`
- Export command: `make backend-export-contracts`
- Product API base path: `/v1`
- Error model: stable `{ "error": { "code", "message", "request_id", "details?" } }`

## API Surface Rules

Every OpenAPI operation carries MomCozy extension metadata:

- `x-momcozy-api-surface`: whether the route is app-facing, streaming,
  internal-service, admin/ops, infra-probe, or deprecated.
- `x-momcozy-owner`: owning backend module or team boundary.
- `x-momcozy-client`: intended callers.
- `x-momcozy-stability`: stability level for client coordination.

Flutter should integrate only routes marked `public_app_api`. It must not
depend on `internal_service_api`, `admin_ops_api`, or `infra_probe_api` routes.
Agent conversation and stream routes are published by the separate Agent
Runtime contract, not Product OpenAPI.

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
Access tokens are RS256-signed, carry both the Product API and Agent Runtime
audiences, and expose their public signing key through
`GET /.well-known/jwks.json`. The Product Backend is the only private-key owner;
the Agent Runtime validates the same opaque client token from its local JWKS
cache and never receives the private key.
Invite-login is a beta-access path: the mobile app sends a configured invite
code plus its stable device id. The first successful login binds that invite
code to the device id; subsequent logins must use the same device id and receive
the same access/refresh token pair contract as signup/login. A different device
using an already-bound invite code is rejected with `permission_denied`.
Service-to-service callers use `X-Service-Key`; this is not a user token and
must not be used by mobile clients.

Product API authentication also checks the active device session. The Agent
Runtime does not call the Product Backend on every request, so logout or session
revocation reaches it no later than the 15-minute access-token expiry.

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
plans/tasks, device telemetry, notifications, support tickets, and the internal
Agent action-apply endpoints.

## Owner Scope

User-owned APIs derive owner scope from the access token. Mobile clients should
not send `user_id` as an authority. Service-created notifications are the only
current public route that accepts a target `owner_user_id`, and it requires
`X-Service-Key`.

## Agent Runtime Internal API

Agent Runtime is a separate service. It does not import Product modules or read
Product tables. It calls the following Product-owned internal endpoints:

### Read

- `GET /v1/internal/agent/profile`
- `GET /v1/internal/agent/lactation/milk-analysis-snapshot`
- `GET /v1/internal/agent/plans/current`
- `GET /v1/internal/agent/plans/calendar`
- `GET /v1/internal/agent/plans/{plan_id}`
- `GET /v1/internal/agent/schedule-timeline`
- `GET /v1/internal/agent/diary`

Read calls require `X-Service-Key: <runtime-service-key>` and an explicit
`actor_user_id`. Product applies owner scope before returning a bounded domain
projection.

### Files

- `POST /v1/internal/agent/files/resolve`

The request carries `actor_user_id`, Product `file_id`, and purpose. Product
validates ownership and returns a bounded signed URL. `file_id` remains the
durable identity; the signed URL must not be persisted as an identity or used
as a cache key.

### Business Actions

- `POST /v1/internal/agent/actions/profile.update/apply`
- `POST /v1/internal/agent/actions/lactation.record/apply`
- `POST /v1/internal/agent/actions/plans/apply`
- `POST /v1/internal/agent/actions/diary.entry/apply`

Action requests carry `actor_user_id`, `action_id`, Runtime correlation
metadata, and a bounded Product payload. Every request requires:

```text
X-Service-Key: <runtime-service-key>
Idempotency-Key: agent-action:<action_id>
```

Product validates owner scope and domain invariants, commits the business
mutation, records `actor_service=agent-runtime` audit metadata, and returns an
explicit Product result. Replaying the same action key must not duplicate the
business write.

The Runtime owns tool selection, proposal, confirmation, conversation events,
and final response. The Product Backend owns business authorization, action
application, idempotency, audit, and Product data. Mobile clients never call
these internal endpoints.

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

`VISION_PROVIDER=disabled` remains the default stable production contract. The
production replacement for the legacy vision WebSocket is
`GET /v1/files/{file_id}/vision/events/stream`; it requires bearer auth, resolves
the file through current-user owner scope, and never accepts tokens in URLs.
Disabled provider responses use the standard error envelope with
`code=vision_provider_disabled` and status `503`.

The default `purpose=general` keeps the existing `vision.event` summary. For an
editable Schedule screenshot preview, call the same endpoint with
`purpose=schedule`. It emits `vision.started`, zero to 32
`vision.schedule_task.preview` events, then `vision.completed`. Each task preview
payload contains `purpose=schedule`, `time` as strict 24-hour `HH:mm`, `event` as
the editable task title (1-80 characters), and `event_type` as `pump`,
`breastfeed`, or `custom`. `vision.completed.payload.event_count` is the number
of preview tasks. The endpoint is read-only: it does not create or update a
Plan, PlanTask, feeding, or pumping record.

`VISION_PROVIDER=openai` enables the Responses structured-output adapter using
the existing `OPENAI_API_KEY`; provider requests use `store=false`, a bounded
output schema, no original filename, no bearer token, and a hard timeout. See
`vision-provider-integration.md` for stable error codes and rollout gates.

## Flutter Integration Rule

Flutter repositories should be generated from or validated against the OpenAPI
snapshot. Do not build new client code against legacy raw response shapes.

Use `docs/flutter-smoke-flows.json` as the initial integration
smoke fixture for auth, core records/plans/files, and voice contract checks.
Use `docs/flutter-client-compatibility.md` for generated
client regeneration and breaking-change rules.
