# Product Backend API Contract Handoff

This handoff is the human-readable companion to
`docs/openapi.generated.json`.

## Contract Sources

- OpenAPI snapshot: `docs/openapi.generated.json`
- API surface catalog: `docs/api-surface-catalog.md`
- Export command: `make backend-export-contracts`
- Product Backend API base path: `/v1`
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
Runtime contract, not Product Backend OpenAPI.

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
Access tokens are RS256-signed, carry both the Product Backend API and Agent Runtime
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

Product Backend API authentication also checks the active device session. The Agent
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
plans/tasks, device telemetry, notifications, and support tickets.

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

This service-only endpoint provides the current Run's basic profile context.
It requires the Runtime service key and explicit `actor_user_id` owner scope.

### Files

- `POST /v1/internal/agent/files/resolve`
- `GET /v1/model-assets/{token}`

The request carries `actor_user_id`, Product `file_id`, and purpose. Product
validates ownership and returns an opaque Product capability URL. Repeated
authorized resolves reuse the URL and slide its 30-minute inactivity TTL.
`file_id` remains the durable identity; the capability URL must not be
persisted as an identity.

The model-asset GET is intentionally unauthenticated because the high-entropy
path token is a bearer capability. It remains rate limited, does not renew its
own TTL, revalidates authoritative file state before proxying bytes, returns
`Cache-Control: private, no-store`, and uses the same opaque 404 for expired,
revoked, unknown, or drifted mappings. Product application logs redact the
path token.

Runtime business tools and Action endpoints have been removed. Mobile clients
continue using public Product APIs; the Runtime has no Product write adapter.

## Files

File upload uses multipart form data at `POST /v1/files/upload`. File metadata
is owner-scoped and object bytes are stored through the configured object
storage provider.

## Schedule Projection And Care Plan Progress

Schedule resources are owner-scoped and never accept a mobile-provided
`user_id` as authority. The mobile app reads one projection that combines
personal entries, appointments, episodes, and the latest published Care Plan:

- `GET /v1/schedule?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&timezone=...`
  returns the bounded calendar window and a `has_more` cursor hint.
- `POST /v1/schedule/personal` creates an idempotent personal entry.
- `PATCH /v1/schedule/personal/{task_id}` uses `expected_updated_at` for
  optimistic concurrency; `DELETE` uses the same timestamp in the query.
- `PUT /v1/care/plan-publications/{publication_id}/tasks/{source_key}` accepts
  the typed task state and `expected_version`, then returns the complete
  authoritative publication. A stale write returns `version_conflict` and the
  client reloads the schedule.

The old `/v1/plans` surface remains deprecated. New Flutter clients use
`/v1/schedule` and `/v1/care`. Prenatal plans, pregnancy cards and diaries have
been removed; Runtime business tools are unavailable.

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
