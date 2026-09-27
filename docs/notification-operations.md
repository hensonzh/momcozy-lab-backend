# Notification module: local operation and provider handoff

> **Current repository notice (2026-09-26):** The appointment/consultation
> event projection described below is historical design documentation; its
> producer modules are absent from the current production source. The currently
> implemented trigger is Agent reply completion. See
> [agent-reply-push-rollout.md](agent-reply-push-rollout.md) for its code path and
> remaining FCM/APNs acceptance work.

The current acceptance scope is local implementation and verification. Firebase,
FCM service account and Apple APNs configuration were not prepared as of
2026-09-10. No real push was sent and no cloud deployment was performed.

## Data and delivery

`care_service_events` (committed business events) → `notification-worker` →
`notifications` (inbox and durable task) → `notification_deliveries` per eligible
`push_installations` binding → FCM HTTP v1 → Android / APNs → device.

The worker also projects events when `PUSH_PROVIDER=disabled`, keeping the inbox
usable without a provider. Apply migration `20260910_0018` before running the new
API or worker. It preserves existing inbox/read state and seeds receipts for
historical business events so they cannot become a launch-time push backlog.

Tables added: `push_installations`, `notification_deliveries`,
`notification_preferences`, `notification_event_receipts`. The existing
`notifications` table receives task, route, resource, expiry and deduplication
columns. Read status (`unread`, `read`, `archived`) is independent of send status
(`in_app`, `disabled`, `pending`, `scheduled`, `sent`, `failed`, `expired`,
`canceled`). `sent_at` means provider acceptance; `delivered_at` is not fabricated.

Workers serialize each owner's projection and delivery with PostgreSQL row locks,
use `SKIP LOCKED` for other owners, and commit receipts and tasks together.
Business, registration, notification mutations and sender use the same
User → session/resource/notification lock order. Reconciliation uses pages of
100 records and checks live appointment/resource state before every send.
No memory-only timers hold the durable schedule.

Each logical notification has a unique idempotency key. Each device delivery is
unique by notification, installation and binding. Retries only target unsuccessful
devices; transient errors use bounded exponential backoff plus jitter, with at
most five attempts. FCM `UNREGISTERED` disables the affected token. Permission,
category, owner, session, resource state and expiry are rechecked on retries.
The transaction rolls back after a worker crash; expired work is never sent.
An external provider may have accepted a request immediately before a crash.
Thus delivery is at least once, not exactly once. Stable notification tags and
collapse IDs reduce duplicate presentation, but cannot prove delivery or reading.

## Local commands and configuration

Use the existing backend virtual environment and the local Compose environment.
`docker-compose.local.yml` includes `notification-worker`; its command is
`python -m app.workers.notifications`. The test Compose template also contains
this worker, but was not deployed during local acceptance.

Public examples are in `env/local.env.example` and
`env/staging.env.example`:

- `PUSH_PROVIDER=disabled` keeps provider delivery off.
- `PUSH_TOKEN_KEY` is a private random encryption key of at least 32 bytes.
  Preserve it across worker/API restarts. Changing it invalidates decryption of
  stored tokens and requires device re-registration; do not rotate it casually.
- `PUSH_FCM_PROJECT_ID` identifies the project; the provider validates its format.
- `PUSH_FCM_CREDENTIALS_FILE` points to the private service-account JSON file.
  Compose mounts `MOMCOZY_PUSH_SECRETS_DIR` read-only at `/run/momcozy-push`.
  An example in-container file path is `/run/momcozy-push/service-account.json`.
  Keep the file outside Git and Docker build context; restrict filesystem access.

API and worker must share the token encryption key. Only the worker needs access
to service-account credentials. Use a project-scoped Firebase Messaging sender
identity. No credential belongs in a Flutter build or chat message.

Flutter uses `MOMCOZY_FIREBASE_API_KEY`, `MOMCOZY_FIREBASE_APP_ID`,
`MOMCOZY_FIREBASE_SENDER_ID` and `MOMCOZY_FIREBASE_PROJECT_ID` as platform-specific
Dart defines (see `app/docs/notification-firebase-defines.example.json`). These identify the Firebase client app; configure Firebase app
restrictions. Do not substitute the server service-account private key. Missing
values result in an unavailable provider and do not trigger a permission prompt.

Register the exact Android flavor application ID and the iOS bundle ID with the
Firebase project. Native auto-init is off by default. Android uses the
`service_updates` channel; clearing service notifications leaves the independent
pump foreground notification intact. The selected Firebase SDK requires iOS 15 or newer. iOS enables `remote-notification` background
mode and uses `Runner/Runner.entitlements`; `APS_ENVIRONMENT` is development for
Debug and production for Release/Profile. APNs capabilities, signing profiles and
an APNs key/certificate uploaded to Firebase still require project-owner setup.

## Permission and account lifecycle

