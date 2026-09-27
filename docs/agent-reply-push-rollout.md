# Agent reply notification handoff (Android and iOS)

This is the **current** executable notification trigger. The older
`notification-operations.md` describes an appointment/consultation event
projection that is not present in the current production modules. Do not use
that historical trigger matrix to claim those reminders work.

## Flow and guarantees

1. Agent Runtime commits a completed run and `run.completed` event.
2. Its run worker scans committed runs from the last 24 hours. Migration
   `20260926_0003` pre-marks *already completed* runs as delivered, so a release
   never replays the historical backlog. For each new completion the worker
   calls `POST /v1/internal/agent/notifications/reply-ready` using its existing
   `PRODUCT_BACKEND_SERVICE_KEY`. Failed/ambiguous handoffs retry from a durable
   `agent_notification_receipts` row with bounded backoff. A crash between
   backend acceptance and receipt commit can cause another call.
3. Product Backend authenticates the dedicated Agent Runtime service key,
   serializes by owner and deduplicates by run ID. The notification is an inbox
   entry. It enters `pending` **only if** FCM is configured, the `agent_updates`
   preference permits it, the completion is unexpired, and an eligible bound
   installation exists. Otherwise it stays `in_app`; enabling push later does
   not back-send it.
4. The existing `notification-worker` rechecks account/session/permission and
   sends one generic FCM/APNs alert per eligible binding. It never puts the
   assistant's answer in the lock-screen payload. A successful FCM response
   means provider acceptance, **not** device delivery. App click handling
   resolves the conversation through the authenticated notification-open API.

The internal event contract carries `run_id`, `owner_user_id`, `thread_id`, and
`completed_at`; no reply text or user-entered data is sent across this handoff.
Agent and Product Backend must use the matching service key. Each run is
idempotent; retries after `sent` also acknowledge the original notification.

## Local verification

Set `MOMCOZY_TEST_DATABASE_URL` and `MOMCOZY_AGENT_TEST_DATABASE_URL` to **isolated**
PostgreSQL test databases; the tests use temporary schemas and mock the provider.

```sh
cd backend
PYTHONPATH=. .venv/bin/pytest -q tests/test_agent_update_notification.py tests/test_push_provider.py tests/test_push_registration.py tests/test_openapi_contract.py
cd ../agent
PYTHONPATH=. .venv/bin/pytest -q tests/test_notification_dispatch.py tests/test_agent_run_worker.py tests/test_runtime_migration.py
```

Check `agent_notification_receipts` for retry backlog, `notifications` for
`send_status`, and `notification_deliveries` for provider attempts. Avoid logging
raw device tokens, service keys, assistant replies or provider payloads.

## Still required before real device acceptance

- Apply the Agent Runtime migration before starting the updated Agent worker;
  deploy Product Backend's new internal endpoint before Agent handoff. Do not
  run a new Agent worker against an old Product Backend API.
- Configure `PUSH_PROVIDER=fcm`, a persistent `PUSH_TOKEN_KEY`, Firebase project
  ID, and a private FCM service-account credential on Product Backend. Ensure
  API and notification worker share the token key. The current local backend
  environment has `PUSH_PROVIDER=disabled` and no FCM credentials, so this
  change **does not** turn on real push.
- Register the exact Android application IDs for each flavor and the iOS bundle
  ID in Firebase; provide the four platform-specific `MOMCOZY_FIREBASE_*` Dart
  defines to the App build. Configure the APNs signing capability and upload
  the APNs key/certificate to Firebase. Keep private keys out of Git.
- On Android **and** iOS physical devices: test permission grant/deny/recovery,
  token registration and rotation, completed Agent reply, pending task, worker
  FCM response, lock-screen notification, cold-start/click resolution, account
  switch/logout isolation, offline retry, and generic privacy-safe wording.
  This test has **not** been performed; do not report push as live until it passes.
