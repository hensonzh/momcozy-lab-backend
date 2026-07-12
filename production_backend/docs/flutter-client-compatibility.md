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
- voice/transcription endpoint contracts and `VOICE_PROVIDER` behavior

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
- Do not add an AG-UI compatibility adapter; legacy AG-UI event names are not
  part of the production backend contract.
- Treat transient `message.delta` events as provisional typing UI and replace
  them with assistant `message.completed.payload.text`.
- Reconnect agent streams with `after_sequence`; do not replay by parsing text.
- Persist a minimal, user-scoped Agent Hub snapshot for app restart recovery:
  `thread_id`, `run_id`, `last_sequence`, rendered event/action state, draft
  composer state, and the active request. This snapshot is only a UI recovery
  aid; backend run/message/action ledgers remain authoritative.
- Merge action events by `action_id`; render `preview_payload` from
  `action.confirmation_required` and never expect `apply_payload` in streams.
- After confirming or rejecting an action, continue following the same run with
  `/v1/agent/runs/{run_id}/stream?after_sequence=<last_sequence>&follow=true`
  so `action.applied`, `action.failed`, `action.rejected`, and final message
  events replace local pending states.
- Treat durable `pregnancy_plan.changed` as a privacy-safe notification and
  cache-invalidation signal only. Deduplicate it by `event_id`, then load the
  current user's authoritative Plan from
  `/v1/plans?plan_type=pregnancy&status=active`; do not derive or cache the
  personalized plan from the event payload.
- Keep Schedule task state authoritative: use `/plans/tasks/{task_id}/state`
  for `pending/completed/skipped`, and refetch the selected day after writes.
  A pumping or feeding record created for task completion must send the stable
  `plan_task_id` and reuse its `Idempotency-Key` on retry.
- Update pregnancy-card todo completion only through
  `/plans/{plan_id}/todos/{item_id}/completion`, sending the last observed plan
  `version` as `expected_version`. Replace local plan state with the returned
  `PlanRead`; on `version_conflict`, reload before retrying. Never fall back to
  title matching when `item_id` is absent.
- Treat `voice_provider_disabled` as a stable unavailable-state response for
  voice UI; do not fall back to legacy realtime voice endpoints.

## Release Checklist

1. Export OpenAPI.
2. Regenerate or validate the Flutter typed client.
3. Run Flutter smoke flows against staging.
4. Verify no token appears in URLs or crash logs.
5. Verify retryable writes preserve idempotency keys across app retries.
6. Verify agent event reducers use stable IDs such as `run_id`, `event_id`,
   `message_id`, `tool_call_id`, and `action_id`.
7. Verify voice UI handles `voice_provider_disabled` without token URLs.
8. Verify `waiting_for_confirmation` can recover after app restart and action
   confirmation resumes the original run stream.
9. Record backend schema version and Flutter build version in the release note.