Startup only reads OS permission. Startup, resume, settings entry/return and every
reminder/preference operation refresh the OS state. First requested reminder:
not_determined → product explanation → explicit confirmation → OS prompt →
authorized/provisional → token registration → enable task. Denial does not create
an executable task, does not repeat the system prompt and offers system settings.
Appointment confirmation succeeds independently and shows its reminder as off.

When permission is revoked, the App syncs the new state; no new executable task
can pass its permission gate. When restored, only still-valid future reminders
that the user previously requested can resume. Immediate historical updates and
missed reminders are not backfilled. Preferences are separate service categories;
marketing is separate and off. English templates are centralized for future
locale variants.

The OS is authoritative on the device. A server cannot instantly observe settings
changed while an App is stopped. The OS enforces display permissions; the App
updates the backend at each checkpoint. Token acquisition and sync failures are
visible as unavailable/pending and never displayed as successful task creation.

An installation has a secure persistent random ID and secret, monotonic client
revision, encrypted token, token digest, account/session binding generation and
last seen time. Token rotation replaces the token; reinstall can create a new
installation and transfers a duplicate token away from its previous record.
Multiple installations per account are supported. Logout clears local inbox and
system alerts, pauses token auto-init, detaches the binding and revokes the
account session using existing authentication. The device token is retained for
later login. Revoked/inactive sessions and stale installations are excluded from
sending. Generation checks prevent late requests from repopulating another
account's UI. Previously accepted provider messages cannot be recalled; their
lock-screen text and payload remain generic and account data requires login.

## API and App surfaces

All routes are under `/v1/notifications`, use the authenticated account, return
`Cache-Control: private, no-store`, and reject a client-supplied target user ID.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Cursor page, descending creation time/ID, server unread count |
| POST | `/read-all` | Read all currently visible, owned inbox entries |
| PATCH | `/{id}/read` | Set one owned notification's read state |
| DELETE | `/{id}` | Archive an owned notification |
| POST | `/{id}/open` | Revalidate resource ownership, mark read, return safe route |
| POST | `/installations` | Register/refresh permission, token and session binding |
| POST | `/installations/{id}/detach` | Detach current installation with its secret |
| GET | `/preferences` | Category preferences; marketing separate |
| PATCH | `/preferences/{category}` | Update a service category; enabling requires permission |
| GET, PUT | `/appointments/{id}/reminder` | Read/update an owned appointment reminder |

`/notifications` reuses the existing inbox with load-more, read-all, single read,
archive, loading/empty/error/retry states and an authoritative unread count.
More shows the unread count; iOS receives that count in the push badge and refreshes it when the inbox loads; Android
launcher badges follow active notifications. `/notifications/settings` shows
current permission and category controls. Booking includes a reminder opt-in;
confirmed appointments and `/services/appointments/{id}` show reminder state.
Existing intake, consultation room, summary/expert feedback and service progress
pages are reused as notification targets.

Push payloads carry only opaque `notification_id` and `binding_id`. A foreground
message displays a generic in-app banner only for the current binding. A click
uses the authenticated open API, never parses a title/body or trusts a payload
route. Cold and background clicks share this resolver; pending IDs persist in
secure storage across login. Missing resources get a friendly fallback; transient
failures retain pending intent for retry. Foreign notifications return 404.
An agent-conversation target additionally verifies ownership against the existing
Runtime conversation API before routing to that conversation.

| Notification | Committed business trigger | Target |
|---|---|---|
| appointment_created | Appointment confirmation | Appointment detail |
| appointment_reminder | Explicit opt-in, 15 minutes before appointment | Appointment detail/preparation |
| consultation_started | Expert starts consultation | Consultation room |
| consultation_ended | Completion, technical failure or no-show/safety end | Consultation summary |
| expert_feedback | Signed-plan publication | Published feedback in consultation summary |
| service_progress_updated | Paid service, session consumption, active-care transition or appointment cancellation | Service progress |

Changing appointment time replaces an unsent reminder; cancellation/start/removed
resource stops obsolete reminders. No generic external notification-creation
endpoint is exposed. Future event types should extend the same templates,
projection, resource authorization and regression suite.

## Operations and remaining provider acceptance

Worker transaction failures log a sanitized warning and retry; request bodies,
private tokens, provider authorization and health content are not logged. Inspect
counts grouped by `notifications.send_status`, overdue `trigger_at`,
`notification_deliveries.status/error_code`, and events without receipts to detect
backlog or permanent failures. Do not blindly reset sent/expired deliveries to
pending; reconcile resource validity and provider acceptance before an operator
replay. A dedicated metrics dashboard/alerts and bulk operator replay UI are
future operational additions, not implied by a successful local run.

Before real rollout, verify the configured Firebase project, Android flavor,
iOS bundle ID/APNs entitlement and signing, then perform device tests for first
consent, rejection, settings recovery, token refresh, killed-app click, account
switch, multi-device logout and generic lock-screen privacy. Verify connectivity,
quota/retry behavior and worker supervision in the target environment. Real FCM,
APNs delivery, distribution signing and cloud deployment remain unaccepted until
that work is actually executed.
